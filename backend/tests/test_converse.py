"""Patient Curiosity Loop server harness: sessions, turns, Stop, receipts.

The store invariants pinned here (execution plan S3/S4):

- one persistent ``TextSession`` per browser session, so repair state and
  follow-up history survive across HTTP turns;
- temporary audio is deleted on success, failure, and cancellation alike;
- Stop invalidates the generation: a turn finishing under a stale
  generation is discarded, transient prompts are dismissed, and the next
  turn cannot inherit a cancelled generation's choices — 100 repeated
  stop-vs-response races produce zero stale results;
- turn responses expose position/label choices and label/url/freshness
  sources only — capture internals never leave the server.

Everything uses fake transcribers/brains/clocks; no network, no audio deps.
"""

from __future__ import annotations

import base64
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.brain.adapter import BrainReply, Source
from app.main import app
from app.parker import converse_router
from app.parker.converse import (
    MAX_CONVERSE_AUDIO_BYTES,
    MAX_SESSIONS,
    SESSION_TTL_SECONDS,
    ConverseError,
    ConverseStore,
    TimedBrain,
)
from app.parker.screen import get_screen_state
from app.db.models import CapturedIntent, StagedAction

client = TestClient(app)

WAV_BYTES = b"RIFF\x00\x00\x00\x00WAVEfmt not-real-audio"
WAV_B64 = base64.b64encode(WAV_BYTES).decode("ascii")


class EchoBrain:
    def __init__(self, speech="echo answer", sources=()):
        self.speech = speech
        self.sources = tuple(sources)
        self.calls = 0

    def respond(self, history, utterance, context):
        self.calls += 1
        return BrainReply(speech=self.speech, sources=self.sources)


class GateBrain:
    """A brain that blocks until released — deterministic slow provider."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def respond(self, history, utterance, context):
        self.entered.set()
        assert self.release.wait(timeout=5), "GateBrain was never released"
        return BrainReply(speech="late answer that must never surface")


class FakeTranscriber:
    def __init__(self, lines=None):
        self.lines = ["Remind me to water the plants"] if lines is None else lines
        self.seen_paths = []

    def __call__(self, path):
        assert path.is_file()
        self.seen_paths.append(path)
        return list(self.lines)


def make_store(db, *, brain=None, transcriber=None, receipts=None, clock=None, loader=None):
    factory = sessionmaker(bind=db.get_bind())
    the_brain = brain if brain is not None else EchoBrain()
    the_transcriber = transcriber if transcriber is not None else FakeTranscriber()
    return ConverseStore(
        session_factory=factory,
        transcriber_loader=loader or (lambda: the_transcriber),
        brain_builder=lambda: TimedBrain(the_brain),
        model_client_builder=lambda: None,
        receipt_writer=(receipts.append if receipts is not None else lambda entry: None),
        clock=clock,
    )


# ---------------------------------------------------------------------------
# Lifecycle + turns
# ---------------------------------------------------------------------------


def test_create_session_warms_asr_and_returns_id(db):
    store = make_store(db)
    created = store.create_session()
    assert created["asr_ready"] is True
    assert len(created["session_id"]) >= 16


def test_unknown_session_is_a_404_shaped_error(db):
    store = make_store(db)
    with pytest.raises(ConverseError) as excinfo:
        store.run_turn("nope", turn_id=1, text="hello")
    assert excinfo.value.status_code == 404


def test_text_turn_routes_through_real_pipeline_to_confirmation_offer(db):
    store = make_store(db)
    session_id = store.create_session()["session_id"]
    result = store.run_turn(session_id, turn_id=1, text="Remind me to water the plants")

    assert result["kind"] == "confirm_offer"  # captured, staged, offered
    assert result["awaiting"] == "yes_no"
    assert "water the plants" in result["heard"]
    assert "yes or no" in result["speech"]
    # One turn, one readback: the capture speech is folded into the offer so
    # the subject is not spoken twice back-to-back.
    assert result["speech"].count("water the plants") == 1
    assert db.query(CapturedIntent).count() == 1

    confirmed = store.run_turn(session_id, turn_id=2, text="yes")
    assert confirmed["kind"] == "executed"
    action = db.query(StagedAction).one()
    assert action.status == "executed"
    assert action.confirmed_by == "patient"


def test_audio_turn_transcribes_and_deletes_the_temp_file(db):
    transcriber = FakeTranscriber(["What is the weather today?"])
    brain = EchoBrain(
        speech="Sunny and 16 in Fitzroy.",
        sources=(Source(label="Open-Meteo — Fitzroy", url="https://open-meteo.com/", fresh_as_of="3pm"),),
    )
    store = make_store(db, brain=brain, transcriber=transcriber)
    session_id = store.create_session()["session_id"]

    result = store.run_turn(session_id, turn_id=1, audio_base64=WAV_B64)

    assert result["kind"] == "answer"
    assert result["heard"] == "What is the weather today?"
    assert result["sources"] == [
        {"label": "Open-Meteo — Fitzroy", "url": "https://open-meteo.com/", "fresh_as_of": "3pm"}
    ]
    assert len(transcriber.seen_paths) == 1
    assert not transcriber.seen_paths[0].exists()  # deleted after transcription
    timings = result["timings_ms"]
    for key in ("decode", "asr", "route", "provider", "total_after_done"):
        assert key in timings and timings[key] >= 0


def test_temp_audio_deleted_even_when_transcription_fails(db):
    seen = []

    def failing_transcriber(path):
        seen.append(path)
        raise ValueError("undecodable audio")

    store = make_store(db, transcriber=failing_transcriber)
    session_id = store.create_session()["session_id"]
    with pytest.raises(ConverseError) as excinfo:
        store.run_turn(session_id, turn_id=1, audio_base64=WAV_B64)
    assert excinfo.value.status_code == 422
    assert len(seen) == 1 and not seen[0].exists()

    # The session survives the failed turn.
    ok = store.run_turn(session_id, turn_id=2, text="Remind me to stretch")
    assert ok["kind"] == "confirm_offer"


def test_audio_validation_bad_base64_oversize_and_exclusive_inputs(db):
    store = make_store(db)
    session_id = store.create_session()["session_id"]

    with pytest.raises(ConverseError) as bad:
        store.run_turn(session_id, turn_id=1, audio_base64="not-base64!")
    assert bad.value.status_code == 422

    huge = base64.b64encode(b"x" * (MAX_CONVERSE_AUDIO_BYTES + 1)).decode("ascii")
    with pytest.raises(ConverseError) as oversize:
        store.run_turn(session_id, turn_id=1, audio_base64=huge)
    assert oversize.value.status_code == 413

    with pytest.raises(ConverseError) as both:
        store.run_turn(session_id, turn_id=1, audio_base64=WAV_B64, text="hi")
    assert both.value.status_code == 422

    with pytest.raises(ConverseError) as neither:
        store.run_turn(session_id, turn_id=1)
    assert neither.value.status_code == 422


def test_silent_window_is_a_gentle_retry_not_an_error(db):
    store = make_store(db, transcriber=FakeTranscriber(lines=[]))
    session_id = store.create_session()["session_id"]
    result = store.run_turn(session_id, turn_id=1, audio_base64=WAV_B64)
    assert result["state"] == "silence"
    assert "take your time" in result["speech"].lower()


def test_asr_unavailable_creates_session_but_audio_turns_are_503(db):
    def unavailable():
        raise RuntimeError("faster-whisper is not installed (simulated)")

    store = make_store(db, loader=unavailable)
    created = store.create_session()
    assert created["asr_ready"] is False
    assert "faster-whisper" in (created["asr_hint"] or "")

    with pytest.raises(ConverseError) as excinfo:
        store.run_turn(created["session_id"], turn_id=1, audio_base64=WAV_B64)
    assert excinfo.value.status_code == 503

    typed = store.run_turn(created["session_id"], turn_id=2, text="Remind me to stretch")
    assert typed["kind"] == "confirm_offer"  # typing still works


def test_repair_choices_expose_position_and_label_only(db):
    store = make_store(db)
    session_id = store.create_session()["session_id"]
    result = store.run_turn(
        session_id, turn_id=1, text="Call... the... you know... the one with the garden..."
    )
    assert result["awaiting"] == "choices"
    assert result["choices"], "expected repair choices"
    for choice in result["choices"]:
        assert set(choice.keys()) == {"position", "label"}

    selected = store.run_turn(session_id, turn_id=2, text="1")
    assert selected["kind"] == "confirm_offer"  # selection captured + offered
    assert db.query(CapturedIntent).count() == 1


def test_followup_history_survives_across_http_turns(db):
    brain = EchoBrain()
    store = make_store(db, brain=brain)
    session_id = store.create_session()["session_id"]
    store.run_turn(session_id, turn_id=1, text="What day is it today?")
    store.run_turn(session_id, turn_id=2, text="What about tomorrow?")
    assert brain.calls == 2  # one TextSession, one brain, shared history


# ---------------------------------------------------------------------------
# Stop and the generation contract
# ---------------------------------------------------------------------------


def test_stop_with_no_turn_in_flight_dismisses_pending_choices(db):
    store = make_store(db)
    session_id = store.create_session()["session_id"]
    offered = store.run_turn(
        session_id, turn_id=1, text="Call... the... you know... the one with the garden..."
    )
    assert offered["awaiting"] == "choices"

    stopped = store.stop(session_id)
    assert stopped["stopped"] is True
    screen = get_screen_state(db)
    assert screen is not None and screen.kind == "cancelled"

    # "1" after Stop must not select a dismissed choice.
    result = store.run_turn(session_id, turn_id=2, text="1")
    assert result["kind"] != "confirm_offer"
    assert db.query(CapturedIntent).count() == 0


def test_stop_during_processing_discards_the_late_result(db):
    gate = GateBrain()
    store = make_store(db, brain=gate)
    session_id = store.create_session()["session_id"]

    results = []

    def run():
        results.append(store.run_turn(session_id, turn_id=1, text="What day is it today?"))

    turn_thread = threading.Thread(target=run)
    turn_thread.start()
    assert gate.entered.wait(timeout=5)

    stopped = store.stop(session_id)  # races the in-flight brain call
    assert stopped["stopped"] is True

    gate.release.set()
    turn_thread.join(timeout=5)
    assert not turn_thread.is_alive()

    assert results[0]["state"] == "stopped"
    assert results[0]["speech"] == ""  # the late answer never surfaces
    assert store.state(session_id)["last_result"] is None
    screen = get_screen_state(db)
    assert screen is not None and screen.kind == "cancelled"


def test_hundred_stop_races_produce_zero_stale_results(db):
    gate = GateBrain()
    store = make_store(db, brain=gate)
    session_id = store.create_session()["session_id"]

    stale = []
    for round_number in range(100):
        gate.entered.clear()
        gate.release.clear()
        results = []
        thread = threading.Thread(
            target=lambda: results.append(
                store.run_turn(session_id, turn_id=round_number, text="What day is it?")
            )
        )
        thread.start()
        assert gate.entered.wait(timeout=5)
        store.stop(session_id)
        gate.release.set()
        thread.join(timeout=5)
        assert not thread.is_alive()
        if results[0]["state"] != "stopped" or results[0]["speech"]:
            stale.append((round_number, results[0]))

    assert stale == [], f"stale results after Stop: {stale[:3]}"


def test_turn_after_stop_runs_under_the_new_generation(db):
    brain = EchoBrain()
    store = make_store(db, brain=brain)
    session_id = store.create_session()["session_id"]
    store.stop(session_id)
    result = store.run_turn(session_id, turn_id=1, text="What day is it today?")
    assert result["state"] == "answer"
    assert result["speech"] == "echo answer"


# ---------------------------------------------------------------------------
# Expiry / eviction / end
# ---------------------------------------------------------------------------


def test_sessions_expire_after_inactivity(db):
    now = [0.0]
    store = make_store(db, clock=lambda: now[0])
    session_id = store.create_session()["session_id"]
    now[0] = SESSION_TTL_SECONDS + 1.0
    store.create_session()  # triggers the sweep
    with pytest.raises(ConverseError):
        store.state(session_id)


def test_session_cap_evicts_the_most_idle(db):
    now = [0.0]
    store = make_store(db, clock=lambda: now[0])
    first = store.create_session()["session_id"]
    ids = [first]
    for _ in range(MAX_SESSIONS - 1):
        now[0] += 1.0
        ids.append(store.create_session()["session_id"])
    now[0] += 1.0
    newest = store.create_session()["session_id"]  # exceeds the cap
    with pytest.raises(ConverseError):
        store.state(first)  # the most idle was evicted
    assert store.state(newest)["session_id"] == newest


def test_end_session_closes_cleanly(db):
    store = make_store(db)
    session_id = store.create_session()["session_id"]
    assert store.end_session(session_id) == {"ended": True}
    with pytest.raises(ConverseError):
        store.state(session_id)


# ---------------------------------------------------------------------------
# Receipts
# ---------------------------------------------------------------------------


def test_every_turn_writes_an_aggregate_only_receipt(db):
    receipts = []
    store = make_store(db, receipts=receipts)
    session_id = store.create_session()["session_id"]
    store.run_turn(session_id, turn_id=1, text="Remind me to water the plants")

    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["recorded_by"] == "server"
    assert receipt["kind"] == "confirm_offer"
    assert "timings_ms" in receipt
    # Aggregate-only: never the words that were said.
    assert "water the plants" not in str(receipt)


def test_client_receipts_are_filtered_to_known_marks(db):
    receipts = []
    store = make_store(db, receipts=receipts)
    session_id = store.create_session()["session_id"]
    store.record_client_receipt(
        session_id,
        {
            "turn_id": 1,
            "done_to_first_audio_ms": 1234.5,
            "outcome": "answer",
            "sneaky": "ignored",
            "heard": "should never be recorded",
        },
    )
    assert len(receipts) == 1
    assert receipts[0]["done_to_first_audio_ms"] == 1234.5
    assert "sneaky" not in receipts[0]
    assert "heard" not in receipts[0]


def test_default_receipt_writer_lands_in_parker_home(db, monkeypatch, tmp_path):
    from app import paths
    from app.parker.converse import write_receipt

    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path))
    write_receipt({"ts": "t", "state": "answer"})
    lines = (tmp_path / "receipts" / "converse_latency.jsonl").read_text().splitlines()
    assert len(lines) == 1 and '"state": "answer"' in lines[0]


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


@pytest.fixture
def http_store(db, monkeypatch):
    store = make_store(db)
    monkeypatch.setattr(converse_router, "converse_store", store)
    return store


def test_http_full_loop_create_turn_stop_state_end(db, http_store):
    created = client.post("/parker/converse/sessions")
    assert created.status_code == 200
    session_id = created.json()["session_id"]

    turn = client.post(
        f"/parker/converse/sessions/{session_id}/turns",
        json={"turn_id": 1, "text": "Remind me to water the plants"},
    )
    assert turn.status_code == 200
    assert turn.json()["kind"] == "confirm_offer"

    stop = client.post(f"/parker/converse/sessions/{session_id}/stop")
    assert stop.status_code == 200 and stop.json()["stopped"] is True

    state = client.get(f"/parker/converse/sessions/{session_id}/state")
    assert state.status_code == 200 and state.json()["last_result"] is None

    receipt = client.post(
        f"/parker/converse/sessions/{session_id}/receipts",
        json={"turn_id": 1, "stop_to_silence_ms": 12},
    )
    assert receipt.status_code == 200

    ended = client.post(f"/parker/converse/sessions/{session_id}/end")
    assert ended.status_code == 200
    assert client.get(f"/parker/converse/sessions/{session_id}/state").status_code == 404


def test_http_turn_validation_and_error_mapping(db, http_store):
    session_id = client.post("/parker/converse/sessions").json()["session_id"]
    bad = client.post(
        f"/parker/converse/sessions/{session_id}/turns",
        json={"turn_id": 1, "audio_base64": "not-base64!"},
    )
    assert bad.status_code == 422
    missing = client.post("/parker/converse/sessions/nope/turns", json={"turn_id": 1, "text": "hi"})
    assert missing.status_code == 404


def test_http_turn_response_never_leaks_capture_internals(db, http_store):
    session_id = client.post("/parker/converse/sessions").json()["session_id"]
    response = client.post(
        f"/parker/converse/sessions/{session_id}/turns",
        json={"turn_id": 1, "text": "Call... the... you know... the one with the garden..."},
    )
    body = response.text
    for leaked in ("intent_text", "recipient", "subject", "parker.db", "/Users/", "api_key"):
        assert leaked not in body


# ---------------------------------------------------------------------------
# The page itself (design contract)
# ---------------------------------------------------------------------------


def test_converse_page_serves_the_four_controls_without_auth(db):
    response = client.get("/parker/converse")
    assert response.status_code == 200
    html = response.text
    for control in ("btn-start", "btn-done", "btn-stop", "btn-again", "btn-yes", "btn-no"):
        assert control in html
    assert "Start listening" in html
    assert "Done talking" in html
    assert "Stop Parker" in html


def test_converse_page_pins_stop_and_stale_guard_mechanics(db):
    html = client.get("/parker/converse").text
    assert "speechSynthesis.cancel()" in html
    assert "clientGen" in html  # stale results are dropped client-side
    assert "abortCtl.abort()" in html
    assert "Escape" in html  # keyboard stop
    assert "Type instead" in html  # typing fallback stays available
    assert "getUserMedia" in html


def test_converse_page_never_speaks_urls(db):
    html = client.get("/parker/converse").text
    # Sources render as label + freshness; the URL is title-only.
    assert "chip.title = source.url" in html
    # The spoken text is exactly data.speech — no source narration path.
    assert "SpeechSynthesisUtterance(text)" in html


# ---------------------------------------------------------------------------
# Adversarial-verifier findings (2026-08-29)
# ---------------------------------------------------------------------------


def test_one_window_never_speaks_two_stacked_questions(db):
    """Pause-free speech splits into two utterances in one window; a repair
    question superseded by the second line must not be spoken, and the
    rendered choices must be the live set."""

    transcriber = FakeTranscriber(
        ["Call... the... you know... the one with the garden...", "remind me to stretch"]
    )
    store = make_store(db, transcriber=transcriber)
    session_id = store.create_session()["session_id"]

    result = store.run_turn(session_id, turn_id=1, audio_base64=WAV_B64)

    assert result["awaiting"] == "yes_no"
    assert "Did you mean" not in result["speech"]  # the dead prompt is silent
    assert result["speech"].count("?") == 1  # exactly one question per turn
    assert result["choices"] == []  # no buttons for a dismissed prompt


def test_choices_render_even_when_a_control_word_ends_the_window(db):
    """Line two being a bare control word must not hide the live choice
    buttons the person is being asked to pick from."""

    transcriber = FakeTranscriber(
        ["Call... the... you know... the one with the garden...", "hold on"]
    )
    store = make_store(db, transcriber=transcriber)
    session_id = store.create_session()["session_id"]

    result = store.run_turn(session_id, turn_id=1, audio_base64=WAV_B64)

    assert result["awaiting"] == "choices"
    assert result["choices"], "live pending choices must render"
    for choice in result["choices"]:
        assert set(choice.keys()) == {"position", "label"}


def test_stop_during_a_silent_window_still_means_stopped(db):
    """The silence path must honor the generation contract too."""

    release = threading.Event()
    entered = threading.Event()

    def gated_silence(path):
        entered.set()
        assert release.wait(timeout=5)
        return []  # a silent window

    store = make_store(db, transcriber=gated_silence)
    session_id = store.create_session()["session_id"]

    results = []
    thread = threading.Thread(
        target=lambda: results.append(
            store.run_turn(session_id, turn_id=1, audio_base64=WAV_B64)
        )
    )
    thread.start()
    assert entered.wait(timeout=5)
    store.stop(session_id)
    release.set()
    thread.join(timeout=5)

    assert results[0]["state"] == "stopped"
    assert results[0]["speech"] == ""
    assert store.state(session_id)["last_result"] is None


def test_concurrent_session_creates_load_the_model_exactly_once(db):
    loads = []

    def loader():
        loads.append(1)
        return FakeTranscriber()

    store = make_store(db, loader=loader)
    threads = [threading.Thread(target=store.create_session) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert len(loads) == 1


def test_turn_racing_end_session_gets_a_404_not_a_closed_db(db):
    """A turn that acquires the lock after end_session dropped the session
    must refuse instead of writing through a closed DB session."""

    release = threading.Event()
    entered = threading.Event()

    def gated(path):
        entered.set()
        assert release.wait(timeout=5)
        return ["What day is it?"]

    store = make_store(db, transcriber=gated)
    session_id = store.create_session()["session_id"]

    outcomes = []

    def in_flight():
        outcomes.append(("first", store.run_turn(session_id, turn_id=1, audio_base64=WAV_B64)))

    def late_turn():
        try:
            outcomes.append(("late", store.run_turn(session_id, turn_id=2, text="hello")))
        except ConverseError as exc:
            outcomes.append(("late-error", exc.status_code))

    first = threading.Thread(target=in_flight)
    first.start()
    assert entered.wait(timeout=5)

    late = threading.Thread(target=late_turn)
    late.start()

    ender = threading.Thread(target=lambda: store.end_session(session_id))
    ender.start()

    release.set()
    first.join(timeout=5)
    late.join(timeout=5)
    ender.join(timeout=5)

    late_results = [entry for entry in outcomes if entry[0].startswith("late")]
    assert late_results, "the late turn must resolve one way or the other"
    kind, value = late_results[0]
    # Either it ran before the drop (fine) or it was refused with 404 —
    # never an exception from a closed session.
    assert kind == "late" or value == 404


# ---------------------------------------------------------------------------
# Sentence streaming (rung 1): events, guards, caps, stop
# ---------------------------------------------------------------------------


class StreamingBrain:
    """Emits scripted sentences through respond_stream, like the Claude adapter."""

    def __init__(self, sentences, sources=()):
        self.sentences = list(sentences)
        self.sources = tuple(sources)

    def respond(self, history, utterance, context):
        return BrainReply(speech=" ".join(self.sentences), sources=self.sources)

    def respond_stream(self, history, utterance, context, on_sentence):
        for sentence in self.sentences:
            on_sentence(sentence)
        return self.respond(history, utterance, context)


def test_streaming_turn_emits_heard_then_guarded_sentences_then_matches_final(db):
    brain = StreamingBrain(["It's 14 and clear.", "Tomorrow looks rainy."])
    store = make_store(db, brain=brain)
    session_id = store.create_session()["session_id"]

    events = []
    result = store.run_turn(
        session_id, turn_id=1, text="What is the weather today?", emit=events.append
    )

    assert events[0] == {"event": "heard", "heard": "What is the weather today?"}
    speech_events = [e["text"] for e in events if e["event"] == "speech"]
    assert speech_events == ["It's 14 and clear.", "Tomorrow looks rainy."]
    assert result["kind"] == "answer"
    assert result["speech"] == "It's 14 and clear. Tomorrow looks rainy."


def test_streamed_sentences_stop_at_the_tts_trim_cap(db):
    brain = StreamingBrain([f"Sentence number {i}." for i in range(1, 7)])
    store = make_store(db, brain=brain)
    session_id = store.create_session()["session_id"]

    events = []
    store.run_turn(session_id, turn_id=1, text="What day is it?", emit=events.append)

    speech_events = [e for e in events if e["event"] == "speech"]
    assert len(speech_events) == 3  # mirrors trim_for_speech's 3-sentence cap


def test_medical_violation_assembled_across_sentences_is_never_streamed(db):
    brain = StreamingBrain(
        ["Here is a thought.", "You should take an extra 50 mg before lunch."]
    )
    store = make_store(db, brain=brain)
    session_id = store.create_session()["session_id"]

    events = []
    result = store.run_turn(
        session_id, turn_id=1, text="What do you think?", emit=events.append
    )

    speech_events = [e["text"] for e in events if e["event"] == "speech"]
    assert speech_events == ["Here is a thought."]  # the violating tail never streams
    assert result["kind"] == "refused"  # the final guard replaces the whole reply
    assert "doctor" in result["speech"]


def test_stop_mid_stream_silences_the_remaining_sentences(db):
    store_holder = {}
    emitted = []

    class StopAfterFirst:
        def respond(self, history, utterance, context):
            return BrainReply(speech="one. two.")

        def respond_stream(self, history, utterance, context, on_sentence):
            on_sentence("Sentence one.")
            store_holder["store"].stop(store_holder["sid"])  # Stop lands mid-answer
            on_sentence("Sentence two.")
            return self.respond(history, utterance, context)

    store = make_store(db, brain=StopAfterFirst())
    session_id = store.create_session()["session_id"]
    store_holder.update(store=store, sid=session_id)

    result = store.run_turn(
        session_id, turn_id=1, text="What day is it?", emit=emitted.append
    )

    speech_events = [e["text"] for e in emitted if e["event"] == "speech"]
    assert speech_events == ["Sentence one."]  # nothing after Stop
    assert result["state"] == "stopped"


def test_http_stream_endpoint_yields_ndjson_events(db, http_store):
    session_id = client.post("/parker/converse/sessions").json()["session_id"]
    with client.stream(
        "POST",
        f"/parker/converse/sessions/{session_id}/turns/stream",
        json={"turn_id": 1, "text": "Remind me to water the plants"},
    ) as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line]
    events = [__import__("json").loads(line) for line in lines]
    assert events[0]["event"] == "heard"
    assert events[-1]["event"] == "final"
    assert events[-1]["kind"] == "confirm_offer"


def test_http_stream_endpoint_reports_errors_as_events(db, http_store):
    with client.stream(
        "POST",
        "/parker/converse/sessions/nope/turns/stream",
        json={"turn_id": 1, "text": "hi"},
    ) as response:
        lines = [line for line in response.iter_lines() if line]
    events = [__import__("json").loads(line) for line in lines]
    assert events[-1]["event"] == "error"
    assert events[-1]["status"] == 404


def test_converse_page_carries_the_presence_layer(db):
    """Thinking/talking presence: orb, earcons, streaming reader, honest cue."""

    html = client.get("/parker/converse").text
    assert 'id="orb"' in html
    assert "earcon(" in html            # audible tap confirmation
    assert "turns/stream" in html       # sentence-streaming endpoint
    assert "Let me check." in html      # truthful latency cue, never a fake answer
    assert "prefers-reduced-motion" in html
    assert "onboundary" in html         # word-pulse while talking
    assert "Thinking…" in html and "Parker is talking" in html


def test_converse_page_offers_the_live_lane_behind_availability(db):
    html = client.get("/parker/converse").text
    assert 'id="btn-live"' in html
    assert "Live conversation" in html
    assert "realtime_available" in html   # server decides; the page just shows
    assert "/parker/converse/realtime" in html
    assert "talk over Parker any time" in html  # barge-in is part of the promise


def test_streamed_speech_is_always_a_prefix_of_the_final_speech(db):
    """The page speaks the final's REMAINDER after streamed sentences (the
    confirmation question, 'Want more detail?'). That only works if what
    streams is a strict prefix of the final guard-screened speech — the
    char cap must mirror trim_for_speech, not just the sentence cap."""

    long_sentences = [f"This is quite a long sentence number {i} with plenty of words in it." for i in range(1, 7)]
    brain = StreamingBrain(long_sentences)
    store = make_store(db, brain=brain)
    session_id = store.create_session()["session_id"]

    events = []
    result = store.run_turn(session_id, turn_id=1, text="What day is it?", emit=events.append)

    streamed = " ".join(e["text"] for e in events if e["event"] == "speech")
    assert result["speech"].startswith(streamed)
    assert len(streamed) <= 360  # the trim cap holds on the streamed path too


def test_converse_page_mounts_the_reachy_presence_scene(db):
    """The 3D Reachy Mini scene (2026-08-31 brief): mounted as enhancement,
    orb + full text experience kept as the fallback."""

    html = client.get("/parker/converse").text
    assert 'id="reachy-mount"' in html
    assert "/parker/converse/static/converse/expression.js" in html
    assert "createReachyScene" in html
    assert "scene-active" in html
    assert 'aria-live="polite"' in html
    # The renderer boots as an independent module — the microphone never
    # waits for it — and the orb stays in the markup as the fallback.
    assert 'type="module"' in html
    assert 'id="orb"' in html


def test_converse_page_forwards_real_signals_to_the_expression_state(db):
    """Motion tells the truth: every presence input is a real signal."""

    html = client.get("/parker/converse").text
    assert "ParkerExpression.createController" in html
    assert "presence('connect', {mode: 'live'})" in html
    assert "'work_start'" in html and "'work_failed'" in html
    assert "assistant_audio_drained" in html
    assert "micEnergy(" in html  # hearing derives from actual mic level
    # `clear` yields the scene only on a real flush or a cancelled think.
    assert "flushLivePlayback();" in html
    assert "if (flushed || thinkingCancelled) presence('interrupted')" in html
    # The PAGE owns truth housekeeping: overlay TTLs and the interrupt
    # dwell keep expiring with no WebGL renderer at all (review find).
    assert "expr.tick()" in html
    # Repair posture rides the real awaiting state of a finished turn.
    assert "'repair_offered' : 'repair_resolved'" in html


def test_converse_page_separates_stop_from_failure_outcomes(db):
    """'Stopped' is his; a dropped line is an error he can retry — the
    page never presents a failure as something he did (review find)."""

    html = client.get("/parker/converse").text
    assert "endLive('The live line dropped.', 'error')" in html
    assert "endLive('The live line closed.', 'error')" in html
    assert "live.closingSeen" in html  # a post-goodbye close stays a normal end
    assert "presence('offline')" in html  # unavailable is neither stop nor error
    # a stale goodbye-drain timer can never end a NEW session
    assert "live.ws === wsAtClosing" in html


def test_converse_page_makes_live_primary_when_available(db):
    """The Live lane is the lane you meet (first tester finding): the page
    carries the live-primary styling and the live control leads the row."""

    html = client.get("/parker/converse").text
    assert "live-primary" in html
    assert html.index('id="btn-live"') < html.index('id="btn-start"')
    assert "Start listening is the push-button way" in html


def test_converse_page_speaks_the_final_remainder_and_live_redirect(db):
    html = client.get("/parker/converse").text
    # the confirmation question / 'Want more detail?' remainder is spoken
    assert "finalSpeech.slice(spokenSoFar.length)" in html
    assert "startsWith(spokenSoFar)" in html
    # the live-lane medical redirect bypasses the turn-generation queue
    assert "speakNow(" in html
    # one live line, one opening at a time — and Stop covers a mic mid-open
    assert "startingLive" in html
