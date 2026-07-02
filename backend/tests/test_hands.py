"""The execution seam: OpenClaw skills act only on confirmed, approved intents.

Pins the v1 design's second half (docs/brain-adapters.md): skill discovery
gates what is proposable/executable, the policy taxonomy gates what a
gateway may advertise, execution forwards the approved intent exactly once,
and failure becomes a spoken/visible ``failed`` row — never a silent retry.
All gateway traffic is a fake transport; no key, no network.
"""

from __future__ import annotations

import json
from datetime import datetime

import httpx
from fastapi.testclient import TestClient

from app.config import settings
from app.db.models import CallLog, CapturedIntent
from app.main import app
from app.parker import hands as hands_module
from app.parker.hands import (
    OpenClawHands,
    SkillResult,
    configure_hands,
    configure_hands_from_settings,
    configured_hands,
    effective_proposable_action_types,
    gateway_executable_action_types,
)
from app.parker.pipeline import (
    confirm_staged_action,
    currently_executable_action_types,
    execute_staged_action,
    resolve_captured_intents,
    stage_resolved_actions,
)
from app.brain.openclaw import OpenClawGateway

NOW = datetime(2026, 7, 1, 20, 0, 0)

SKILLS_PAYLOAD = {
    "skills": [
        {"name": "youtube-tv", "action_types": ["media_playlist"], "enabled": True},
        {"name": "family-browser", "action_types": ["open_links"], "enabled": True},
        {"name": "shopping", "action_types": ["purchase"], "enabled": True},
        {"name": "weird", "action_types": ["teleport_patient"], "enabled": True},
        {"name": "disabled-radio", "action_types": ["media_playlist"], "enabled": False},
    ]
}


def _gateway(handler) -> OpenClawGateway:
    return OpenClawGateway(
        "http://gateway.test:18789",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


class FakeHands:
    """Scripted hands for pipeline tests: records every invocation."""

    def __init__(self, action_types=("media_playlist", "open_links"), result: SkillResult | None = None):
        self._action_types = frozenset(action_types)
        self._result = result or SkillResult(ok=True, detail="queued the playlist on the living-room TV")
        self.invocations: list[dict] = []

    def enabled_action_types(self) -> frozenset[str]:
        return self._action_types

    def invoke(self, action_type, payload, *, idempotency_key) -> SkillResult:
        self.invocations.append(
            {"action_type": action_type, "payload": payload, "idempotency_key": idempotency_key}
        )
        return self._result


def _captured(db, requested_action="media_playlist", subject="old Hindi songs on the TV"):
    call = CallLog(call_sid="CA_HANDS", call_type="text_loop")
    db.add(call)
    db.commit()
    captured = CapturedIntent(
        call_log_id=call.id,
        intent_text=f"play {subject}",
        requested_action=requested_action,
        subject=subject,
        due_at=NOW,
    )
    db.add(captured)
    db.commit()
    return captured


def _staged(db, **kwargs):
    _captured(db, **kwargs)
    resolve_captured_intents(db, now=NOW)
    return stage_resolved_actions(db, now=NOW)[0]


# ---------------------------------------------------------------------------
# Discovery: the family curates the skill surface on the gateway
# ---------------------------------------------------------------------------


def test_discover_reads_enabled_skills_from_the_bridge_endpoint():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json=SKILLS_PAYLOAD)

    hands = OpenClawHands.discover(_gateway(handler))

    assert seen["path"] == "/parker/v1/skills"
    assert hands.enabled_action_types() == {"media_playlist", "open_links", "purchase", "teleport_patient"}
    assert hands.skill_for("media_playlist") == "youtube-tv"  # the enabled one, not the disabled radio


def test_configure_from_settings_without_url_is_noop(monkeypatch):
    monkeypatch.setattr(settings, "parker_openclaw_gateway_url", "")

    assert configure_hands_from_settings() is None
    assert configured_hands() is None


def test_configure_from_settings_survives_a_down_gateway(monkeypatch):
    monkeypatch.setattr(settings, "parker_openclaw_gateway_url", "http://127.0.0.1:1")

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(
        hands_module, "build_openclaw_gateway", lambda: _gateway(boom)
    )

    assert configure_hands_from_settings() is None
    assert configured_hands() is None  # degraded, not crashed


# ---------------------------------------------------------------------------
# Policy gates what a gateway may advertise
# ---------------------------------------------------------------------------


def test_gateway_executable_types_are_policy_filtered():
    configure_hands(FakeHands(action_types=(
        "media_playlist",  # local_reversible + user confirm -> allowed
        "open_links",      # local_reversible + user confirm -> allowed
        "purchase",        # irreversible/human operator -> ignored
        "family_message",  # external messaging tier: the outbox owns it -> ignored
        "reminder",        # locally executable: never routed to the gateway
        "teleport_patient",  # unknown -> safe default policy -> ignored
    )))

    assert gateway_executable_action_types() == {"media_playlist", "open_links"}


def test_execution_surface_without_hands_is_exactly_the_local_set():
    assert configured_hands() is None
    assert currently_executable_action_types() == {"reminder", "exercise_start", "family_message"}


def test_execution_surface_with_hands_adds_gateway_types():
    configure_hands(FakeHands())

    assert currently_executable_action_types() == {
        "reminder",
        "exercise_start",
        "family_message",
        "media_playlist",
        "open_links",
    }


def test_proposable_types_require_an_enabled_skill():
    # Without hands: gateway-backed types are invisible to every brain.
    without = effective_proposable_action_types()
    assert "media_playlist" not in without
    assert "open_links" not in without
    assert {"reminder", "family_message", "exercise_start", "appointment_note"} <= without

    configure_hands(FakeHands(action_types=("media_playlist",)))
    with_playlist = effective_proposable_action_types()
    assert "media_playlist" in with_playlist
    assert "open_links" not in with_playlist  # no skill -> still not proposable


# ---------------------------------------------------------------------------
# Execution: approved intent forwarded exactly once, result relayed
# ---------------------------------------------------------------------------


def test_confirmed_gateway_action_executes_via_skill(db):
    fake = FakeHands()
    configure_hands(fake)
    staged = _staged(db)
    confirm_staged_action(db, staged.id, confirmed_by="patient", now=NOW)

    executed = execute_staged_action(db, staged.id, now=NOW)

    assert executed.status == "executed"
    assert "openclaw skill completed" in executed.execution_result
    assert "living-room TV" in executed.execution_result  # the skill's speakable detail
    assert len(fake.invocations) == 1
    invocation = fake.invocations[0]
    assert invocation["action_type"] == "media_playlist"
    assert invocation["payload"]["subject"] == "old Hindi songs on the TV"
    assert invocation["idempotency_key"] == f"staged-action-{staged.id}"


def test_unconfirmed_gateway_action_is_blocked_and_never_invoked(db):
    fake = FakeHands()
    configure_hands(fake)
    staged = _staged(db)

    blocked = execute_staged_action(db, staged.id, now=NOW)

    assert blocked.status == "blocked"
    assert "confirmation" in blocked.execution_result
    assert fake.invocations == []


def test_skill_failure_becomes_failed_row_with_no_retry(db):
    fake = FakeHands(result=SkillResult(ok=False, detail="the TV is unreachable"))
    configure_hands(fake)
    staged = _staged(db)
    confirm_staged_action(db, staged.id, confirmed_by="patient", now=NOW)

    failed = execute_staged_action(db, staged.id, now=NOW)

    assert failed.status == "failed"
    assert "no retry was attempted" in failed.execution_result
    assert "the TV is unreachable" in failed.execution_result
    assert len(fake.invocations) == 1

    # Executing again is a no-op: the failure stands, the skill is not re-run.
    again = execute_staged_action(db, staged.id, now=NOW)
    assert again.status == "failed"
    assert "the TV is unreachable" in again.execution_result
    assert len(fake.invocations) == 1


def test_executed_action_is_terminal_and_not_rerun(db):
    fake = FakeHands()
    configure_hands(fake)
    staged = _staged(db)
    confirm_staged_action(db, staged.id, confirmed_by="patient", now=NOW)
    execute_staged_action(db, staged.id, now=NOW)

    again = execute_staged_action(db, staged.id, now=NOW)

    assert again.status == "executed"
    assert len(fake.invocations) == 1


def test_hands_vanishing_after_staging_blocks_execution(db):
    configure_hands(FakeHands())
    staged = _staged(db)
    confirm_staged_action(db, staged.id, confirmed_by="patient", now=NOW)
    configure_hands(None)  # gateway went away between staging and execution

    blocked = execute_staged_action(db, staged.id, now=NOW)

    assert blocked.status == "blocked"


def test_without_hands_gateway_types_are_rejected_at_staging(db):
    _captured(db)

    resolve_captured_intents(db, now=NOW)
    staged = stage_resolved_actions(db, now=NOW)

    assert staged == []  # media_playlist without a skill never stages


# ---------------------------------------------------------------------------
# OpenClawHands.invoke over the fake gateway transport
# ---------------------------------------------------------------------------


def test_invoke_posts_payload_and_idempotency_key():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/parker/v1/skills":
            return httpx.Response(200, json=SKILLS_PAYLOAD)
        seen["path"] = request.url.path
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"status": "ok", "detail": "queued 12 songs on the TV"})

    hands = OpenClawHands.discover(_gateway(handler))
    result = hands.invoke(
        "media_playlist",
        {"subject": "old Hindi songs"},
        idempotency_key="staged-action-7",
    )

    assert result.ok is True
    assert result.detail == "queued 12 songs on the TV"
    assert seen["path"] == "/parker/v1/skills/invoke"
    assert seen["payload"]["action_type"] == "media_playlist"
    assert seen["payload"]["idempotency_key"] == "staged-action-7"
    assert seen["payload"]["payload"]["skill"] == "youtube-tv"


def test_invoke_maps_gateway_error_to_failed_result_without_raising():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/parker/v1/skills":
            return httpx.Response(200, json=SKILLS_PAYLOAD)
        calls["count"] += 1
        raise httpx.ConnectError("gateway died mid-execution")

    hands = OpenClawHands.discover(_gateway(handler))
    result = hands.invoke("media_playlist", {}, idempotency_key="staged-action-9")

    assert result.ok is False
    assert "youtube-tv" in result.detail
    assert calls["count"] == 1  # exactly one attempt, no retry


def test_invoke_error_status_from_skill_is_a_failed_result():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/parker/v1/skills":
            return httpx.Response(200, json=SKILLS_PAYLOAD)
        return httpx.Response(200, json={"status": "error", "detail": "the TV rejected the cast"})

    hands = OpenClawHands.discover(_gateway(handler))
    result = hands.invoke("media_playlist", {}, idempotency_key="staged-action-3")

    assert result.ok is False
    assert result.detail == "the TV rejected the cast"


# ---------------------------------------------------------------------------
# Review: failures are visible, never silent
# ---------------------------------------------------------------------------


def test_failed_execution_appears_in_review_feed_and_ui(db):
    configure_hands(FakeHands(result=SkillResult(ok=False, detail="the TV is unreachable")))
    staged = _staged(db)
    confirm_staged_action(db, staged.id, confirmed_by="patient", now=NOW)
    execute_staged_action(db, staged.id, now=NOW)
    client = TestClient(app)

    review = client.get("/parker/review").json()

    assert len(review["recent_failed"]) == 1
    row = review["recent_failed"][0]
    assert row["status"] == "failed"
    assert "the TV is unreachable" in row["execution_result"]
    assert review["pending_actions"] == []  # failed is not pending — it needs eyes, not a retry button

    page = client.get("/parker/review/ui").text
    assert "Needs attention — skill failures" in page
    assert "recent_failed" in page
