"""The two v1 acceptance scenarios from docs/brain-adapters.md, end to end.

Voice-transcript style against one fake OpenClaw gateway (chat + skill
discovery + skill invocation all faked; no key, no network):

1. "Put on some old Hindi songs on the TV" → media_playlist proposal →
   patient confirms → skill invoked → spoken result.
2. "Find two-bedroom homes near Sarah and show them on my computer" →
   research + open_links proposal → confirm → browsing skill returns
   listings → open-on-device invoked → spoken summary. No purchase path
   exists anywhere in the flow.

Every step is the real machinery: TextSession routing, the post-response
guard, capture → resolve → stage, the patient's spoken confirmation, and
the hands executor — exactly what `make talk-loop` runs per turn.
"""

from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

from app.brain.adapter import BrainContext
from app.brain.openclaw import OpenClawBrainAdapter, OpenClawGateway
from app.db.models import CallLog, CapturedIntent, StagedAction
from app.main import app
from app.parker.hands import OpenClawHands, configure_hands
from app.parker.pipeline import resolve_captured_intents, stage_resolved_actions

CONTEXT = BrainContext(patient_name="Dad", lexicon_names=("Sarah", "Priya"))


class FakeGatewayServer:
    """One fake OpenClaw gateway: scripted replies, every request recorded."""

    def __init__(self, chat_replies: list[dict], skills: list[dict], invoke_reply: dict):
        self._chat_replies = list(chat_replies)
        self._skills = skills
        self._invoke_reply = invoke_reply
        self.requests: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        record = {"method": request.method, "path": request.url.path}
        if request.content:
            record["payload"] = json.loads(request.content)
        self.requests.append(record)
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(200, json=self._chat_replies.pop(0))
        if request.url.path == "/parker/v1/skills":
            return httpx.Response(200, json={"skills": self._skills})
        if request.url.path == "/parker/v1/skills/invoke":
            return httpx.Response(200, json=self._invoke_reply)
        return httpx.Response(404, json={"error": "unknown path"})

    def gateway(self) -> OpenClawGateway:
        return OpenClawGateway(
            "http://gateway.test:18789",
            client=httpx.Client(transport=httpx.MockTransport(self.handler)),
        )

    def invocations(self) -> list[dict]:
        return [r for r in self.requests if r["path"] == "/parker/v1/skills/invoke"]

    def assert_no_purchase_anywhere(self) -> None:
        blob = json.dumps(self.requests).lower()
        assert '"purchase"' not in blob
        assert "checkout" not in blob
        assert all("purchase" not in r["path"] for r in self.requests)


def _proposal_reply(speech: str, arguments: dict) -> dict:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": speech,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "propose_action",
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ],
                }
            }
        ]
    }


def _session(db, server: FakeGatewayServer):
    gateway = server.gateway()
    configure_hands(OpenClawHands.discover(gateway))
    from app.conversation.textloop import TextSession

    call = CallLog(call_sid="CA_ACCEPTANCE", call_type="text_loop")
    db.add(call)
    db.commit()
    db.refresh(call)
    return TextSession(db, call.id, brain=OpenClawBrainAdapter(gateway), brain_context=CONTEXT)


def _tick(db):
    resolve_captured_intents(db)
    stage_resolved_actions(db)


def test_scenario_1_old_hindi_songs_on_the_tv(db):
    server = FakeGatewayServer(
        chat_replies=[
            _proposal_reply(
                "Lovely idea.",
                {
                    "action_type": "media_playlist",
                    "label": "put old Hindi songs on the TV",
                    "subject": "old Hindi songs on the TV",
                    "intent_text": "play a playlist of old Hindi songs on the living-room TV",
                },
            )
        ],
        skills=[{"name": "youtube-tv", "action_types": ["media_playlist"], "enabled": True}],
        invoke_reply={"status": "ok", "detail": "queued 12 old Hindi songs on the living-room TV"},
    )
    session = _session(db, server)

    # you> Put on some old Hindi songs on the TV
    offered = session.handle("Put on some old Hindi songs on the TV")
    assert offered["kind"] == "choices"
    assert "Lovely idea." in offered["speech"]
    assert db.query(CapturedIntent).count() == 0  # proposing captured nothing

    # you> 1   (picks the proposal — this is repair/consent, not yet the action gate)
    picked = session.handle("1")
    assert picked["kind"] == "captured"

    # [tick] → parker offers the staged action for the patient's own confirmation
    _tick(db)
    offer = session.offer_pending_confirmation()
    assert offer is not None
    assert "old Hindi songs" in offer["speech"]
    assert "yes or no" in offer["speech"]

    # you> yes
    done = session.handle("yes")
    assert done["kind"] == "executed"
    assert done["speech"] == "Done — queued 12 old Hindi songs on the living-room TV"

    # The skill was invoked exactly once, with the approved intent.
    invocations = server.invocations()
    assert len(invocations) == 1
    payload = invocations[0]["payload"]
    assert payload["action_type"] == "media_playlist"
    assert payload["payload"]["subject"] == "old Hindi songs on the TV"
    assert payload["idempotency_key"].startswith("staged-action-")

    # Pipeline record: the patient confirmed; the family sees it in review.
    action = db.query(StagedAction).one()
    assert action.status == "executed"
    assert action.confirmed_by == "patient"
    review = TestClient(app).get("/parker/review").json()
    assert any(
        "queued 12 old Hindi songs" in (item["execution_result"] or "")
        for item in review["recent_history"]
    )
    server.assert_no_purchase_anywhere()


def test_scenario_2_find_homes_and_open_on_the_computer(db):
    server = FakeGatewayServer(
        chat_replies=[
            _proposal_reply(
                "I can look for two-bedroom homes near Sarah and put the listings on your computer.",
                {
                    "action_type": "open_links",
                    "label": "find two-bedroom homes near Sarah and show them on your computer",
                    "subject": "two-bedroom homes near Sarah",
                    "intent_text": (
                        "research two-bedroom homes for sale near Sarah and open the "
                        "best listings read-only on the approved family computer"
                    ),
                },
            )
        ],
        skills=[{"name": "family-browser", "action_types": ["open_links"], "enabled": True}],
        invoke_reply={
            "status": "ok",
            "detail": "opened 3 two-bedroom listings near Sarah on the family computer",
        },
    )
    session = _session(db, server)

    offered = session.handle("Find two-bedroom homes near Sarah and show them on my computer")
    assert offered["kind"] == "choices"

    picked = session.handle("1")
    assert picked["kind"] == "captured"

    _tick(db)
    offer = session.offer_pending_confirmation()
    assert offer is not None
    assert "family computer" in offer["speech"]

    done = session.handle("yes")
    assert done["kind"] == "executed"
    assert "opened 3 two-bedroom listings near Sarah" in done["speech"]

    invocations = server.invocations()
    assert len(invocations) == 1
    assert invocations[0]["payload"]["action_type"] == "open_links"
    # The research intent travels with the approved action — the skill does
    # the browsing; Parker never gets a purchase surface to forward to.
    assert "read-only" in invocations[0]["payload"]["payload"]["intent_text"]

    # No purchase path exists anywhere in the flow: not in gateway traffic,
    # not in captured intents, not on the execution surface.
    server.assert_no_purchase_anywhere()
    assert db.query(CapturedIntent).filter(CapturedIntent.requested_action == "purchase").count() == 0
    from app.parker.pipeline import currently_executable_action_types

    assert "purchase" not in currently_executable_action_types()


def test_scenario_1_gateway_error_mid_execution_fails_loud_and_visible(db):
    """The failure edge of scenario 1: the TV skill dies after confirmation."""

    server = FakeGatewayServer(
        chat_replies=[
            _proposal_reply(
                "Lovely idea.",
                {
                    "action_type": "media_playlist",
                    "label": "put old Hindi songs on the TV",
                    "subject": "old Hindi songs on the TV",
                    "intent_text": "play old Hindi songs on the TV",
                },
            )
        ],
        skills=[{"name": "youtube-tv", "action_types": ["media_playlist"], "enabled": True}],
        invoke_reply={"status": "error", "detail": "the TV rejected the cast"},
    )
    session = _session(db, server)

    session.handle("Put on some old Hindi songs on the TV")
    session.handle("1")
    _tick(db)
    assert session.offer_pending_confirmation() is not None

    failed = session.handle("yes")

    # Spoken failure + review row; exactly one attempt, no silent retry.
    assert failed["kind"] == "execution_failed"
    assert "the TV rejected the cast" in failed["speech"]
    assert "won't retry" in failed["speech"]
    assert len(server.invocations()) == 1
    action = db.query(StagedAction).one()
    assert action.status == "failed"
    review = TestClient(app).get("/parker/review").json()
    assert len(review["recent_failed"]) == 1
