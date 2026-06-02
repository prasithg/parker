"""Parker capture → resolve → stage → resurface vertical-slice tests."""

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.conversation.tools import TOOL_DEFINITIONS, execute_tool
from app.db.models import CallLog, CapturedIntent, ResolutionResult, StagedAction
from app.parker.pipeline import (
    confirm_staged_action,
    execute_staged_action,
    get_due_resurfaced_actions,
    resolve_captured_intents,
    stage_resolved_actions,
)
from app.main import app


def _call(db):
    call = CallLog(call_sid="CA_PARKER", call_type="check_in")
    db.add(call)
    db.commit()
    db.refresh(call)
    return call


def test_capture_intent_tool_persists_pending_intent(db):
    call = _call(db)

    result = execute_tool(
        db,
        call.id,
        "capture_intent",
        {
            "intent_text": "Remind me to call Mary tomorrow morning.",
            "requested_action": "remind",
            "due_at": "2026-06-03T09:00:00",
            "subject": "call Mary",
        },
    )

    assert result["status"] == "captured"
    saved = db.get(CapturedIntent, result["captured_intent_id"])
    assert saved.call_log_id == call.id
    assert saved.intent_text == "Remind me to call Mary tomorrow morning."
    assert saved.requested_action == "remind"
    assert saved.subject == "call Mary"
    assert saved.due_at == datetime(2026, 6, 3, 9, 0, 0)
    assert saved.status == "pending"


def test_capture_intent_is_registered_as_openai_tool():
    names = {tool["function"]["name"] for tool in TOOL_DEFINITIONS}

    assert "capture_intent" in names


def test_due_intent_resolves_stages_resurfaces_confirms_and_executes(db):
    call = _call(db)
    captured = CapturedIntent(
        call_log_id=call.id,
        intent_text="Remind me to drink water in 10 minutes.",
        requested_action="remind",
        subject="drink water",
        due_at=datetime(2026, 6, 2, 10, 10, 0),
    )
    db.add(captured)
    db.commit()

    resolutions = resolve_captured_intents(db, now=datetime(2026, 6, 2, 10, 10, 0))

    assert len(resolutions) == 1
    assert resolutions[0].captured_intent_id == captured.id
    assert resolutions[0].status == "resolved"
    assert resolutions[0].action_type == "reminder"
    assert resolutions[0].reversible is True
    assert db.get(CapturedIntent, captured.id).status == "resolved"

    staged_actions = stage_resolved_actions(db, now=datetime(2026, 6, 2, 10, 10, 1))

    assert len(staged_actions) == 1
    staged = staged_actions[0]
    assert staged.resolution_result_id == resolutions[0].id
    assert staged.status == "staged"
    assert staged.action_type == "reminder"
    assert staged.execute_after == datetime(2026, 6, 2, 10, 10, 0)

    due = get_due_resurfaced_actions(db, now=datetime(2026, 6, 2, 10, 10, 1))

    assert [action.id for action in due] == [staged.id]
    assert due[0].resurface_count == 1
    assert due[0].last_resurfaced_at == datetime(2026, 6, 2, 10, 10, 1)

    confirmed = confirm_staged_action(db, staged.id, confirmed_by="patient", now=datetime(2026, 6, 2, 10, 11, 0))

    assert confirmed.status == "confirmed"
    assert confirmed.confirmed_by == "patient"

    executed = execute_staged_action(db, staged.id, now=datetime(2026, 6, 2, 10, 12, 0))

    assert executed.status == "executed"
    assert executed.executed_at == datetime(2026, 6, 2, 10, 12, 0)
    assert executed.execution_result == "reminder resurfaced: drink water"


def test_stager_refuses_irreversible_actions(db):
    call = _call(db)
    captured = CapturedIntent(
        call_log_id=call.id,
        intent_text="Order more medication without asking anyone.",
        requested_action="order_medication",
        subject="medication refill",
        due_at=datetime(2026, 6, 2, 10, 0, 0),
        status="resolved",
    )
    db.add(captured)
    db.commit()
    db.add(
        ResolutionResult(
            captured_intent_id=captured.id,
            status="resolved",
            action_type="order_medication",
            reversible=False,
            summary="Ordering medication is not reversible in v0.",
            execute_after=datetime(2026, 6, 2, 10, 0, 0),
        )
    )
    db.commit()

    staged = stage_resolved_actions(db, now=datetime(2026, 6, 2, 10, 0, 1))

    assert staged == []
    result = db.query(ResolutionResult).one()
    assert result.status == "rejected"
    assert "reversible" in result.summary
    assert db.query(StagedAction).count() == 0


def test_execute_requires_confirmation_and_reversible_action(db):
    call = _call(db)
    captured = CapturedIntent(call_log_id=call.id, intent_text="Ping me", requested_action="remind")
    db.add(captured)
    db.commit()
    resolution = ResolutionResult(
        captured_intent_id=captured.id,
        status="resolved",
        action_type="reminder",
        reversible=True,
        summary="Ping me",
    )
    db.add(resolution)
    db.commit()
    staged = StagedAction(
        resolution_result_id=resolution.id,
        status="staged",
        action_type="reminder",
        action_payload='{"subject": "Ping me"}',
    )
    db.add(staged)
    db.commit()

    attempted = execute_staged_action(db, staged.id)

    assert attempted.status == "blocked"
    assert "confirmation" in attempted.execution_result


def test_parker_api_ticks_resurfaces_confirms_and_executes(db):
    call = _call(db)
    db.add(
        CapturedIntent(
            call_log_id=call.id,
            intent_text="Remind me to stretch.",
            requested_action="remind",
            subject="stretch",
            due_at=datetime(2026, 6, 2, 10, 0, 0),
        )
    )
    db.commit()
    client = TestClient(app)

    tick = client.post("/parker/tick", json={"now": "2026-06-02T10:00:00"})

    assert tick.status_code == 200
    assert tick.json() == {"resolved": 1, "staged": 1}

    resurface = client.get("/parker/resurface", params={"now": "2026-06-02T10:00:01"})

    assert resurface.status_code == 200
    body = resurface.json()
    assert len(body["actions"]) == 1
    action_id = body["actions"][0]["id"]
    assert body["actions"][0]["subject"] == "stretch"

    confirm = client.post(f"/parker/actions/{action_id}/confirm", json={"confirmed_by": "caregiver"})

    assert confirm.status_code == 200
    assert confirm.json()["status"] == "confirmed"
    assert confirm.json()["confirmed_by"] == "caregiver"

    execute = client.post(f"/parker/actions/{action_id}/execute", json={"now": "2026-06-02T10:01:00"})

    assert execute.status_code == 200
    assert execute.json()["status"] == "executed"
    assert execute.json()["execution_result"] == "reminder resurfaced: stretch"
