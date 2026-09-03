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
import base64
import json
import queue
import threading
import time
from typing import Any

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


def browser_frame(ws, expected_type: str, *, working=()) -> dict:
    """Receive the next *expected_type* browser frame, consuming presence
    frames along the way — deliberately.

    The lookup dispatch/finish presence frames (``{"type": "working"}``,
    2026-08-31 Reachy brief) interleave with the frames scenarios pin.
    The deck stays the authority on browser traffic: every consumed
    presence frame must be declared by the caller as a ``(kind, status)``
    pair, so an unexpected frame — presence or otherwise — still fails
    loudly instead of being skipped in a helper.
    """

    expected_working = list(working)
    while True:
        frame = ws.receive_json()
        if frame.get("type") != "working":
            assert frame.get("type") == expected_type, frame
            assert not expected_working, (
                f"expected working frames {expected_working} before {expected_type}"
            )
            return frame
        assert expected_working, f"undeclared working frame: {frame}"
        kind, status = expected_working.pop(0)
        assert (frame.get("kind"), frame.get("status")) == (kind, status), frame


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


_power: dict = {}


@pytest.fixture(autouse=True)
def owned_power():
    """Power is server-authoritative: the lane refuses a socket that does
    not present the owner credentials a power claim issued. Every test
    here is the page that turned Parker on."""

    from app.parker.companion_power import authority

    granted = authority.claim(lambda on: None, client_id="test-page")
    _power["query"] = f"?owner={granted['owner']}&gen={granted['gen']}"
    yield granted
    authority.release(lambda on: None)
    _power.clear()


def live_url() -> str:
    return "/parker/converse/realtime" + _power.get("query", "")


@pytest.fixture(autouse=True)
def realtime_db(db, monkeypatch):
    """Bridge side effects land in the test engine, never the real DB.

    Teardown waits for the bridge (and its worker threads) to finish so
    the next test never inherits a thread still writing to this one's
    database file.
    """

    factory = sessionmaker(bind=db.get_bind())
    monkeypatch.setattr(realtime, "_db_session_factory", factory)
    monkeypatch.setattr(realtime, "HELLO_WAIT_SECONDS", 0.05)  # tests send hello at open or not at all
    yield db
    # Quiescence, not just the slot: threadpool DB threads can outlive a
    # cancelled worker task, and drop_all must never race one.
    _wait_until(
        lambda: realtime._active_bridges == 0 and realtime._inflight_db_threads == 0,
        timeout=3.0,
    )


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
    with client.websocket_connect(live_url()) as ws:
        message = ws.receive_json()
    assert message["type"] == "unavailable"
    assert "OpenAI key" in message["text"]


def test_session_config_carries_persona_vad_transcription_and_tools(
    db, realtime_enabled, brainless, upstream
):
    fake = upstream["script"]([])
    with client.websocket_connect(live_url()) as ws:
        ws.send_json({"type": "audio", "data": "QUJD"})  # valid base64
        ws.send_json({"type": "end"})
        # The test client cancels the handler the moment this block exits;
        # wait for the bridge to have consumed the frames it is judged on.
        assert _wait_until(
            lambda: any(e["type"] == "input_audio_buffer.append" for e in fake.sent)
        )

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
    # brainless -> no web lookup; my_day is local and always offered
    assert [tool["name"] for tool in session["tools"]] == ["propose_action", "my_day"]
    assert "Parkinson" in session["instructions"]
    # Spoken confirmation (companion take 2, 2026-09-01): the model reads
    # the action back and asks for his yes/no — it never tells him to tap.
    assert "yes to do it or no to cancel" in session["instructions"]
    assert "Never tell him to tap" in session["instructions"]
    assert "Right now it is" in session["instructions"]  # local clock grounding
    # the browser's audio chunk was forwarded verbatim
    appended = [e for e in fake.sent if e["type"] == "input_audio_buffer.append"]
    assert appended and appended[0]["audio"] == "QUJD"


def test_brained_session_offers_look_that_up_and_says_so(
    db, realtime_enabled, brained
):
    session = realtime.build_session_update()["session"]
    assert [tool["name"] for tool in session["tools"]] == ["propose_action", "my_day", "look_that_up"]
    # only stageable types are advertised — never a promise that dies at the gate
    enum = session["tools"][0]["parameters"]["properties"]["action_type"]["enum"]
    assert "reminder" in enum and "appointment_note" not in enum
    assert "look_that_up" in session["instructions"]
    assert "do NOT have web search" not in session["instructions"]
    assert "read web addresses aloud" in session["instructions"]  # wraps lines


def test_greeting_is_requested_before_any_audio_arrives(
    db, realtime_enabled, brainless, upstream
):
    fake = upstream["script"]([])
    with client.websocket_connect(live_url()) as ws:
        ws.send_json({"type": "end"})
        assert _wait_until(lambda: _response_creates(fake) >= 1)  # see above
    items = _system_items(fake)
    assert items and "line just opened" in items[0]
    assert _response_creates(fake) >= 1  # the greeting nudge


def _user_items(fake) -> list[str]:
    return [
        e["item"]["content"][0]["text"]
        for e in fake.sent
        if e["type"] == "conversation.item.create"
        and e["item"].get("type") == "message"
        and e["item"].get("role") == "user"
    ]


def test_the_wake_tail_is_a_user_item_never_system_text(db, realtime_enabled, brainless, upstream):
    """"Hey Parker, can you help me": the page's FIRST frame is a hello
    carrying the words after the wake phrase. Those are HIS words —
    untrusted, locally transcribed — so they reach the model as a user
    item, never interpolated into a system instruction (PR #40 review
    blocker 2). The instruction only says his message follows; one nudge
    asks for the reply; the tail is journaled."""

    from app.parker.session_review import RealtimeSessionEvent

    fake = upstream["script"]([])
    with client.websocket_connect(live_url()) as ws:
        ws.send_json({"type": "hello", "tail": "  can you   help me with the tv  "})
        ws.send_json({"type": "end"})
        assert _wait_until(lambda: _response_creates(fake) >= 1)
    items = _system_items(fake)
    assert items and "Skip the standalone greeting" in items[0]
    assert "arrives next as his own message" in items[0]
    assert "line just opened" not in items[0]
    assert not any("can you help me with the tv" in text for text in items), items
    assert _user_items(fake) == ["can you help me with the tv"]
    assert _response_creates(fake) == 1
    # Order on the wire: instruction, then his words, then the one nudge.
    kinds = [
        (e["type"], e.get("item", {}).get("role"))
        for e in fake.sent
        if e["type"] in ("conversation.item.create", "response.create")
    ][:3]
    assert kinds == [
        ("conversation.item.create", "system"),
        ("conversation.item.create", "user"),
        ("response.create", None),
    ], kinds
    assert _wait_until(
        lambda: db.query(RealtimeSessionEvent).filter(RealtimeSessionEvent.kind == "wake_tail").count() == 1
    )
    event = db.query(RealtimeSessionEvent).filter(RealtimeSessionEvent.kind == "wake_tail").one()
    assert event.heard == "can you help me with the tv"


def test_a_pending_hello_waits_for_the_final_tail_before_the_first_reply(
    db, realtime_enabled, brainless, upstream, monkeypatch
):
    """The line opened before the local wake lane finished his sentence:
    the hello says `pending`, and the bridge asks for NO reply until the
    page forwards the lane's final tail — then ONE user item carrying the
    full words, ONE nudge, the full text journaled. A second tail frame is
    ignored (PR #40 review blocker 2: the delayed tail was lost)."""

    from app.parker.session_review import RealtimeSessionEvent

    monkeypatch.setattr(realtime, "TAIL_WAIT_SECONDS", 30.0)  # the frame, not the deadline
    fake = upstream["script"]([])
    with client.websocket_connect(live_url()) as ws:
        ws.send_json({"type": "hello", "tail": "can you", "pending": True})
        assert _wait_until(lambda: any("his own message" in t for t in _system_items(fake)))
        assert _response_creates(fake) == 0, "no reply may be requested before his words"
        assert _user_items(fake) == []
        ws.send_json({"type": "tail", "text": "can you help me with the tv"})
        assert _wait_until(lambda: _response_creates(fake) >= 1)
        ws.send_json({"type": "tail", "text": "a second tail must not re-shape anything"})
        ws.send_json({"type": "audio", "data": base64.b64encode(b"\x00\x00").decode()})
        assert _wait_until(lambda: any(e["type"] == "input_audio_buffer.append" for e in fake.sent))
        ws.send_json({"type": "end"})
    assert _user_items(fake) == ["can you help me with the tv"]
    assert _response_creates(fake) == 1
    assert _wait_until(
        lambda: db.query(RealtimeSessionEvent).filter(RealtimeSessionEvent.kind == "wake_tail").count() == 1
    )
    event = db.query(RealtimeSessionEvent).filter(RealtimeSessionEvent.kind == "wake_tail").one()
    assert event.heard == "can you help me with the tv"


def test_a_pending_hello_falls_back_to_the_hello_tail_at_the_deadline(
    db, realtime_enabled, brainless, upstream, monkeypatch
):
    """The lane's final tail never arrives (dropped lane): after the
    bounded wait the bridge goes with what the hello carried — the user
    item is 'can you', exactly one nudge. With nothing at all, the wake
    instruction alone is nudged (the model asks what he needs) and no
    user item is minted."""

    monkeypatch.setattr(realtime, "TAIL_WAIT_SECONDS", 0.05)
    fake = upstream["script"]([])
    with client.websocket_connect(live_url()) as ws:
        ws.send_json({"type": "hello", "tail": "can you", "pending": True})
        assert _wait_until(lambda: _response_creates(fake) >= 1)
        ws.send_json({"type": "end"})
    assert _user_items(fake) == ["can you"]
    assert _response_creates(fake) == 1

    fake = upstream["script"]([])
    with client.websocket_connect(live_url()) as ws:
        ws.send_json({"type": "hello", "tail": "", "pending": True})
        assert _wait_until(lambda: _response_creates(fake) >= 1)
        ws.send_json({"type": "end"})
    assert _user_items(fake) == []
    assert any("his own message" in t for t in _system_items(fake))
    assert _response_creates(fake) == 1


def test_an_empty_hello_keeps_the_plain_greeting(db, realtime_enabled, brainless, upstream):
    fake = upstream["script"]([])
    with client.websocket_connect(live_url()) as ws:
        ws.send_json({"type": "hello", "tail": ""})
        ws.send_json({"type": "end"})
        assert _wait_until(lambda: _response_creates(fake) >= 1)
    items = _system_items(fake)
    assert items and "line just opened" in items[0]


def test_an_oversized_tail_is_bounded_and_a_late_hello_is_ignored(
    db, realtime_enabled, brainless, upstream, monkeypatch
):
    monkeypatch.setattr(realtime, "HELLO_WAIT_SECONDS", 0.05)
    fake = upstream["script"]([])
    with client.websocket_connect(live_url()) as ws:
        ws.send_json({"type": "hello", "tail": "word " * 200})
        ws.send_json({"type": "hello", "tail": "a second hello must not re-shape anything"})
        assert _wait_until(lambda: _system_items(fake))  # the client cancels the handler on exit
        ws.send_json({"type": "end"})
        assert _wait_until(lambda: _response_creates(fake) >= 1)
    users = _user_items(fake)
    assert len(users) == 1 and len(users[0]) <= realtime.MAX_WAKE_TAIL_CHARS  # 200-char tail cap
    assert "second hello" not in users[0]
    assert not any("second hello" in text for text in _system_items(fake))


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
    with client.websocket_connect(live_url()) as ws:
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
    with client.websocket_connect(live_url()) as ws:
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
    with client.websocket_connect(live_url()) as ws:
        staged_note = ws.receive_json()
        assert staged_note["type"] == "proposal_staged"
        assert "water the plants" in staged_note["label"]
        assert _wait_until(lambda: _function_outputs(fake))  # the client cancels the handler on exit
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
    with client.websocket_connect(live_url()) as ws:
        ws.send_json({"type": "stop"})  # provoke traffic so we can end cleanly
        assert ws.receive_json() == {"type": "clear"}
        # The test client cancels the handler the moment this block exits:
        # wait for the ack we are about to judge (CI flake on PR #43).
        assert _wait_until(lambda: _function_outputs(fake))
        ws.send_json({"type": "end"})

    assert db.query(StagedAction).count() == 0
    outputs = _function_outputs(fake)
    assert outputs and "not allowed" in outputs[0]["item"]["output"]


def _propose_event(call_id="call-1", **overrides):
    arguments = {
        "action_type": "reminder",
        "label": "a reminder to water the plants",
        "subject": "water the plants",
        "intent_text": "remind me to water the plants",
    }
    arguments.update(overrides)
    return {
        "type": "response.done",
        "response": {
            "output": [
                {
                    "type": "function_call",
                    "name": "propose_action",
                    "call_id": call_id,
                    "arguments": json.dumps(arguments),
                }
            ]
        },
    }


def _heard(text):
    return {
        "type": "conversation.item.input_audio_transcription.completed",
        "transcript": text,
    }


# ---------------------------------------------------------------------------
# Spoken confirmation (companion take 2, 2026-09-01): his voice is the whole
# interface — a staged offer resolves on the SAME deterministic yes/no
# grammar the turns lane executes on. No taps, no model-decided execution.
# ---------------------------------------------------------------------------


def test_action_readback_says_exactly_what_would_run():
    """The read-back line is the spoken contract; every shape is pinned
    (the deck's assert_staged relies on these for readback content)."""

    assert realtime._action_readback(
        {"action_type": "reminder", "subject": "water the plants",
         "recipient": "", "intent_text": "remind me"}
    ) == "a reminder about “water the plants”"
    assert realtime._action_readback(
        {"action_type": "family_message", "subject": "Sunday visit",
         "recipient": "Sarah", "intent_text": "the park sounds lovely"}
    ) == "a message to Sarah saying “the park sounds lovely”"
    assert realtime._action_readback(
        {"action_type": "exercise_start", "subject": "morning stretches",
         "recipient": "", "intent_text": ""}
    ) == "starting morning stretches"
    assert realtime._action_readback(
        {"action_type": "media_playlist", "subject": "old Hindi songs",
         "recipient": "", "intent_text": ""}
    ) == "media_playlist: old Hindi songs"


def test_spoken_yes_confirms_and_executes_the_staged_action(
    db, realtime_enabled, brainless, upstream
):
    fake = upstream["script"]([_propose_event()])
    with client.websocket_connect(live_url()) as ws:
        staged_note = ws.receive_json()
        assert staged_note["type"] == "proposal_staged"
        assert "water the plants" in staged_note["readback"]
        fake.feed(_heard("yes"))
        assert ws.receive_json() == {"type": "user_transcript", "text": "yes"}
        result = ws.receive_json()
        assert result == {
            "type": "action_result",
            "status": "executed",
            "label": "a reminder to water the plants",
        }
        ws.send_json({"type": "end"})

    action = db.query(StagedAction).one()
    assert action.status == "executed"
    assert action.confirmed_by == "patient"
    # The model is told the truth so it can say it aloud.
    assert any("executed exactly as read back" in text for text in _system_items(fake))


def test_spoken_no_cancels_and_never_executes(
    db, realtime_enabled, brainless, upstream
):
    fake = upstream["script"]([_propose_event()])
    with client.websocket_connect(live_url()) as ws:
        assert ws.receive_json()["type"] == "proposal_staged"
        fake.feed(_heard("no, not now"))
        assert ws.receive_json()["type"] == "user_transcript"
        result = ws.receive_json()
        assert result["type"] == "action_result" and result["status"] == "cancelled"
        ws.send_json({"type": "end"})

    action = db.query(StagedAction).one()
    assert action.status == "cancelled"
    assert action.cancelled_by == "patient"
    assert any("He said no" in text for text in _system_items(fake))


def test_ambiguous_speech_defers_and_a_later_yes_still_executes(
    db, realtime_enabled, brainless, upstream
):
    """'yes one', a question, or ordinary talk is NOT an answer: nothing
    executes, nothing cancels, and the offer stays open for its window."""

    fake = upstream["script"]([_propose_event()])
    with client.websocket_connect(live_url()) as ws:
        assert ws.receive_json()["type"] == "proposal_staged"
        for utterance in ("yes one", "what time is it again", "hmm"):
            fake.feed(_heard(utterance))
            assert ws.receive_json()["type"] == "user_transcript"
        action = db.query(StagedAction).one()
        db.refresh(action)
        assert action.status == "staged"  # deferred, untouched
        fake.feed(_heard("yes please"))
        assert ws.receive_json()["type"] == "user_transcript"
        assert ws.receive_json()["status"] == "executed"
        ws.send_json({"type": "end"})


def test_the_offer_expires_and_a_late_yes_executes_nothing(
    db, realtime_enabled, brainless, upstream, monkeypatch
):
    monkeypatch.setattr(realtime, "CONFIRM_WINDOW_SECONDS", 0.2)
    fake = upstream["script"]([_propose_event()])
    with client.websocket_connect(live_url()) as ws:
        assert ws.receive_json()["type"] == "proposal_staged"
        # The watchdog expires the offer; the card clears honestly.
        expired = ws.receive_json()
        assert expired == {
            "type": "action_result",
            "status": "expired",
            "label": "a reminder to water the plants",
        }
        fake.feed(_heard("yes"))
        assert ws.receive_json()["type"] == "user_transcript"
        ws.send_json({"type": "end"})

    action = db.query(StagedAction).one()
    assert action.status == "staged"  # still waiting on the family surface
    assert action.confirmed_at is None


def test_a_mutated_action_fails_closed_on_spoken_yes(
    db, realtime_enabled, brainless, upstream
):
    """The yes binds to the read-back contract: a row that changed between
    offer and yes is cancelled and reported failed — never executed."""

    fake = upstream["script"]([_propose_event()])
    with client.websocket_connect(live_url()) as ws:
        assert ws.receive_json()["type"] == "proposal_staged"

        def mutate() -> bool:
            # The bridge's journal threads share the harness connection; a
            # mid-cursor collision surfaces as OperationalError — retry
            # until the write lands (shared-connection artifact).
            try:
                action = db.query(StagedAction).one()
                payload = json.loads(action.action_payload)
                payload["subject"] = "send money somewhere"
                action.action_payload = json.dumps(payload)
                db.commit()
                return True
            except Exception:  # noqa: BLE001
                db.rollback()
                return False

        assert _wait_until(mutate)
        fake.feed(_heard("yes"))
        assert ws.receive_json()["type"] == "user_transcript"
        result = ws.receive_json()
        assert result["type"] == "action_result" and result["status"] == "failed"
        ws.send_json({"type": "end"})

    db.expire_all()
    action = db.query(StagedAction).one()
    assert action.status == "cancelled"
    assert action.cancelled_by == "confirmation_contract_mismatch"
    assert any("did NOT run" in text for text in _system_items(fake))


def test_a_newer_offer_replaces_the_old_one_unambiguously(
    db, realtime_enabled, brainless, upstream
):
    fake = upstream["script"](
        [
            _propose_event(call_id="call-1"),
            _propose_event(
                call_id="call-2",
                label="a reminder to stretch",
                subject="stretch",
                intent_text="remind me to stretch",
            ),
        ]
    )
    with client.websocket_connect(live_url()) as ws:
        assert "water the plants" in ws.receive_json()["label"]
        assert "stretch" in ws.receive_json()["label"]
        fake.feed(_heard("yes"))
        assert ws.receive_json()["type"] == "user_transcript"
        result = ws.receive_json()
        assert result["status"] == "executed"
        assert result["label"] == "a reminder to stretch"  # the NEWEST offer
        ws.send_json({"type": "end"})

    executed = [a for a in db.query(StagedAction).all() if a.status == "executed"]
    assert len(executed) == 1
    assert "stretch" in executed[0].action_payload


def test_stop_cancels_upstream_and_junk_audio_never_forwards(
    db, realtime_enabled, brainless, upstream
):
    fake = upstream["script"]([])
    with client.websocket_connect(live_url()) as ws:
        ws.send_json({"type": "audio", "data": "not-base64!"})
        ws.send_json({"type": "stop"})
        assert ws.receive_json() == {"type": "clear"}
        ws.send_json({"type": "end"})

    assert not any(e["type"] == "input_audio_buffer.append" for e in fake.sent)
    assert any(e["type"] == "response.cancel" for e in fake.sent)


def test_barge_in_flushes_playback(db, realtime_enabled, brainless, upstream):
    upstream["script"]([{"type": "input_audio_buffer.speech_started"}])
    with client.websocket_connect(live_url()) as ws:
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
    def mirrored() -> bool:
        db.expire_all()
        state = get_screen_state(db)
        return state is not None and state.heard == "what's the weather"

    with client.websocket_connect(live_url()) as ws:
        ws.receive_json()  # user_transcript
        ws.receive_json()  # assistant delta
        # The mirror write rides a threadpool thread that shutdown does not
        # wait for — hang up only once the row is provably there, or the
        # test's read races the commit (heard and speech land in one write).
        assert _wait_until(mirrored)
        ws.send_json({"type": "end"})

    state = get_screen_state(db)
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
    with client.websocket_connect(live_url()) as ws:
        fake.feed(_look_done_event("when does Alcaraz play the US Open?"))
        assert _wait_until(lambda: _function_outputs(fake))
        ack = json.loads(_function_outputs(fake)[0]["item"]["output"])
        assert ack["status"] == "working"
        assert "keep the conversation going" in ack["detail"].lower()
        # The page hears about the dispatch the moment it is real — the
        # presence frame the Reachy scene animates "checking" from.
        assert ws.receive_json() == {
            "type": "working", "kind": "search", "status": "started"
        }
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

        # The finished lookup closes its presence pair, then the browser
        # gets the evidence chips.
        assert ws.receive_json() == {
            "type": "working", "kind": "search", "status": "done"
        }
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
    with client.websocket_connect(live_url()) as ws:
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
        # One worker, one presence pair: the duplicate ask must not have
        # queued a second `started` claim — the frames arrive strictly as
        # started (dispatch) then done (delivery), nothing between.
        assert ws.receive_json() == {
            "type": "working", "kind": "search", "status": "started"
        }
        release.set()
        assert ws.receive_json() == {
            "type": "working", "kind": "search", "status": "done"
        }
        ws.send_json({"type": "end"})


def test_failed_lookup_injects_an_honest_note(
    db, realtime_enabled, brained, upstream, monkeypatch
):
    def exploding_search(question):
        raise RuntimeError("boom")

    monkeypatch.setattr(realtime_workers, "run_search_worker", exploding_search)
    fake = upstream["script"]([])
    with client.websocket_connect(live_url()) as ws:
        fake.feed(_look_done_event("what's on at the cinema?"))
        assert _wait_until(
            lambda: any("could not finish" in text for text in _system_items(fake))
        )
        note = next(text for text in _system_items(fake) if "could not finish" in text)
        assert "what's on at the cinema?" in note
        assert "offer to try again" in note
        # The presence pair is honest too: started, then FAILED — the scene
        # must never keep claiming work, and must never claim it succeeded.
        assert ws.receive_json() == {
            "type": "working", "kind": "search", "status": "started"
        }
        assert ws.receive_json() == {
            "type": "working", "kind": "search", "status": "failed"
        }
        ws.send_json({"type": "end"})


def test_working_started_commits_before_an_instant_result(
    db, realtime_enabled, brained, upstream, monkeypatch
):
    """The reviewer reproduced `done` overtaking `started` with a worker
    that finishes instantly (independent review, 2026-09-01): dispatch must
    COMMIT the started frame to the browser before the worker is spawned,
    so start-before-terminal holds for instant success too."""

    def instant_search(question):
        return WorkerResult(kind="search", question=question, speech="knew it already")

    monkeypatch.setattr(realtime_workers, "run_search_worker", instant_search)
    upstream["script"]([])
    with client.websocket_connect(live_url()) as ws:
        ws.send_json({"type": "audio", "data": "QUJD"})  # prove the line is up
        fake = upstream["fake"]
        fake.feed(_look_done_event("instant one?"))
        first = ws.receive_json()
        second = ws.receive_json()
        assert (first["type"], first["status"]) == ("working", "started")
        assert (second["type"], second["status"]) == ("working", "done")
        ws.send_json({"type": "end"})


def test_working_started_commits_before_an_instant_failure(
    db, realtime_enabled, brained, upstream, monkeypatch
):
    def exploding_search(question):
        raise RuntimeError("boom")

    monkeypatch.setattr(realtime_workers, "run_search_worker", exploding_search)
    upstream["script"]([])
    with client.websocket_connect(live_url()) as ws:
        upstream["fake"].feed(_look_done_event("doomed one?"))
        first = ws.receive_json()
        second = ws.receive_json()
        assert (first["type"], first["status"]) == ("working", "started")
        assert (second["type"], second["status"]) == ("working", "failed")
        ws.send_json({"type": "end"})


def test_lookup_cancelled_by_close_never_reports_a_late_result(
    db, realtime_enabled, brained, upstream, monkeypatch
):
    """Close cancels in-flight workers: `started` was honestly sent, and
    nothing may claim done/inject after the session is over (the page's
    own work TTL clears the stale claim; late results drop by policy)."""

    release = threading.Event()

    def gated_search(question):
        release.wait(timeout=3)
        return WorkerResult(kind="search", question=question, speech="too late")

    monkeypatch.setattr(realtime_workers, "run_search_worker", gated_search)
    fake = upstream["script"]([])
    with client.websocket_connect(live_url()) as ws:
        fake.feed(_look_done_event("slow one?"))
        assert ws.receive_json() == {
            "type": "working", "kind": "search", "status": "started"
        }
        ws.send_json({"type": "end"})
    release.set()
    assert _wait_until(lambda: realtime._active_bridges == 0)
    assert not any("LOOKUP RESULT" in text for text in _system_items(fake))


def test_audio_bearing_response_gets_an_authoritative_done_frame(
    db, realtime_enabled, brainless, upstream
):
    """The page may claim listening only after the provider response is
    done AND its scheduled playback drained — never from a gap in the
    local chunk queue (independent review, 2026-09-01). The done frame
    arrives in-order after the response's last forwarded audio chunk."""

    upstream["script"](
        [
            {"type": "response.output_audio.delta", "delta": "UENN"},
            {"type": "response.done", "response": {"output": []}},
        ]
    )
    with client.websocket_connect(live_url()) as ws:
        assert ws.receive_json() == {"type": "audio", "data": "UENN"}
        assert ws.receive_json() == {"type": "response_state", "status": "done"}
        ws.send_json({"type": "end"})


def test_audioless_responses_send_no_response_state_frame(
    db, realtime_enabled, brainless, upstream
):
    """Function-call/empty responses never opened an audio epoch page-side;
    a done frame for them would be undeclared noise for the deck."""

    upstream["script"](
        [
            {"type": "response.done", "response": {"output": []}},  # audioless
            {"type": "response.output_audio.delta", "delta": "UENN"},
            {"type": "response.done", "response": {"output": []}},
        ]
    )
    with client.websocket_connect(live_url()) as ws:
        # Nothing for the audioless done — the next frame is the audio.
        assert ws.receive_json() == {"type": "audio", "data": "UENN"}
        assert ws.receive_json() == {"type": "response_state", "status": "done"}
        ws.send_json({"type": "end"})


def test_guard_tripped_response_still_closes_its_audio_epoch(
    db, realtime_enabled, brainless, upstream
):
    """Audio reached the browser before the guard tripped: the cancelled
    response's done still closes the epoch, so guard TTS draining can
    hand the scene back to listening truthfully."""

    upstream["script"](
        [
            {"type": "response.output_audio.delta", "delta": "UENN"},
            {"type": "response.output_audio_transcript.delta", "delta": "Maybe try "},
            {
                "type": "response.output_audio_transcript.delta",
                "delta": "taking an extra 50 mg tonight.",
            },
            {"type": "response.done", "response": {"output": []}},
        ]
    )
    with client.websocket_connect(live_url()) as ws:
        assert ws.receive_json() == {"type": "audio", "data": "UENN"}
        assert ws.receive_json()["type"] == "assistant_transcript_delta"
        assert ws.receive_json() == {"type": "clear"}
        assert ws.receive_json()["type"] == "guard_redirect"
        assert ws.receive_json() == {"type": "response_state", "status": "done"}
        ws.send_json({"type": "end"})


def test_expression_transitions_journal_bounded_and_allowlisted(
    db, realtime_enabled, brainless, upstream, monkeypatch
):
    """The page reports what Reachy showed; the bridge journals it for
    session review — allowlisted fields only, typed, truncated, capped
    (independent review, 2026-09-01)."""

    from app.parker.session_review import RealtimeSessionEvent

    monkeypatch.setattr(realtime, "MAX_EXPRESSION_RECEIPTS", 3)

    def journaled() -> int:
        # Polled from the test thread while bridge threads may hold the
        # shared harness connection — a transient refusal reads as
        # not-yet, never as a failure.
        try:
            db.expire_all()
            return (
                db.query(RealtimeSessionEvent)
                .filter(RealtimeSessionEvent.kind == "expression")
                .count()
            )
        except Exception:  # noqa: BLE001
            return -1

    upstream["script"]([])
    with client.websocket_connect(live_url()) as ws:
        for i in range(5):
            ws.send_json(
                {
                    "type": "expression",
                    "at_ms": 100.5 + i,
                    "gen": 1,
                    "from": "listening",
                    "to": "talking",
                    "reason": "assistant_audio",
                    "work": "",
                    "action": "none",
                    "guard": "none",
                    "attention": "x" * 100,  # truncated, never stored raw
                    "junk": "y" * 500,  # never journaled
                    "nested": {"a": 1},  # never journaled
                }
            )
        # Receipts are best-effort by design: an abrupt hang-up may drop
        # the in-flight tail (the page's beacon lane carries it instead).
        # Wait for the cap to land BEFORE closing — the CI runner caught
        # this test hanging up mid-write (2026-09-01).
        assert _wait_until(lambda: journaled() >= 3)
        ws.send_json({"type": "end"})
    assert _wait_until(
        lambda: realtime._active_bridges == 0 and realtime._inflight_db_threads == 0
    )
    db.expire_all()
    rows = (
        db.query(RealtimeSessionEvent)
        .filter(RealtimeSessionEvent.kind == "expression")
        .order_by(RealtimeSessionEvent.seq)
        .all()
    )
    assert len(rows) == 3  # capped, later transitions dropped
    details = [json.loads(row.detail) for row in rows]
    assert details[0]["from"] == "listening" and details[0]["to"] == "talking"
    assert details[0]["at_ms"] == 100 and details[0]["gen"] == 1
    assert len(details[0]["attention"]) == 32
    assert "junk" not in details[0] and "nested" not in details[0]
    assert details[-1].get("truncated") is True  # the cap is visible to review


def test_lookup_without_a_brain_is_honestly_unavailable(
    db, realtime_enabled, brainless, upstream
):
    """The tool isn't offered brainless — but a model may still call it."""

    fake = upstream["script"]([_look_done_event("anything at all?")])
    with client.websocket_connect(live_url()) as ws:
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
    with client.websocket_connect(live_url()) as ws:
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
    with client.websocket_connect(live_url()) as ws:
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
    with client.websocket_connect(live_url()) as ws:
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
    with client.websocket_connect(live_url()) as ws:
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
    with client.websocket_connect(live_url()) as ws:
        closing = ws.receive_json()  # forced by the watchdog floor
        assert closing == {"type": "closing"}


def test_a_word_from_him_stands_the_wrapup_down(
    db, realtime_enabled, brainless, upstream, monkeypatch
):
    monkeypatch.setattr(realtime, "IDLE_WRAPUP_SECONDS", 0.2)
    monkeypatch.setattr(realtime, "IDLE_GOODBYE_SECONDS", 30.0)  # never in this test
    monkeypatch.setattr(realtime, "_WATCHDOG_TICK_SECONDS", 0.05)
    fake = upstream["script"]([])
    with client.websocket_connect(live_url()) as ws:
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
    with client.websocket_connect(live_url()) as ws:
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
    with client.websocket_connect(live_url()) as ws:
        ws.send_json({"type": "end"})

    time.sleep(0.2)  # let the finalize threadpool settle
    assert db.query(ConversationMemory).count() == 0
    call = db.query(CallLog).filter(CallLog.call_type == "realtime").one()
    assert call.summary is None  # the eager row exists; nothing was invented
    assert call.ended_at is not None  # …but the session still honestly ENDED
    # (verifier find: the review feed's live flag derives from ended_at, and
    # an accidental tap must not read as a live conversation forever)


def test_unanswered_last_word_is_journaled_past_the_exchange_cap(
    db, realtime_enabled, brainless
):
    """The exchange list is a bounded memory cap; the journal is not. His
    unanswered last words at hang-up must reach the review timeline even
    when the session already filled all fifty tracked exchanges
    (verifier find: the dangling journal was gated behind the cap).
    """

    async def scenario():
        async def send_json(message):
            pass

        async def receive_json():
            await asyncio.Event().wait()

        bridge = realtime.RealtimeBridge(send_json, receive_json)
        bridge._exchanges = [
            (f"tell me about question {i}", "ok") for i in range(realtime._MAX_TRACKED_EXCHANGES)
        ]
        bridge._user_transcript = "these are my last words"
        await bridge._shutdown()
        return bridge

    bridge = asyncio.run(scenario())
    assert len(bridge._exchanges) == realtime._MAX_TRACKED_EXCHANGES  # cap held
    from app.parker.session_review import RealtimeSessionEvent

    db.expire_all()
    event = db.query(RealtimeSessionEvent).one()
    assert event.kind == "turn"
    assert event.heard == "these are my last words"
    assert json.loads(event.detail)["dangling"] is True


def test_a_turn_cancelled_mid_mirror_still_reaches_the_journal(
    db, realtime_enabled, brainless, monkeypatch
):
    """Cancellation landing on the screen-mirror await must not eat the
    turn's journal row: the summary counts the exchange, so the review
    timeline must show it too (verifier find, executed repro: summary said
    '1 exchange(s)' while the timeline had zero turns). Shutdown flushes
    the stashed turn writer; the (call, seq) idempotency guard keeps a
    just-completed write from doubling.
    """

    mirror_started = threading.Event()

    def slow_mirror(heard, speech):
        mirror_started.set()
        time.sleep(0.2)

    monkeypatch.setattr(realtime, "_record_exchange_sync", slow_mirror)
    monkeypatch.setattr(
        realtime_workers,
        "run_context_worker",
        lambda make_db, sources=None: WorkerResult(kind="context", question="", speech=""),
    )
    fake = FakeUpstream(
        [
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "what day is the tennis on this week",
            },
            {"type": "response.output_audio_transcript.delta", "delta": "Saturday."},
            {"type": "response.done", "response": {"output": []}},
        ]
    )

    async def connect():
        return fake

    monkeypatch.setattr(realtime, "connect_openai", connect)

    async def scenario():
        sent: list[dict] = []

        async def send_json(message):
            sent.append(message)

        async def receive_json():
            await asyncio.Event().wait()

        bridge = realtime.RealtimeBridge(send_json, receive_json)
        task = asyncio.create_task(bridge.run())
        assert await asyncio.to_thread(mirror_started.wait, 3.0)
        while not task.done():  # anyio-style cancel storm, mid-mirror
            task.cancel()
            await asyncio.sleep(0)
        return bridge

    bridge = asyncio.run(scenario())
    from app.parker.session_review import RealtimeSessionEvent

    db.expire_all()
    turns = (
        db.query(RealtimeSessionEvent)
        .filter(RealtimeSessionEvent.kind == "turn")
        .all()
    )
    assert len(turns) == 1  # journaled exactly once — flushed, not doubled
    assert turns[0].heard == "what day is the tennis on this week"
    assert turns[0].said == "Saturday."
    call = db.query(CallLog).filter(CallLog.call_sid == bridge._call_sid).one()
    assert "1 exchange(s)" in (call.summary or "")  # summary and journal agree


def test_finalize_survives_a_transient_local_write_refusal(db, monkeypatch):
    """The local SQLite is shared — other Parker processes hold the file DB,
    and the test harness shares one in-memory connection across threads —
    so a finalize can hit a transient refusal. It is the session's only
    durable record: one refusal must cost a retry, never the record (CI
    reproduced the silent loss on the shared connection, 2026-08-31).
    """

    factory = sessionmaker(bind=db.get_bind())
    attempts = {"n": 0}

    def flaky_factory():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("database is locked")
        return factory()

    monkeypatch.setattr(realtime, "_db_session_factory", flaky_factory)
    realtime._finalize_session_sync(
        "REALTIME-flaky", [("when does Alcaraz play next", "Friday night.")]
    )
    db.expire_all()
    call = db.query(CallLog).filter(CallLog.call_sid == "REALTIME-flaky").one()
    assert call.ended_at is not None  # the retry landed the record
    assert db.query(ConversationMemory).count() == 1


def test_finalize_rerun_never_mints_a_second_topic_memory(db):
    """Retries reopen their own session, so a re-run after a committed
    attempt must be idempotent — one session, one topic memory, always
    (the deck pins the one-memory-per-session contract).
    """

    exchanges = [("when does Alcaraz play next", "Friday night.")]
    realtime._finalize_session_sync("REALTIME-twice", exchanges)
    realtime._finalize_session_sync("REALTIME-twice", exchanges)
    db.expire_all()
    assert db.query(ConversationMemory).count() == 1


def test_upstream_closes_before_any_shutdown_write(
    db, realtime_enabled, brainless, monkeypatch
):
    """Off means off (PR #40 review blocker 1): the billed upstream socket
    closes BEFORE the dangling-turn journal and the session finalize —
    persistence may drain afterwards but never holds the boundary open.
    A deliberately slow finalize used to keep the OpenAI session alive for
    its whole write window."""

    fake = FakeUpstream(
        [
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "when does Alcaraz play next",
            }
        ]
    )

    async def connect():
        return fake

    monkeypatch.setattr(realtime, "connect_openai", connect)
    monkeypatch.setattr(
        realtime_workers,
        "run_context_worker",
        lambda make_db, **_: WorkerResult(kind="context", question="", speech=""),
    )
    seen: list[tuple[str, bool]] = []
    real_retries = realtime._with_local_write_retries

    def recording_retries(label, write):
        seen.append((label, fake.closed))  # was the upstream already closed?
        if label == "session finalize":
            time.sleep(0.3)  # a slow local write must not delay the hang-up
        real_retries(label, write)

    monkeypatch.setattr(realtime, "_with_local_write_retries", recording_retries)

    async def scenario():
        sent: list[dict] = []
        hung_up = asyncio.Event()

        async def send_json(message):
            sent.append(message)

        async def receive_json():
            await hung_up.wait()
            return {"type": "end"}

        bridge = realtime.RealtimeBridge(send_json, receive_json)
        task = asyncio.create_task(bridge.run())
        deadline = time.monotonic() + 3.0
        while not any(m.get("type") == "user_transcript" for m in sent):
            assert time.monotonic() < deadline, "transcript never reached the browser"
            await asyncio.sleep(0.01)
        hung_up.set()  # the page hangs up with his last words unanswered
        await task
        return bridge

    asyncio.run(scenario())
    shutdown_writes = [(label, closed) for label, closed in seen if label != "eager call log"]
    labels = [label for label, _ in shutdown_writes]
    assert "session finalize" in labels and "session event" in labels, seen
    assert all(closed for _, closed in shutdown_writes), seen  # every shutdown write ran after the close
    assert fake.closed


def test_shutdown_leaves_no_bridge_task_or_frame_behind(
    db, realtime_enabled, brainless, brained, monkeypatch
):
    """Under a cancellation storm asyncio.wait never cancelled the pumps:
    upstream events kept being forwarded, and a lookup could still be
    spawned, AFTER run() returned (PR #40 review blocker 1). Now shutdown
    revokes first: no frame, no injection, no worker after off."""

    fake = FakeUpstream(
        [
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "when does Alcaraz play next",
            }
        ]
    )

    async def connect():
        return fake

    monkeypatch.setattr(realtime, "connect_openai", connect)
    monkeypatch.setattr(
        realtime_workers,
        "run_context_worker",
        lambda make_db, **_: WorkerResult(kind="context", question="", speech=""),
    )
    searches: list[str] = []

    def spy_search(question, **_):
        searches.append(question)
        return WorkerResult(kind="search", question=question, speech="late")

    monkeypatch.setattr(realtime_workers, "run_search_worker", spy_search)

    async def scenario():
        sent: list[dict] = []
        never = asyncio.Event()

        async def send_json(message):
            sent.append(message)

        async def receive_json():
            await never.wait()
            return {"type": "end"}

        bridge = realtime.RealtimeBridge(send_json, receive_json)
        task = asyncio.create_task(bridge.run())
        deadline = time.monotonic() + 3.0
        while not any(m.get("type") == "user_transcript" for m in sent):
            assert time.monotonic() < deadline, "transcript never reached the browser"
            await asyncio.sleep(0.01)
        while not task.done():  # anyio-style: a fresh cancel at every await
            task.cancel()
            await asyncio.sleep(0)
        assert task.cancelled()
        frames_before = len(sent)
        # Late upstream traffic after off: speech and a tool call.
        fake.feed({"type": "response.output_audio_transcript.delta", "delta": "late words"})
        fake.feed(_look_done_event("slow one?"))
        await asyncio.sleep(0.3)
        alive = [
            t for t in asyncio.all_tasks()
            if t is not asyncio.current_task() and "RealtimeBridge" in repr(t.get_coro())
        ]
        return bridge, sent[frames_before:], alive

    bridge, late_frames, alive = asyncio.run(scenario())
    assert bridge._closed
    assert late_frames == [], late_frames
    assert alive == [], alive
    assert searches == []
    assert not any("LOOKUP RESULT" in text for text in _system_items(fake))
    assert fake.closed


def test_cancellation_storm_never_outruns_the_finalize_write(
    db, realtime_enabled, brainless, monkeypatch
):
    """The websocket layer (anyio) re-cancels at every await, so a one-shot
    shielded await skips its shutdown step with the finalize write still in
    flight — the bridge slot released while the write raced the assertions
    (the gauntlet's "unreproduced full-suite blip", reproduced on CI
    2026-08-31). Pin: when run() returns under a cancellation storm, a
    deliberately slow finalize has already landed — ended_at, summary, and
    the topic memory together (the finalize commit is atomic).
    """

    fake = FakeUpstream(
        [
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "when does Alcaraz play next",
            }
        ]
    )

    async def connect():
        return fake

    monkeypatch.setattr(realtime, "connect_openai", connect)

    real_finalize = realtime._finalize_session_sync

    def slow_finalize(call_sid, exchanges):
        time.sleep(0.25)  # a busy CI runner's threadpool lag
        real_finalize(call_sid, exchanges)

    monkeypatch.setattr(realtime, "_finalize_session_sync", slow_finalize)
    # This test is about finalize, not context: a stubbed context worker
    # leaves no DB-touching thread behind to race a later test's teardown
    # (threadpool work outlives a cancelled task and this test's own loop).
    monkeypatch.setattr(
        realtime_workers,
        "run_context_worker",
        lambda make_db: WorkerResult(kind="context", question="", speech=""),
    )

    async def scenario():
        sent: list[dict] = []
        browser_hung = asyncio.Event()

        async def send_json(message):
            sent.append(message)

        async def receive_json():
            await browser_hung.wait()  # the browser never speaks; we cancel
            return {"type": "end"}

        bridge = realtime.RealtimeBridge(send_json, receive_json)
        task = asyncio.create_task(bridge.run())
        deadline = time.monotonic() + 3.0
        while not any(m.get("type") == "user_transcript" for m in sent):
            assert time.monotonic() < deadline, "transcript never reached the browser"
            await asyncio.sleep(0.01)
        while not task.done():  # anyio-style: a fresh cancel at every await
            task.cancel()
            await asyncio.sleep(0)
        assert task.cancelled()
        return bridge

    bridge = asyncio.run(scenario())
    # No waiting here — run() returning IS the contract being pinned.
    db.expire_all()
    call = db.query(CallLog).filter(CallLog.call_sid == bridge._call_sid).one()
    assert call.ended_at is not None
    assert "Alcaraz" in (call.summary or "")
    memory = db.query(ConversationMemory).one()
    assert memory.source == "realtime"


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
    with client.websocket_connect(live_url()) as ws:
        ws.send_json({"type": "stop"})
        assert ws.receive_json() == {"type": "clear"}
        assert _wait_until(lambda: _function_outputs(fake))  # the client cancels the handler on exit
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
    with client.websocket_connect(live_url()) as ws:
        ws.send_json({"type": "stop"})
        assert ws.receive_json() == {"type": "clear"}
        assert _wait_until(lambda: _function_outputs(fake))  # the client cancels the handler on exit
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
    with client.websocket_connect(live_url()) as ws:
        notice = ws.receive_json()
        assert notice["type"] == "notice"  # the string error became a friendly notice
        follow = ws.receive_json()
        assert follow == {"type": "assistant_transcript_delta", "text": "Still alive."}
        assert _wait_until(lambda: _function_outputs(fake))  # the client cancels the handler on exit
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
        with client.websocket_connect(live_url()) as ws:
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


# ---------------------------------------------------------------------------
# my_day: his own day from Parker's records, never the web (session 3,
# call 41: "what do I have today" went to search, which had no calendar).
# ---------------------------------------------------------------------------


def _my_day_event(call_id="day-1", about="today"):
    return {
        "type": "response.done",
        "response": {
            "output": [
                {
                    "type": "function_call",
                    "name": "my_day",
                    "call_id": call_id,
                    "arguments": json.dumps({"about": about}),
                }
            ]
        },
    }


def test_my_day_is_offered_without_a_brain_and_the_prompt_steers_to_it(
    db, realtime_enabled, brainless
):
    update = realtime.build_session_update()["session"]
    names = [tool["name"] for tool in update["tools"]]
    assert "my_day" in names and "look_that_up" not in names
    assert "call my_day" in update["instructions"]
    assert "there is no calendar" in update["instructions"]


def test_my_day_answers_from_local_records_never_a_dose(
    db, realtime_enabled, brainless, upstream
):
    from app.db.models import Medication
    from app.memory.store import save_memory

    db.add(Medication(name="Sinemet", dosage="25-100 mg", schedule_times='["08:00", "14:00", "20:00"]', active=True))
    db.commit()
    save_memory(db, "Sarah moved the neurologist appointment to Friday at two.", "event")
    fake = upstream["script"]([_my_day_event()])
    with client.websocket_connect(live_url()) as ws:
        assert ws.receive_json() == {"type": "working", "kind": "my_day", "status": "started"}
        assert ws.receive_json() == {"type": "working", "kind": "my_day", "status": "done"}
        assert _wait_until(lambda: any("Sinemet" in i for i in _system_items(fake)))
        ws.send_json({"type": "end"})
    outputs = _function_outputs(fake)
    assert outputs and json.loads(outputs[0]["item"]["output"])["status"] == "working"
    note = next(i for i in _system_items(fake) if "Sinemet" in i)
    assert "8 AM, 2 PM and 8 PM" in note
    assert "25-100" not in note and "mg" not in note  # names and times only
    assert "neurologist appointment" in note
    assert "no calendar" in note
    assert "Right now it is" in note


def test_my_day_with_nothing_on_record_says_so(db, realtime_enabled, brainless, upstream):
    fake = upstream["script"]([_my_day_event()])
    with client.websocket_connect(live_url()) as ws:
        assert ws.receive_json()["type"] == "working"
        assert ws.receive_json() == {"type": "working", "kind": "my_day", "status": "done"}
        assert _wait_until(lambda: any("Nothing is on record" in i for i in _system_items(fake)))
        ws.send_json({"type": "end"})


def test_my_day_lists_the_reminder_he_set(db, realtime_enabled, brainless, upstream):
    fake = upstream["script"]([_propose_event(call_id="p1")])
    with client.websocket_connect(live_url()) as ws:
        assert ws.receive_json()["type"] == "proposal_staged"
        fake.feed(_heard("yes"))
        assert ws.receive_json()["type"] == "user_transcript"
        assert ws.receive_json()["status"] == "executed"
        fake.feed(_my_day_event())
        assert ws.receive_json()["type"] == "working"
        assert ws.receive_json() == {"type": "working", "kind": "my_day", "status": "done"}
        assert _wait_until(lambda: any("water the plants" in i and "(set)" in i for i in _system_items(fake)))
        ws.send_json({"type": "end"})


def test_my_day_always_ends_with_the_limit_line_even_on_a_busy_day(db, realtime_enabled, brainless, upstream):
    """Fresh review of PR #45: with a full day the twelve-line cap dropped
    the unconditional \"no calendar\" line. Six reminders, four notes, a
    medicine — the limit line is still last."""

    import json as _json

    from app.db.models import CallLog, CapturedIntent, Medication, ResolutionResult, StagedAction
    from app.memory.store import save_memory

    db.add(Medication(name="Sinemet", dosage="25-100 mg", schedule_times='["08:00"]', active=True))
    call = CallLog(call_sid="BUSY", call_type="converse")
    db.add(call)
    db.commit()
    for i in range(6):
        intent = CapturedIntent(call_log_id=call.id, intent_text=f"remind me about thing {i}", requested_action="remind", subject=f"thing {i}", status="resolved")
        db.add(intent)
        db.flush()
        rr = ResolutionResult(captured_intent_id=intent.id, status="staged", action_type="reminder", reversible=True, summary="x")
        db.add(rr)
        db.flush()
        db.add(StagedAction(resolution_result_id=rr.id, status="executed", action_type="reminder", action_payload=_json.dumps({"subject": f"thing {i}"}), reversible=True))
    db.commit()
    for i in range(4):
        save_memory(db, f"Sarah moved the appointment {i} to Friday at two.", "event")
    fake = upstream["script"]([_my_day_event()])
    with client.websocket_connect(live_url()) as ws:
        assert ws.receive_json()["type"] == "working"
        assert ws.receive_json() == {"type": "working", "kind": "my_day", "status": "done"}
        assert _wait_until(lambda: any("Sinemet" in i for i in _system_items(fake)))
        ws.send_json({"type": "end"})
    note = next(i for i in _system_items(fake) if "Sinemet" in i)
    assert "Parker keeps no calendar" in note
    after = note.split("Parker keeps no calendar", 1)[1]
    assert "thing" not in after and "appointment" not in after  # the limit line is the last content
    content = [l for l in note.splitlines() if l.startswith(("His ", "A reminder", "A note", "Right now", "Parker keeps", "…and"))]
    assert len(content) <= 12 and content[-1].startswith("Parker keeps no calendar")
    assert any(l.startswith("…and") and "more Parker did not list" in l for l in content), "a cut is never silent"


def test_my_day_store_failure_is_honest_never_nothing_on_record(db, realtime_enabled, brainless, upstream, monkeypatch):
    """Fresh review of PR #45: a locked/unavailable store must not make
    Parker deny reminders he holds."""

    def broken():
        raise RuntimeError("database is locked")

    monkeypatch.setattr(realtime, "_make_db", broken)
    fake = upstream["script"]([_my_day_event()])
    with client.websocket_connect(live_url()) as ws:
        assert ws.receive_json()["type"] == "working"
        assert ws.receive_json() == {"type": "working", "kind": "my_day", "status": "failed"}
        assert _wait_until(lambda: any("could not read his notes" in i for i in _system_items(fake)))
        ws.send_json({"type": "end"})
    item = next(i for i in _system_items(fake) if "could not read his notes" in i)
    assert "Never say nothing is on record" in item
    assert "nothing written down" not in item


def test_a_vad_reply_created_after_the_tail_satisfies_its_nudge(
    db, realtime_enabled, brainless, upstream, monkeypatch
):
    """He is still talking as the line opens (the common same-breath case):
    the final tail lands while the server VAD has him speaking, so the
    nudge is deferred — and the VAD's own reply, created AFTER the user
    item, already answers it. That reply's done must not fire a second
    reply for one wake (fresh review, 2026-09-02). The inverse — no VAD
    reply — is pinned by test_a_pending_hello_waits_for_the_final_tail."""

    monkeypatch.setattr(realtime, "TAIL_WAIT_SECONDS", 30.0)
    fake = upstream["script"]([])
    with client.websocket_connect(live_url()) as ws:
        ws.send_json({"type": "hello", "tail": "can you", "pending": True})
        assert _wait_until(lambda: any("his own message" in t for t in _system_items(fake)))
        fake.feed({"type": "input_audio_buffer.speech_started"})
        assert ws.receive_json() == {"type": "clear"}
        ws.send_json({"type": "tail", "text": "can you help me with the tv"})
        assert _wait_until(lambda: _user_items(fake) == ["can you help me with the tv"])
        assert _response_creates(fake) == 0  # deferred: he is speaking
        fake.feed({"type": "input_audio_buffer.speech_stopped"})
        fake.feed({"type": "response.created"})  # the VAD answers him — after the user item
        fake.feed({"type": "response.output_audio_transcript.delta", "delta": "Sure, the TV."})
        assert ws.receive_json() == {"type": "assistant_transcript_delta", "text": "Sure, the TV."}
        fake.feed({"type": "response.done", "response": {"output": []}})
        # A later injection still nudges at that response's done as usual.
        fake.feed(_look_done_event("what's on tonight?"))
        assert _wait_until(lambda: any(e["type"] == "conversation.item.create" and e["item"].get("type") == "function_call_output" for e in fake.sent))
        ws.send_json({"type": "end"})
    creates = _response_creates(fake)
    assert creates == 1, f"one wake, one VAD reply, then exactly the lookup's nudge — got {creates}"
    assert _user_items(fake) == ["can you help me with the tv"]


def test_a_tail_frame_without_a_wake_handoff_is_ignored(db, realtime_enabled, brainless, upstream):
    """The handoff delivers his words exactly once and only when a wake
    hello opened it: a stray `tail` on the plain-greeting path mints no
    user item (fresh review, 2026-09-02)."""

    fake = upstream["script"]([])
    with client.websocket_connect(live_url()) as ws:
        ws.send_json({"type": "hello", "tail": ""})
        assert _wait_until(lambda: any("line just opened" in t for t in _system_items(fake)))
        ws.send_json({"type": "tail", "text": "injected after the greeting"})
        ws.send_json({"type": "audio", "data": base64.b64encode(b"\x00\x00").decode()})
        assert _wait_until(lambda: any(e["type"] == "input_audio_buffer.append" for e in fake.sent))
        ws.send_json({"type": "end"})
    assert _user_items(fake) == []
    assert _response_creates(fake) == 1


def test_revoke_cancels_an_upstream_connection_attempt_before_it_can_open():
    """Strict OFF owns connection setup too, not only an opened socket."""

    async def scenario() -> tuple[bool, bool]:
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def connect():
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        async def browser_send(_frame):
            return None

        async def browser_receive() -> dict[str, Any]:
            await asyncio.Event().wait()
            return {}

        bridge = realtime.RealtimeBridge(
            browser_send, browser_receive, upstream_connect=connect
        )
        running = asyncio.create_task(bridge.run())
        await asyncio.wait_for(started.wait(), timeout=1.0)
        bridge.revoke()
        try:
            await asyncio.wait_for(cancelled.wait(), timeout=0.2)
        except asyncio.TimeoutError:
            pass
        try:
            await asyncio.wait_for(asyncio.shield(running), timeout=0.2)
        except asyncio.TimeoutError:
            pass
        observed = cancelled.is_set(), running.done()
        if not running.done():
            running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        return observed

    connect_cancelled, run_finished = asyncio.run(scenario())
    assert connect_cancelled
    assert run_finished


def test_revoke_before_run_is_quiescent_and_never_connects():
    """A bridge registered during handover can be powered off before run()."""

    async def scenario() -> int:
        connects = 0

        async def connect():
            nonlocal connects
            connects += 1
            raise AssertionError("a pre-revoked bridge must not open upstream")

        async def browser_send(_frame):
            return None

        async def browser_receive() -> dict[str, Any]:
            await asyncio.Event().wait()
            return {}

        bridge = realtime.RealtimeBridge(
            browser_send, browser_receive, upstream_connect=connect
        )
        bridge.revoke()
        await asyncio.wait_for(bridge.wait_quiesced(), timeout=0.2)
        await asyncio.wait_for(bridge.run(), timeout=0.2)
        return connects

    assert asyncio.run(scenario()) == 0


@pytest.mark.parametrize(
    "stage", ["session_update", "call_log", "greeting_injection", "initial_nudge"]
)
def test_revoke_cancels_the_supervisor_during_every_startup_await(stage, monkeypatch):
    """OFF reaches startup before the three long-lived pump tasks exist."""

    db_started = threading.Event()
    release_db = threading.Event()

    if stage == "call_log":

        def ensure_call_log(_call_sid):
            db_started.set()
            assert release_db.wait(timeout=3.0)

    else:

        def ensure_call_log(_call_sid):
            return None

    monkeypatch.setattr(realtime, "_ensure_call_log_sync", ensure_call_log)
    monkeypatch.setattr(realtime, "_finalize_session_sync", lambda *_args: None)

    async def scenario() -> tuple[bool, bool]:
        entered = asyncio.Event()
        release_send = asyncio.Event()
        never = asyncio.Event()

        class StartupUpstream:
            def __init__(self):
                self.send_count = 0
                self.closed = False

            async def send(self, raw: str) -> None:
                self.send_count += 1
                frame_type = json.loads(raw).get("type")
                blocked = (
                    (stage == "session_update" and self.send_count == 1)
                    or (stage == "greeting_injection" and self.send_count == 2)
                    or (stage == "initial_nudge" and frame_type == "response.create")
                )
                if blocked:
                    entered.set()
                    await release_send.wait()

            async def close(self) -> None:
                self.closed = True

        upstream = StartupUpstream()
        hello_sent = False

        async def connect():
            return upstream

        async def browser_send(_frame):
            return None

        async def browser_receive() -> dict[str, Any]:
            nonlocal hello_sent
            if not hello_sent:
                hello_sent = True
                return {"type": "hello", "tail": ""}
            await never.wait()
            return {}

        bridge = realtime.RealtimeBridge(
            browser_send, browser_receive, upstream_connect=connect
        )
        running = asyncio.create_task(bridge.run())
        if stage == "call_log":
            assert await asyncio.to_thread(db_started.wait, 1.0)
        else:
            await asyncio.wait_for(entered.wait(), timeout=1.0)
        bridge.revoke()
        quiesced = asyncio.create_task(bridge.wait_quiesced())
        try:
            await asyncio.wait_for(asyncio.shield(quiesced), timeout=0.25)
            stopped_before_release = True
        except asyncio.TimeoutError:
            stopped_before_release = False
        finally:
            release_send.set()
            release_db.set()
            if not running.done():
                running.cancel()
            await asyncio.gather(running, return_exceptions=True)
            if not quiesced.done():
                await asyncio.wait_for(quiesced, timeout=1.0)
        return stopped_before_release, upstream.closed

    stopped, upstream_closed = asyncio.run(scenario())
    assert stopped
    assert upstream_closed


def test_worker_timeout_does_not_claim_provider_quiescence_before_thread_exit(monkeypatch):
    """The timeout owns the result, but OFF still owns the real provider thread."""

    monkeypatch.setattr(realtime, "WORKER_TIMEOUT_SECONDS", 0.05)

    async def abandoning_tracked_thread(fn):
        return await asyncio.to_thread(fn)

    monkeypatch.setattr(realtime, "_tracked_thread", abandoning_tracked_thread)
    worker_started = threading.Event()
    release_worker = threading.Event()

    async def scenario() -> bool:
        timeout_delivered = asyncio.Event()

        class Upstream:
            async def send(self, _raw: str) -> None:
                return None

        async def browser_send(frame):
            if frame == {"type": "working", "kind": "search", "status": "failed"}:
                timeout_delivered.set()

        async def browser_receive() -> dict[str, Any]:
            await asyncio.Event().wait()
            return {}

        def work() -> WorkerResult:
            worker_started.set()
            assert release_worker.wait(timeout=3.0)
            return WorkerResult(kind="search", question="probe", speech="late")

        bridge = realtime.RealtimeBridge(browser_send, browser_receive)
        bridge._upstream = Upstream()
        bridge._spawn_worker("search", work, inflight_key="probe", question="probe")
        assert await asyncio.to_thread(worker_started.wait, 1.0)
        await asyncio.wait_for(timeout_delivered.wait(), timeout=1.0)
        bridge.revoke()
        quiesced = asyncio.create_task(bridge.wait_quiesced())
        await asyncio.sleep(0.1)
        returned_while_thread_running = quiesced.done()
        release_worker.set()
        await asyncio.wait_for(quiesced, timeout=1.0)
        return returned_while_thread_running

    try:
        assert asyncio.run(scenario()) is False
    finally:
        release_worker.set()


def test_revoke_cancels_result_delivery_already_blocked_on_the_realtime_socket():
    """A provider may finish before OFF; its still-blocked delivery stays silent."""

    async def scenario() -> tuple[list[dict], list[dict]]:
        send_started = asyncio.Event()
        release_send = asyncio.Event()
        sent: list[dict] = []
        browser_frames: list[dict] = []

        class Upstream:
            async def send(self, raw: str) -> None:
                send_started.set()
                await release_send.wait()
                sent.append(json.loads(raw))

        async def browser_send(frame):
            browser_frames.append(frame)

        async def browser_receive() -> dict[str, Any]:
            await asyncio.Event().wait()
            return {}

        bridge = realtime.RealtimeBridge(browser_send, browser_receive)
        bridge._upstream = Upstream()
        bridge._spawn_worker(
            "search",
            lambda: WorkerResult(kind="search", question="probe", speech="answer"),
            inflight_key="probe",
            question="probe",
        )
        await asyncio.wait_for(send_started.wait(), timeout=1.0)
        bridge.revoke()
        await asyncio.sleep(0)
        release_send.set()
        await asyncio.wait_for(bridge.wait_quiesced(), timeout=1.0)
        await asyncio.sleep(0)
        return sent, browser_frames

    sent, browser_frames = asyncio.run(scenario())
    assert sent == []
    assert browser_frames == []


@pytest.mark.parametrize("result_key", ["search:us-open", "my_day:"])
@pytest.mark.parametrize("status", ["cancelled", "incomplete", "failed", "completed"])
def test_unspoken_result_response_keeps_the_obligation_open(result_key, status):
    """Search and My Day stay open until a completed response produces speech."""

    async def scenario() -> tuple[set[str], set[str], set[str], str]:
        class Upstream:
            async def send(self, _raw: str) -> None:
                return None

        async def browser_send(_frame):
            return None

        async def browser_receive() -> dict[str, Any]:
            await asyncio.Event().wait()
            return {}

        bridge = realtime.RealtimeBridge(browser_send, browser_receive)
        bridge._upstream = Upstream()

        async def no_journal(*_args, **_kwargs):
            return None

        bridge._journal = no_journal
        bridge._last_assistant_speech = "Here is the useful answer Parker gave him just before."
        bridge._inflight_lookups.add(result_key)
        await bridge._request_nudge(result_key=result_key)
        await bridge._handle_upstream_event(
            {"type": "response.done", "response": {"status": status, "output": []}}
        )
        await bridge._maybe_end_session("OK, thanks.")
        return (
            set(bridge._inflight_lookups),
            set(bridge._pending_result_keys),
            set(bridge._active_result_keys),
            bridge._session_end_kind,
        )

    inflight, pending, active, end_kind = asyncio.run(scenario())
    assert result_key in inflight
    assert result_key in pending | active
    assert end_kind == ""


def test_browser_stop_keeps_result_pending_without_immediately_restarting_speech():
    """Stop silences this response; the result rebinds only when he continues."""

    async def scenario() -> tuple[int, set[str], set[str]]:
        sent: list[dict[str, Any]] = []
        browser_frames = iter(({"type": "stop"}, {"type": "end"}))

        class Upstream:
            async def send(self, raw: str) -> None:
                sent.append(json.loads(raw))

        async def browser_send(_frame):
            return None

        async def browser_receive() -> dict[str, Any]:
            return next(browser_frames)

        bridge = realtime.RealtimeBridge(browser_send, browser_receive)
        bridge._upstream = Upstream()
        bridge._inflight_lookups.add("search:weather")
        await bridge._request_nudge(result_key="search:weather")
        await bridge._pump_browser()
        await bridge._handle_upstream_event(
            {
                "type": "response.done",
                "response": {"status": "cancelled", "output": []},
            }
        )
        creates = sum(frame.get("type") == "response.create" for frame in sent)
        return creates, set(bridge._inflight_lookups), set(bridge._pending_result_keys)

    creates, inflight, pending = asyncio.run(scenario())
    assert creates == 1
    assert inflight == {"search:weather"}
    assert pending == {"search:weather"}


def test_search_question_cannot_alias_the_my_day_result_key(monkeypatch):
    """Tool-controlled namespaces stay disjoint from arbitrary question text."""

    monkeypatch.setattr(realtime_workers, "search_worker_available", lambda: True)

    async def scenario():
        sent = []
        spawned = []

        class Upstream:
            async def send(self, raw):
                sent.append(json.loads(raw))

        async def browser_send(_frame):
            return None

        async def browser_receive():
            await asyncio.Event().wait()
            return {}

        bridge = realtime.RealtimeBridge(browser_send, browser_receive)
        bridge._upstream = Upstream()

        async def no_journal(*_args, **_kwargs):
            return None

        bridge._journal = no_journal
        bridge._spawn_search_worker = (
            lambda question, key: spawned.append((question, key))
        )
        bridge._inflight_lookups.add("my_day:")
        await bridge._handle_look_that_up(
            {"call_id": "collision"}, {"question": "my_day:"}
        )
        outputs = [
            json.loads(frame["item"]["output"])
            for frame in sent
            if frame.get("type") == "conversation.item.create"
            and frame.get("item", {}).get("type") == "function_call_output"
        ]
        return spawned, outputs, set(bridge._inflight_lookups)

    spawned, outputs, inflight = asyncio.run(scenario())
    assert spawned == [("my_day:", "search:my_day:")]
    assert [output["status"] for output in outputs] == ["working"]
    assert inflight == {"my_day:", "search:my_day:"}
