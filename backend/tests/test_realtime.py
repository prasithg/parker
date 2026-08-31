"""The realtime full-duplex lane: Parker stays the policy boundary.

Everything runs against a scripted fake upstream over the real websocket
endpoint — no network, no OpenAI key. Pinned contracts:

- the session config carries the Parker persona, patient semantic VAD,
  transcription, and the tool surface: propose_action always, look_that_up
  only when a brain is configured (keyless sessions honestly have neither
  search nor live data);
- the fast-voice orchestrator: look_that_up is acked instantly, the worker
  runs behind the conversation, and the result is injected as a system
  item (with the original question, fenced untrusted content, and sources
  browser-only) nudged through exactly one response.create emitter;
- the context card injects silently (no nudge, never narrated) and drops
  any line the spoken-dosage guard would cancel;
- the post-hoc guard cancels a medical-boundary reply mid-stream, flushes
  playback, and speaks the standard redirect;
- a propose_action function call stages through the real pipeline and the
  model is told it is waiting for on-screen confirmation — nothing
  executes from this lane;
- idle → wrap-up question → goodbye → a "closing" handshake so the
  browser drains audio before the line drops; the session is persisted
  (call log + one topic memory) only when he actually said something;
- browser Stop cancels the response; junk audio is never forwarded;
- no key -> an honest unavailable message, never a broken socket.
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.brain.adapter import Source
from app.brain.guard import MEDICAL_BOUNDARY_REDIRECT
from app.db.models import CallLog, StagedAction
from app.main import app
from app.memory.models import ConversationMemory
from app.parker import realtime, realtime_workers
from app.parker.realtime_workers import WorkerResult

client = TestClient(app)


class FakeUpstream:
    """Scripted OpenAI-side socket: replays events, records what Parker sends.

    ``feed()`` is thread-safe so tests can interleave upstream events with
    worker completions — the ordering contracts need that, and the old
    replay-everything-at-connect shape made them pass vacuously.
    """

    def __init__(self, events):
        self.sent: list[dict] = []
        self._queue: queue.Queue = queue.Queue()
        for event in events:
            self._queue.put(event)
        self.closed = False

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def recv(self) -> str:
        while True:
            try:
                return json.dumps(self._queue.get_nowait())
            except queue.Empty:
                await asyncio.sleep(0.01)

    def feed(self, event) -> None:
        self._queue.put(event)

    async def close(self) -> None:
        self.closed = True


def _wait_until(predicate, timeout: float = 3.0, interval: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _function_outputs(fake) -> list[dict]:
    return [
        e
        for e in fake.sent
        if e["type"] == "conversation.item.create"
        and e["item"].get("type") == "function_call_output"
    ]


def _system_items(fake) -> list[str]:
    return [
        e["item"]["content"][0]["text"]
        for e in fake.sent
        if e["type"] == "conversation.item.create"
        and e["item"].get("type") == "message"
        and e["item"].get("role") == "system"
    ]


def _response_creates(fake) -> int:
    return sum(1 for e in fake.sent if e["type"] == "response.create")


@pytest.fixture
def realtime_enabled(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "parker_realtime_enabled", True)
    return settings


@pytest.fixture
def brainless(monkeypatch):
    """Force the no-brain path regardless of the developer's local .env."""

    from app.config import settings

    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "parker_openclaw_gateway_url", "")
    return settings


@pytest.fixture
def brained(monkeypatch):
    """Force look_that_up availability without any real key use."""

    from app.config import settings

    monkeypatch.setattr(settings, "anthropic_api_key", "test-anthropic-key")
    return settings


@pytest.fixture
def upstream(monkeypatch):
    """Install a scripted upstream; tests load events via .script(...)."""

    holder: dict = {}

    def install(events):
        fake = FakeUpstream(events)
        holder["fake"] = fake

        async def connect():
            return fake

        monkeypatch.setattr(realtime, "connect_openai", connect)
        return fake

    holder["script"] = install
    return holder


@pytest.fixture(autouse=True)
def realtime_db(db, monkeypatch):
    """Bridge side effects land in the test engine, never the real DB.

    Teardown waits for the bridge (and its worker threads) to finish so
    the fixture's drop_all never races a thread still holding the shared
    in-memory connection.
    """

    factory = sessionmaker(bind=db.get_bind())
    monkeypatch.setattr(realtime, "_db_session_factory", factory)
    yield db
    _wait_until(lambda: realtime._active_bridges == 0, timeout=3.0)


def _look_done_event(question, call_id="look-1"):
    return {
        "type": "response.done",
        "response": {
            "output": [
                {
                    "type": "function_call",
                    "name": "look_that_up",
                    "call_id": call_id,
                    "arguments": json.dumps({"question": question}),
                }
            ]
        },
    }


def test_without_a_key_the_lane_is_honestly_unavailable(db):
    with client.websocket_connect("/parker/converse/realtime") as ws:
        message = ws.receive_json()
    assert message["type"] == "unavailable"
    assert "OpenAI key" in message["text"]


def test_session_config_carries_persona_vad_transcription_and_tools(
    db, realtime_enabled, brainless, upstream
):
    fake = upstream["script"]([])
    with client.websocket_connect("/parker/converse/realtime") as ws:
        ws.send_json({"type": "audio", "data": "QUJD"})  # valid base64
        ws.send_json({"type": "end"})

    update = fake.sent[0]
    assert update["type"] == "session.update"
    session = update["session"]
    assert session["audio"]["input"]["turn_detection"] == {
        "type": "semantic_vad",
        "eagerness": "low",  # patient speech: wait the longest
        # the injection mechanics depend on these; pinned, not defaulted
        "create_response": True,
        "interrupt_response": True,
    }
    assert session["audio"]["input"]["transcription"]["model"]
    # output rate is required by the live API — its absence voided the whole
    # session.update (tools included) on the first real probe
    assert session["audio"]["output"]["format"] == {"type": "audio/pcm", "rate": 24000}
    # brainless -> propose_action stays the only tool
    assert [tool["name"] for tool in session["tools"]] == ["propose_action"]
    assert "Parkinson" in session["instructions"]
    assert "waiting for their" in session["instructions"]  # wraps across a line
    assert "Right now it is" in session["instructions"]  # local clock grounding
    # the browser's audio chunk was forwarded verbatim
    appended = [e for e in fake.sent if e["type"] == "input_audio_buffer.append"]
    assert appended and appended[0]["audio"] == "QUJD"


def test_brained_session_offers_look_that_up_and_says_so(
    db, realtime_enabled, brained
):
    session = realtime.build_session_update()["session"]
    assert [tool["name"] for tool in session["tools"]] == [
        "propose_action",
        "look_that_up",
    ]
    assert "look_that_up" in session["instructions"]
    assert "do NOT have web search" not in session["instructions"]
    assert "read web addresses aloud" in session["instructions"]  # wraps lines


def test_greeting_is_requested_before_any_audio_arrives(
    db, realtime_enabled, brainless, upstream
):
    fake = upstream["script"]([])
    with client.websocket_connect("/parker/converse/realtime") as ws:
        ws.send_json({"type": "end"})
    items = _system_items(fake)
    assert items and "line just opened" in items[0]
    assert _response_creates(fake) >= 1  # the greeting nudge


def test_audio_and_transcripts_flow_to_the_browser(
    db, realtime_enabled, brainless, upstream
):
    upstream["script"](
        [
            {"type": "response.output_audio.delta", "delta": "UENN"},
            {"type": "response.output_audio_transcript.delta", "delta": "It's a lovely day."},
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "what's the weather",
            },
        ]
    )
    with client.websocket_connect("/parker/converse/realtime") as ws:
        assert ws.receive_json() == {"type": "audio", "data": "UENN"}
        assert ws.receive_json() == {
            "type": "assistant_transcript_delta",
            "text": "It's a lovely day.",
        }
        assert ws.receive_json() == {"type": "user_transcript", "text": "what's the weather"}
        ws.send_json({"type": "end"})


def test_posthoc_guard_cancels_flushes_and_redirects(
    db, realtime_enabled, brainless, upstream
):
    fake = upstream["script"](
        [
            {"type": "response.output_audio_transcript.delta", "delta": "Maybe try "},
            {"type": "response.output_audio_transcript.delta", "delta": "taking an extra 50 mg tonight."},
            # audio arriving after the trip must never reach the browser
            {"type": "response.output_audio.delta", "delta": "UENN"},
        ]
    )
    with client.websocket_connect("/parker/converse/realtime") as ws:
        assert ws.receive_json()["type"] == "assistant_transcript_delta"  # clean prefix
        # "Maybe try " + "taking…50 mg" assembles the violation across deltas
        assert ws.receive_json() == {"type": "clear"}
        redirect = ws.receive_json()
        assert redirect["type"] == "guard_redirect"
        assert redirect["text"] == MEDICAL_BOUNDARY_REDIRECT
        ws.send_json({"type": "end"})

    cancels = [e for e in fake.sent if e["type"] == "response.cancel"]
    assert cancels, "the violating response must be cancelled upstream"


def test_propose_action_stages_through_the_pipeline_and_reports_back(
    db, realtime_enabled, brainless, upstream
):
    arguments = {
        "action_type": "reminder",
        "label": "a reminder to water the plants",
        "subject": "water the plants",
        "intent_text": "remind me to water the plants",
    }
    fake = upstream["script"](
        [
            {
                "type": "response.done",
                "response": {
                    "output": [
                        {
                            "type": "function_call",
                            "name": "propose_action",
                            "call_id": "call-1",
                            "arguments": json.dumps(arguments),
                        }
                    ]
                },
            }
        ]
    )
    with client.websocket_connect("/parker/converse/realtime") as ws:
        staged_note = ws.receive_json()
        assert staged_note["type"] == "proposal_staged"
        assert "water the plants" in staged_note["label"]
        ws.send_json({"type": "end"})

    action = db.query(StagedAction).one()
    assert action.status == "staged"  # staged, NOT executed — this lane cannot act
    outputs = _function_outputs(fake)
    assert outputs and outputs[0]["item"]["call_id"] == "call-1"
    assert "confirmation" in outputs[0]["item"]["output"]
    assert any(e["type"] == "response.create" for e in fake.sent)


def test_prohibited_action_types_are_rejected_not_staged(
    db, realtime_enabled, brainless, upstream
):
    fake = upstream["script"](
        [
            {
                "type": "response.done",
                "response": {
                    "output": [
                        {
                            "type": "function_call",
                            "name": "propose_action",
                            "call_id": "call-2",
                            "arguments": json.dumps(
                                {
                                    "action_type": "purchase",
                                    "label": "buy tickets",
                                    "subject": "tickets",
                                    "intent_text": "buy the tickets",
                                }
                            ),
                        }
                    ]
                },
            }
        ]
    )
    with client.websocket_connect("/parker/converse/realtime") as ws:
        ws.send_json({"type": "stop"})  # provoke traffic so we can end cleanly
        assert ws.receive_json() == {"type": "clear"}
        ws.send_json({"type": "end"})

    assert db.query(StagedAction).count() == 0
    outputs = _function_outputs(fake)
    assert outputs and "not allowed" in outputs[0]["item"]["output"]


def test_stop_cancels_upstream_and_junk_audio_never_forwards(
    db, realtime_enabled, brainless, upstream
):
    fake = upstream["script"]([])
    with client.websocket_connect("/parker/converse/realtime") as ws:
        ws.send_json({"type": "audio", "data": "not-base64!"})
        ws.send_json({"type": "stop"})
        assert ws.receive_json() == {"type": "clear"}
        ws.send_json({"type": "end"})

    assert not any(e["type"] == "input_audio_buffer.append" for e in fake.sent)
    assert any(e["type"] == "response.cancel" for e in fake.sent)


def test_barge_in_flushes_playback(db, realtime_enabled, brainless, upstream):
    upstream["script"]([{"type": "input_audio_buffer.speech_started"}])
    with client.websocket_connect("/parker/converse/realtime") as ws:
        assert ws.receive_json() == {"type": "clear"}
        ws.send_json({"type": "end"})


def test_exchange_mirrors_to_the_live_screen(db, realtime_enabled, brainless, upstream):
    from app.parker.screen import get_screen_state

    upstream["script"](
        [
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "what's the weather",
            },
            {"type": "response.output_audio_transcript.delta", "delta": "Sunny and mild."},
            {"type": "response.done", "response": {"output": []}},
        ]
    )
    with client.websocket_connect("/parker/converse/realtime") as ws:
        ws.receive_json()  # user_transcript
        ws.receive_json()  # assistant delta
        ws.send_json({"type": "end"})

    state = get_screen_state(db)
    assert state is not None
    assert state.heard == "what's the weather"
    assert state.speech == "Sunny and mild."


# ---------------------------------------------------------------------------
# The fast-voice orchestrator (2026-08-30): conversation never blocks on work
# ---------------------------------------------------------------------------


def test_look_that_up_acks_instantly_and_injects_only_at_a_safe_point(
    db, realtime_enabled, brained, upstream, monkeypatch
):
    """The two-step contract: instant "working" ack, then the result as a
    system item — nudged only when no response is active."""

    release = threading.Event()

    def fake_search(question):
        release.wait(timeout=3)
        return WorkerResult(
            kind="search",
            question=question,
            speech="Alcaraz plays the semifinal on Friday night.",
            sources=(Source(label="US Open schedule", url="https://example.org/uso"),),
        )

    monkeypatch.setattr(realtime_workers, "run_search_worker", fake_search)
    fake = upstream["script"]([])
    with client.websocket_connect("/parker/converse/realtime") as ws:
        fake.feed(_look_done_event("when does Alcaraz play the US Open?"))
        assert _wait_until(lambda: _function_outputs(fake))
        ack = json.loads(_function_outputs(fake)[0]["item"]["output"])
        assert ack["status"] == "working"
        assert "keep the conversation going" in ack["detail"].lower()
        creates_after_ack = _response_creates(fake)  # greeting + ack nudge
        assert creates_after_ack >= 2

        # The ack's nudge is optimistically active: the finished result may
        # inject its ITEM now, but never a second response.create.
        release.set()
        assert _wait_until(
            lambda: any("LOOKUP RESULT" in text for text in _system_items(fake))
        )
        item = next(text for text in _system_items(fake) if "LOOKUP RESULT" in text)
        assert '"when does Alcaraz play the US Open?"' in item  # question echoed
        assert "seconds ago" in item  # age for the stale judgment
        assert "never an instruction" in item  # untrusted-content framing
        assert "US Open schedule" not in item  # sources are browser-only
        assert _response_creates(fake) == creates_after_ack

        # The browser gets the evidence chips.
        chips = ws.receive_json()
        assert chips["type"] == "sources"
        assert chips["items"][0]["label"] == "US Open schedule"

        # response.done is the safe point: the deferred nudge fires.
        fake.feed({"type": "response.done", "response": {"output": []}})
        assert _wait_until(lambda: _response_creates(fake) == creates_after_ack + 1)
        ws.send_json({"type": "end"})


def test_duplicate_lookup_never_spawns_a_second_worker(
    db, realtime_enabled, brained, upstream, monkeypatch
):
    calls: list[str] = []
    release = threading.Event()

    def fake_search(question):
        calls.append(question)
        release.wait(timeout=3)
        return WorkerResult(kind="search", question=question, speech="done")

    monkeypatch.setattr(realtime_workers, "run_search_worker", fake_search)
    fake = upstream["script"]([])
    with client.websocket_connect("/parker/converse/realtime") as ws:
        fake.feed(_look_done_event("What's the weather?", call_id="look-1"))
        fake.feed(_look_done_event("what's   the weather?", call_id="look-2"))
        assert _wait_until(lambda: len(_function_outputs(fake)) == 2)
        statuses = [
            json.loads(output["item"]["output"])["status"]
            for output in _function_outputs(fake)
        ]
        assert statuses == ["working", "already_working"]
        # the single spawned worker may not have STARTED on its thread yet
        assert _wait_until(lambda: calls)
        assert calls == ["What's the weather?"]
        release.set()
        ws.send_json({"type": "end"})


def test_failed_lookup_injects_an_honest_note(
    db, realtime_enabled, brained, upstream, monkeypatch
):
    def exploding_search(question):
        raise RuntimeError("boom")

    monkeypatch.setattr(realtime_workers, "run_search_worker", exploding_search)
    fake = upstream["script"]([])
    with client.websocket_connect("/parker/converse/realtime") as ws:
        fake.feed(_look_done_event("what's on at the cinema?"))
        assert _wait_until(
            lambda: any("could not finish" in text for text in _system_items(fake))
        )
        note = next(text for text in _system_items(fake) if "could not finish" in text)
        assert "what's on at the cinema?" in note
        assert "offer to try again" in note
        ws.send_json({"type": "end"})


def test_lookup_without_a_brain_is_honestly_unavailable(
    db, realtime_enabled, brainless, upstream
):
    """The tool isn't offered brainless — but a model may still call it."""

    fake = upstream["script"]([_look_done_event("anything at all?")])
    with client.websocket_connect("/parker/converse/realtime") as ws:
        assert _wait_until(lambda: _function_outputs(fake))
        ack = json.loads(_function_outputs(fake)[0]["item"]["output"])
        assert ack["status"] == "unavailable"
        ws.send_json({"type": "end"})


def test_context_card_injects_without_a_nudge_and_drops_dose_lines(
    db, realtime_enabled, brainless, upstream
):
    from app.memory.store import save_memory

    save_memory(db, "Loves old Hindi songs in the evening.", "preference")
    save_memory(db, "The pharmacist said his 25-100 mg refill is ready.", "event")
    fake = upstream["script"]([])
    with client.websocket_connect("/parker/converse/realtime") as ws:
        # settle the greeting response so a context nudge WOULD be legal
        fake.feed({"type": "response.done", "response": {"output": []}})
        assert _wait_until(
            lambda: any("Background context" in text for text in _system_items(fake))
        )
        card = next(text for text in _system_items(fake) if "Background context" in text)
        assert "old Hindi songs" in card
        assert "never recite" in card
        assert "25-100 mg" not in card  # the spoken-dosage guard owns this line
        # exactly one response.create (the greeting) — the card never speaks
        assert _response_creates(fake) == 1
        ws.send_json({"type": "end"})


def test_benign_protocol_collisions_never_reach_dad(
    db, realtime_enabled, brainless, upstream
):
    upstream["script"](
        [
            {
                "type": "error",
                "error": {
                    "code": "conversation_already_has_active_response",
                    "message": "Conversation already has an active response",
                },
            },
            {"type": "response.output_audio_transcript.delta", "delta": "Still smooth."},
        ]
    )
    with client.websocket_connect("/parker/converse/realtime") as ws:
        first = ws.receive_json()
        assert first == {"type": "assistant_transcript_delta", "text": "Still smooth."}
        ws.send_json({"type": "end"})


def test_idle_wrapup_then_goodbye_then_closing_handshake(
    db, realtime_enabled, brainless, upstream, monkeypatch
):
    monkeypatch.setattr(realtime, "IDLE_WRAPUP_SECONDS", 0.2)
    monkeypatch.setattr(realtime, "IDLE_GOODBYE_SECONDS", 0.2)
    monkeypatch.setattr(realtime, "CLOSING_DRAIN_SECONDS", 0.3)
    monkeypatch.setattr(realtime, "_WATCHDOG_TICK_SECONDS", 0.05)
    fake = upstream["script"]([])
    with client.websocket_connect("/parker/converse/realtime") as ws:
        assert _wait_until(
            lambda: any("anything else" in text for text in _system_items(fake))
        )
        assert _wait_until(
            lambda: any("goodbye" in text for text in _system_items(fake))
        )
        # the goodbye response finishing is what hands the browser the hang-up
        fake.feed({"type": "response.done", "response": {"output": []}})
        closing = ws.receive_json()
        assert closing == {"type": "closing"}
        # the bridge closes itself even if the browser never answers


def test_barge_in_during_the_goodbye_aborts_the_close(
    db, realtime_enabled, brainless, upstream, monkeypatch
):
    """He says "wait—" over the goodbye: Parker must NOT hang up on him.

    The stand-down fires in the speech_started handler itself, because the
    goodbye's response.done arrives faster than any watchdog tick.
    """

    monkeypatch.setattr(realtime, "IDLE_WRAPUP_SECONDS", 0.15)
    monkeypatch.setattr(realtime, "IDLE_GOODBYE_SECONDS", 30.0)
    monkeypatch.setattr(realtime, "_WATCHDOG_TICK_SECONDS", 0.05)
    fake = upstream["script"]([])
    with client.websocket_connect("/parker/converse/realtime") as ws:
        assert _wait_until(
            lambda: any("anything else" in text for text in _system_items(fake))
        )
        # force the goodbye immediately, then barge in over it
        monkeypatch.setattr(realtime, "IDLE_GOODBYE_SECONDS", 0.0)
        assert _wait_until(lambda: any("goodbye" in t for t in _system_items(fake)))
        monkeypatch.setattr(realtime, "IDLE_GOODBYE_SECONDS", 30.0)
        fake.feed({"type": "input_audio_buffer.speech_started"})
        assert ws.receive_json() == {"type": "clear"}  # barge-in flush
        # the (cancelled) goodbye's response.done lands right after his voice
        fake.feed({"type": "response.done", "response": {"output": []}})
        fake.feed({"type": "response.output_audio_transcript.delta", "delta": "Go on."})
        follow = ws.receive_json()
        assert follow == {"type": "assistant_transcript_delta", "text": "Go on."}
        # no closing event arrived in between — the line stayed open
        ws.send_json({"type": "end"})


def test_a_mute_model_cannot_hold_the_line_open_after_the_goodbye(
    db, realtime_enabled, brainless, upstream, monkeypatch
):
    monkeypatch.setattr(realtime, "IDLE_WRAPUP_SECONDS", 0.1)
    monkeypatch.setattr(realtime, "IDLE_GOODBYE_SECONDS", 0.1)
    monkeypatch.setattr(realtime, "CLOSING_DRAIN_SECONDS", 0.2)
    monkeypatch.setattr(realtime, "_WATCHDOG_TICK_SECONDS", 0.05)
    upstream["script"]([])  # the model never answers anything
    with client.websocket_connect("/parker/converse/realtime") as ws:
        closing = ws.receive_json()  # forced by the watchdog floor
        assert closing == {"type": "closing"}


def test_a_word_from_him_stands_the_wrapup_down(
    db, realtime_enabled, brainless, upstream, monkeypatch
):
    monkeypatch.setattr(realtime, "IDLE_WRAPUP_SECONDS", 0.2)
    monkeypatch.setattr(realtime, "IDLE_GOODBYE_SECONDS", 30.0)  # never in this test
    monkeypatch.setattr(realtime, "_WATCHDOG_TICK_SECONDS", 0.05)
    fake = upstream["script"]([])
    with client.websocket_connect("/parker/converse/realtime") as ws:
        assert _wait_until(
            lambda: any("anything else" in text for text in _system_items(fake))
        )
        fake.feed({"type": "input_audio_buffer.speech_started"})
        assert ws.receive_json() == {"type": "clear"}  # barge-in still flushes
        time.sleep(0.3)
        assert not any("goodbye" in text for text in _system_items(fake))
        ws.send_json({"type": "end"})


def test_finished_session_persists_call_log_and_one_topic_memory(
    db, realtime_enabled, brainless, upstream
):
    upstream["script"](
        [
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "when does Alcaraz play next",
            },
            {"type": "response.output_audio_transcript.delta", "delta": "Let me see."},
            {"type": "response.done", "response": {"output": []}},
            # a visible event AFTER response.done: receiving it below proves
            # the exchange was recorded before the browser hangs up
            {"type": "response.output_audio_transcript.delta", "delta": "There."},
        ]
    )
    with client.websocket_connect("/parker/converse/realtime") as ws:
        ws.receive_json()  # user_transcript
        ws.receive_json()  # assistant delta
        ws.receive_json()  # post-done delta — the exchange is in
        ws.send_json({"type": "end"})

    def finalized():
        db.expire_all()  # the poll must see the finalize thread's write
        call = db.query(CallLog).filter(CallLog.call_type == "realtime").first()
        return call is not None and call.ended_at is not None

    assert _wait_until(finalized)
    call = db.query(CallLog).filter(CallLog.call_type == "realtime").one()
    assert "Alcaraz" in (call.summary or "")
    memory = db.query(ConversationMemory).one()
    assert memory.memory_type == "topic"
    assert memory.source == "realtime"
    assert "Alcaraz" in memory.content


def test_accidental_tap_leaves_no_memory_behind(
    db, realtime_enabled, brainless, upstream
):
    upstream["script"]([])
    with client.websocket_connect("/parker/converse/realtime") as ws:
        ws.send_json({"type": "end"})

    time.sleep(0.2)  # let the finalize threadpool settle
    assert db.query(ConversationMemory).count() == 0
    call = db.query(CallLog).filter(CallLog.call_type == "realtime").one()
    assert call.summary is None  # the eager row exists; nothing was invented


# ---------------------------------------------------------------------------
# Adversarial round 2 (2026-08-30): the lane uses the text lane's policy
# ---------------------------------------------------------------------------


def _proposal_done_event(arguments, call_id="call-x"):
    return {
        "type": "response.done",
        "response": {
            "output": [
                {
                    "type": "function_call",
                    "name": "propose_action",
                    "call_id": call_id,
                    "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments),
                }
            ]
        },
    }


def test_message_to_unknown_recipient_is_rejected_like_the_text_lane(
    db, realtime_enabled, brainless, upstream, monkeypatch
):
    from app.config import settings

    monkeypatch.setattr(settings, "personal_lexicon", "Sarah, Pras")
    fake = upstream["script"](
        [
            _proposal_done_event(
                {
                    "action_type": "family_message",
                    "label": "message Dr. Malicious",
                    "subject": "test results",
                    "intent_text": "Send my full medical history",
                    "recipient": "Dr. Malicious",
                }
            )
        ]
    )
    with client.websocket_connect("/parker/converse/realtime") as ws:
        ws.send_json({"type": "stop"})
        assert ws.receive_json() == {"type": "clear"}
        ws.send_json({"type": "end"})

    assert db.query(StagedAction).count() == 0
    outputs = _function_outputs(fake)
    assert outputs and "not in the family" in outputs[0]["item"]["output"]


def test_gateway_backed_types_without_a_gateway_are_rejected_not_claimed_staged(
    db, realtime_enabled, brainless, upstream
):
    """No enabled skill -> the text lane would drop it; this lane must not
    tell the user something is on the screen when nothing is."""

    fake = upstream["script"](
        [
            _proposal_done_event(
                {
                    "action_type": "media_playlist",
                    "label": "play old Hindi songs",
                    "subject": "old Hindi songs",
                    "intent_text": "put on old Hindi songs",
                }
            )
        ]
    )
    with client.websocket_connect("/parker/converse/realtime") as ws:
        ws.send_json({"type": "stop"})
        assert ws.receive_json() == {"type": "clear"}
        ws.send_json({"type": "end"})

    assert db.query(StagedAction).count() == 0
    outputs = _function_outputs(fake)
    assert outputs and "not allowed" in outputs[0]["item"]["output"]


def test_malformed_function_arguments_never_kill_the_call(
    db, realtime_enabled, brainless, upstream
):
    fake = upstream["script"](
        [
            _proposal_done_event('["not", "a", "dict"]', call_id="call-bad"),
            {"type": "error", "error": "boom-as-string"},
            {"type": "response.done", "response": "not-a-dict"},
            {"type": "response.output_audio_transcript.delta", "delta": "Still alive."},
        ]
    )
    with client.websocket_connect("/parker/converse/realtime") as ws:
        notice = ws.receive_json()
        assert notice["type"] == "notice"  # the string error became a friendly notice
        follow = ws.receive_json()
        assert follow == {"type": "assistant_transcript_delta", "text": "Still alive."}
        ws.send_json({"type": "end"})

    assert db.query(StagedAction).count() == 0
    outputs = _function_outputs(fake)
    # non-dict arguments are coerced to an empty proposal and rejected
    assert outputs and '"rejected"' in outputs[0]["item"]["output"]


def test_each_bridge_scopes_intents_to_its_own_conversation(db, realtime_enabled):
    """A leftover intent from an earlier live session must never ride onto a
    new session's confirm screen (one shared call log did exactly that)."""

    from app.parker import realtime as rt

    leftover = rt._stage_proposal_sync(
        {
            "action_type": "reminder",
            "label": "old thing",
            "subject": "the leftover thing",
            "intent_text": "remind me about the leftover thing",
        },
        "REALTIME-SESSION-A",
    )
    assert leftover["status"] == "staged"
    before = db.query(StagedAction).count()

    outcome = rt._stage_proposal_sync(
        {
            "action_type": "reminder",
            "label": "new thing",
            "subject": "today's thing",
            "intent_text": "remind me about today's thing",
        },
        "REALTIME-SESSION-B",
    )
    assert outcome["status"] == "staged"
    assert db.query(StagedAction).count() == before + 1  # exactly one new, no drag-along


def test_oversized_proposal_fields_are_bounded(db, realtime_enabled):
    from app.parker import realtime as rt

    outcome = rt._stage_proposal_sync(
        {
            "action_type": "reminder",
            "label": "x" * 500,
            "subject": "s" * 5000,
            "intent_text": "i" * 5000,
        },
        "REALTIME-CAP",
    )
    assert outcome["status"] == "staged"
    action = db.query(StagedAction).one()
    payload = json.loads(action.action_payload)
    assert len(payload["subject"]) <= 200
    assert len(payload["intent_text"]) <= 500


def test_live_line_cap_refuses_a_third_concurrent_call(
    db, realtime_enabled, brainless, upstream
):
    from app.parker import realtime as rt

    upstream["script"]([])
    assert rt.try_acquire_bridge_slot()
    assert rt.try_acquire_bridge_slot()
    try:
        with client.websocket_connect("/parker/converse/realtime") as ws:
            message = ws.receive_json()
        assert message["type"] == "unavailable"
        assert "already running" in message["text"]
    finally:
        rt.release_bridge_slot()
        rt.release_bridge_slot()


def test_brainless_instructions_never_claim_web_search(db, realtime_enabled, brainless):
    instructions = realtime.build_session_update()["session"]["instructions"]
    assert "do NOT have web search" in instructions
    assert "never claim to have looked something up" in instructions
