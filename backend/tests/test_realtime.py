"""The realtime full-duplex lane: Parker stays the policy boundary.

Everything runs against a scripted fake upstream over the real websocket
endpoint — no network, no OpenAI key. Pinned contracts:

- the session config carries the Parker persona, patient semantic VAD,
  transcription, and propose_action as the ONLY tool;
- the post-hoc guard cancels a medical-boundary reply mid-stream, flushes
  playback, and speaks the standard redirect;
- a propose_action function call stages through the real pipeline and the
  model is told it is waiting for on-screen confirmation — nothing
  executes from this lane;
- browser Stop cancels the response; junk audio is never forwarded;
- no key -> an honest unavailable message, never a broken socket.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.brain.guard import MEDICAL_BOUNDARY_REDIRECT
from app.db.models import StagedAction
from app.main import app
from app.parker import realtime

client = TestClient(app)


class FakeUpstream:
    """Scripted OpenAI-side socket: replays events, records what Parker sends."""

    def __init__(self, events):
        self.sent: list[dict] = []
        self._events = list(events)
        self.closed = False

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def recv(self) -> str:
        if self._events:
            return json.dumps(self._events.pop(0))
        await asyncio.Event().wait()  # nothing left — hold until cancelled
        raise AssertionError("unreachable")

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def realtime_enabled(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "parker_realtime_enabled", True)
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
    """Bridge side effects land in the test engine, never the real DB."""

    factory = sessionmaker(bind=db.get_bind())
    monkeypatch.setattr(realtime, "_db_session_factory", factory)
    return db


def test_without_a_key_the_lane_is_honestly_unavailable(db):
    with client.websocket_connect("/parker/converse/realtime") as ws:
        message = ws.receive_json()
    assert message["type"] == "unavailable"
    assert "OpenAI key" in message["text"]


def test_session_config_carries_persona_vad_transcription_and_only_propose_action(
    db, realtime_enabled, upstream
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
    }
    assert session["audio"]["input"]["transcription"]["model"]
    assert [tool["name"] for tool in session["tools"]] == ["propose_action"]
    assert "Parkinson" in session["instructions"]
    assert "waiting for their" in session["instructions"]  # wraps across a line
    # the browser's audio chunk was forwarded verbatim
    appended = [e for e in fake.sent if e["type"] == "input_audio_buffer.append"]
    assert appended and appended[0]["audio"] == "QUJD"


def test_audio_and_transcripts_flow_to_the_browser(db, realtime_enabled, upstream):
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


def test_posthoc_guard_cancels_flushes_and_redirects(db, realtime_enabled, upstream):
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
    db, realtime_enabled, upstream
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
    outputs = [e for e in fake.sent if e["type"] == "conversation.item.create"]
    assert outputs and outputs[0]["item"]["call_id"] == "call-1"
    assert "confirmation" in outputs[0]["item"]["output"]
    assert any(e["type"] == "response.create" for e in fake.sent)


def test_prohibited_action_types_are_rejected_not_staged(db, realtime_enabled, upstream):
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
    outputs = [e for e in fake.sent if e["type"] == "conversation.item.create"]
    assert outputs and "not allowed" in outputs[0]["item"]["output"]


def test_stop_cancels_upstream_and_junk_audio_never_forwards(
    db, realtime_enabled, upstream
):
    fake = upstream["script"]([])
    with client.websocket_connect("/parker/converse/realtime") as ws:
        ws.send_json({"type": "audio", "data": "not-base64!"})
        ws.send_json({"type": "stop"})
        assert ws.receive_json() == {"type": "clear"}
        ws.send_json({"type": "end"})

    assert not any(e["type"] == "input_audio_buffer.append" for e in fake.sent)
    assert any(e["type"] == "response.cancel" for e in fake.sent)


def test_barge_in_flushes_playback(db, realtime_enabled, upstream):
    upstream["script"]([{"type": "input_audio_buffer.speech_started"}])
    with client.websocket_connect("/parker/converse/realtime") as ws:
        assert ws.receive_json() == {"type": "clear"}
        ws.send_json({"type": "end"})


def test_exchange_mirrors_to_the_live_screen(db, realtime_enabled, upstream):
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
    db, realtime_enabled, upstream, monkeypatch
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
    outputs = [e for e in fake.sent if e["type"] == "conversation.item.create"]
    assert outputs and "not in the family" in outputs[0]["item"]["output"]


def test_gateway_backed_types_without_a_gateway_are_rejected_not_claimed_staged(
    db, realtime_enabled, upstream
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
    outputs = [e for e in fake.sent if e["type"] == "conversation.item.create"]
    assert outputs and "not allowed" in outputs[0]["item"]["output"]


def test_malformed_function_arguments_never_kill_the_call(db, realtime_enabled, upstream):
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
    outputs = [e for e in fake.sent if e["type"] == "conversation.item.create"]
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


def test_live_line_cap_refuses_a_third_concurrent_call(db, realtime_enabled, upstream):
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


def test_realtime_instructions_never_claim_web_search(db, realtime_enabled):
    from app.parker.realtime import build_session_update

    instructions = build_session_update()["session"]["instructions"]
    assert "do NOT have web search" in instructions
    assert "never claim to have looked something up" in instructions
