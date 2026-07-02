"""OpenClawBrainAdapter over a fake gateway — no key, no network.

Pins the gateway contract (OpenAI-compatible chat completions + the Parker
bridge endpoints), the proposal channels (tool_calls and tagged JSON), and
the degradation ladder: gateway down → FallbackBrain notice → Claude
adapter or an honest stub. Every reply still passes the post-response
guard on its way to the user.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.brain.adapter import BrainContext, BrainReply, Message
from app.brain.build import build_brain_adapter
from app.brain.openclaw import (
    FallbackBrain,
    GatewayError,
    OpenClawBrainAdapter,
    OpenClawGateway,
)
from app.config import settings
from app.db.models import CallLog, CapturedIntent

CONTEXT = BrainContext(patient_name="Dad", lexicon_names=("Sarah", "Priya"))


def _chat_reply(content=None, tool_calls=None) -> dict:
    message: dict = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message}]}


def _gateway(handler, token: str = "") -> OpenClawGateway:
    transport = httpx.MockTransport(handler)
    return OpenClawGateway(
        "http://gateway.test:18789",
        token=token,
        client=httpx.Client(transport=transport),
    )


def _tool_call(arguments: dict) -> dict:
    return {
        "id": "call_1",
        "type": "function",
        "function": {"name": "propose_action", "arguments": json.dumps(arguments)},
    }


PLAYLIST_ARGS = {
    "action_type": "media_playlist",
    "label": "put old Hindi songs on the TV",
    "subject": "old Hindi songs on the TV",
    "intent_text": "play a playlist of old Hindi songs on the TV",
}


# ---------------------------------------------------------------------------
# Request shape: the documented gateway API
# ---------------------------------------------------------------------------


def test_respond_posts_openai_chat_completion_with_context_and_tools():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["payload"] = json.loads(request.content)
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json=_chat_reply(content="It's Tuesday."))

    adapter = OpenClawBrainAdapter(_gateway(handler, token="secret-token-name"))
    history = [Message(role="user", content="Hello"), Message(role="assistant", content="Hi Dad.")]

    reply = adapter.respond(history, "What day is it?", CONTEXT)

    assert reply.speech == "It's Tuesday."
    assert seen["path"] == "/v1/chat/completions"
    assert seen["auth"] == "Bearer secret-token-name"
    payload = seen["payload"]
    assert payload["model"] == "openclaw"
    assert payload["messages"][0]["role"] == "system"
    assert "Dad" in payload["messages"][0]["content"]
    assert "Sarah" in payload["messages"][0]["content"]
    assert [m["role"] for m in payload["messages"][1:]] == ["user", "assistant", "user"]
    assert payload["messages"][-1]["content"] == "What day is it?"
    assert payload["tools"][0]["function"]["name"] == "propose_action"


def test_no_auth_header_without_token():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json=_chat_reply(content="Hello."))

    OpenClawBrainAdapter(_gateway(handler)).respond([], "Hi", CONTEXT)

    assert seen["auth"] is None


# ---------------------------------------------------------------------------
# Proposal channels: tool_calls and the tagged-JSON fallback
# ---------------------------------------------------------------------------


def test_tool_call_proposals_are_parsed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_chat_reply(content="Happy to.", tool_calls=[_tool_call(PLAYLIST_ARGS)]),
        )

    reply = OpenClawBrainAdapter(_gateway(handler)).respond([], "Put on some songs", CONTEXT)

    assert reply.speech == "Happy to."
    assert len(reply.proposed_actions) == 1
    action = reply.proposed_actions[0]
    assert action.action_type == "media_playlist"
    assert action.subject == "old Hindi songs on the TV"


def test_tagged_json_proposals_are_parsed_and_stripped_from_speech():
    tagged = (
        "I can queue those up. "
        f"<propose_action>{json.dumps(PLAYLIST_ARGS)}</propose_action>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_reply(content=tagged))

    reply = OpenClawBrainAdapter(_gateway(handler)).respond([], "Put on some songs", CONTEXT)

    assert reply.speech == "I can queue those up."
    assert "<propose_action>" not in reply.speech
    assert len(reply.proposed_actions) == 1
    assert reply.proposed_actions[0].action_type == "media_playlist"


def test_malformed_proposal_json_is_dropped_not_crashed():
    def handler(request: httpx.Request) -> httpx.Response:
        bad_call = {
            "id": "call_1",
            "type": "function",
            "function": {"name": "propose_action", "arguments": "{not json"},
        }
        content = "Still here. <propose_action>{also not json</propose_action>"
        return httpx.Response(200, json=_chat_reply(content=content, tool_calls=[bad_call]))

    reply = OpenClawBrainAdapter(_gateway(handler)).respond([], "Hi", CONTEXT)

    assert reply.proposed_actions == ()
    assert "Still here." in reply.speech


# ---------------------------------------------------------------------------
# Gateway failure modes → GatewayError
# ---------------------------------------------------------------------------


def test_http_error_raises_gateway_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(GatewayError):
        OpenClawBrainAdapter(_gateway(handler)).respond([], "Hi", CONTEXT)


def test_connection_error_raises_gateway_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(GatewayError):
        OpenClawBrainAdapter(_gateway(handler)).respond([], "Hi", CONTEXT)


def test_reply_without_choices_raises_gateway_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"object": "chat.completion"})

    with pytest.raises(GatewayError):
        OpenClawBrainAdapter(_gateway(handler)).respond([], "Hi", CONTEXT)


# ---------------------------------------------------------------------------
# FallbackBrain: spoken notice once, then the backup brain
# ---------------------------------------------------------------------------


class _DownBrain:
    def respond(self, history, utterance, context):
        raise GatewayError("gateway down")


class _EchoBrain:
    def __init__(self):
        self.calls = 0

    def respond(self, history, utterance, context):
        self.calls += 1
        return BrainReply(speech=f"backup answer {self.calls}")


def test_fallback_speaks_notice_once_then_uses_backup():
    backup = _EchoBrain()
    brain = FallbackBrain(_DownBrain(), fallback=backup)

    first = brain.respond([], "Hi", CONTEXT)
    second = brain.respond([], "Hi again", CONTEXT)

    assert FallbackBrain.NOTICE in first.speech
    assert "backup answer 1" in first.speech
    assert second.speech == "backup answer 2"  # notice not repeated
    assert backup.calls == 2


def test_fallback_without_backup_is_an_honest_notice():
    brain = FallbackBrain(_DownBrain(), fallback=None)

    reply = brain.respond([], "Hi", CONTEXT)

    assert FallbackBrain.NOTICE in reply.speech
    assert "reminders" in reply.speech
    assert reply.proposed_actions == ()


def test_fallback_passes_through_when_primary_healthy():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_reply(content="From the gateway."))

    brain = FallbackBrain(OpenClawBrainAdapter(_gateway(handler)), fallback=_EchoBrain())

    reply = brain.respond([], "Hi", CONTEXT)

    assert reply.speech == "From the gateway."
    assert FallbackBrain.NOTICE not in reply.speech


# ---------------------------------------------------------------------------
# Builder selection: zero-config stays zero-config
# ---------------------------------------------------------------------------


def test_build_brain_adapter_without_gateway_or_key_is_none(monkeypatch):
    monkeypatch.setattr(settings, "parker_openclaw_gateway_url", "")
    monkeypatch.setattr(settings, "anthropic_api_key", "")

    assert build_brain_adapter() is None


def test_build_brain_adapter_with_gateway_url_wraps_openclaw_in_fallback(monkeypatch):
    monkeypatch.setattr(settings, "parker_openclaw_gateway_url", "http://127.0.0.1:18789")
    monkeypatch.setattr(settings, "anthropic_api_key", "")

    brain = build_brain_adapter()

    assert isinstance(brain, FallbackBrain)
    assert isinstance(brain._primary, OpenClawBrainAdapter)
    assert brain._fallback is None  # keyless: no Claude backup, honest notice instead


# ---------------------------------------------------------------------------
# Through TextSession: gateway proposals stay confirmation-gated and guarded
# ---------------------------------------------------------------------------


def _session(db, brain):
    from app.conversation.textloop import TextSession

    call = CallLog(call_sid="CA_OPENCLAW_LANE", call_type="text_loop")
    db.add(call)
    db.commit()
    db.refresh(call)
    return TextSession(db, call.id, brain=brain, brain_context=CONTEXT)


def test_gateway_proposal_becomes_choice_and_captures_only_on_selection(db):
    from app.parker.hands import configure_hands
    from tests.test_hands import FakeHands

    configure_hands(FakeHands(action_types=("media_playlist",)))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_chat_reply(content="Happy to.", tool_calls=[_tool_call(PLAYLIST_ARGS)]),
        )

    session = _session(db, OpenClawBrainAdapter(_gateway(handler)))

    offered = session.handle("Could you put on some of the old songs somehow?")
    assert offered["kind"] == "choices"
    assert db.query(CapturedIntent).count() == 0  # proposal alone captures nothing

    picked = session.handle("1")
    assert picked["kind"] == "captured"
    captured = db.query(CapturedIntent).one()
    assert captured.requested_action == "media_playlist"


def test_gateway_proposal_without_enabled_skill_is_dropped(db):
    """Skill curation is the capability gate: no enabled skill, no proposal."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_chat_reply(content="Happy to.", tool_calls=[_tool_call(PLAYLIST_ARGS)]),
        )

    session = _session(db, OpenClawBrainAdapter(_gateway(handler)))

    response = session.handle("Could you put on some of the old songs somehow?")

    assert response["kind"] == "answer"  # the speech survives; the dead-end proposal does not
    assert db.query(CapturedIntent).count() == 0


def test_prohibited_gateway_proposal_is_dropped_by_the_guard(db):
    purchase = {
        "action_type": "purchase",
        "label": "order the walker",
        "subject": "walker",
        "intent_text": "buy the walker on Amazon",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_chat_reply(content="I could get that.", tool_calls=[_tool_call(purchase)]),
        )

    session = _session(db, OpenClawBrainAdapter(_gateway(handler)))

    response = session.handle("That walker we saw would be nice to have around")

    # The proposal vanishes at the guard; the speech survives as an answer.
    assert response["kind"] == "answer"
    assert db.query(CapturedIntent).count() == 0


def test_gateway_down_mid_session_degrades_to_spoken_notice(db, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    brain = FallbackBrain(OpenClawBrainAdapter(_gateway(handler)), fallback=None)
    session = _session(db, brain)

    response = session.handle("What day is it today?")

    assert response["kind"] == "answer"
    assert "backup" in response["speech"] or "can't reach" in response["speech"]
    assert db.query(CapturedIntent).count() == 0
