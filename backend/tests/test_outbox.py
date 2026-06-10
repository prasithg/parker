"""Family-message → local outbox vertical-slice tests.

v0 invariant under test: a confirmed family message "executes" by creating
a cancellable local outbox row, and nothing else. There is no send path.
"""

from datetime import datetime

from fastapi.testclient import TestClient

from app.conversation.tools import execute_tool
from app.db.models import CallLog, CapturedIntent, OutboxMessage
from app.main import app
from app.parker.pipeline import (
    approve_outbox_message,
    cancel_outbox_message,
    confirm_staged_action,
    execute_staged_action,
    resolve_captured_intents,
    stage_resolved_actions,
)

NOW = datetime(2026, 6, 9, 9, 0, 0)


def _call(db):
    call = CallLog(call_sid="CA_OUTBOX", call_type="check_in")
    db.add(call)
    db.commit()
    db.refresh(call)
    return call


def _captured_message(db, call, recipient="Sarah", text="I'd love to see the kids this weekend."):
    captured = CapturedIntent(
        call_log_id=call.id,
        intent_text=text,
        requested_action="message",
        subject=f"message {recipient}",
        recipient=recipient,
        due_at=NOW,
    )
    db.add(captured)
    db.commit()
    db.refresh(captured)
    return captured


def _staged_message(db, call, **kwargs):
    _captured_message(db, call, **kwargs)
    resolve_captured_intents(db, now=NOW)
    return stage_resolved_actions(db, now=NOW)[0]


def test_message_intent_resolves_and_stages_as_family_message(db):
    call = _call(db)
    _captured_message(db, call)

    resolutions = resolve_captured_intents(db, now=NOW)
    staged = stage_resolved_actions(db, now=NOW)

    assert len(resolutions) == 1
    assert resolutions[0].action_type == "family_message"
    assert resolutions[0].reversible is True
    assert len(staged) == 1
    assert staged[0].status == "staged"


def test_unconfirmed_message_is_blocked_and_creates_no_outbox_row(db):
    call = _call(db)
    staged = _staged_message(db, call)

    attempted = execute_staged_action(db, staged.id, now=NOW)

    assert attempted.status == "blocked"
    assert "confirmation" in attempted.execution_result
    assert db.query(OutboxMessage).count() == 0


def test_confirmed_message_executes_to_local_outbox_only(db):
    call = _call(db)
    staged = _staged_message(db, call)
    confirm_staged_action(db, staged.id, confirmed_by="patient", now=NOW)

    executed = execute_staged_action(db, staged.id, now=NOW)

    assert executed.status == "executed"
    assert "queued locally for Sarah" in executed.execution_result
    messages = db.query(OutboxMessage).all()
    assert len(messages) == 1
    message = messages[0]
    assert message.status == "queued_local"
    assert message.recipient == "Sarah"
    assert message.body == "I'd love to see the kids this weekend."
    assert message.staged_action_id == staged.id
    assert message.sent_at is None


def test_message_without_recipient_is_blocked_at_execution(db):
    call = _call(db)
    staged = _staged_message(db, call, recipient=None)
    confirm_staged_action(db, staged.id, confirmed_by="patient", now=NOW)

    executed = execute_staged_action(db, staged.id, now=NOW)

    assert executed.status == "blocked"
    assert "recipient" in executed.execution_result
    assert db.query(OutboxMessage).count() == 0


def test_cancel_queued_message(db):
    call = _call(db)
    staged = _staged_message(db, call)
    confirm_staged_action(db, staged.id, now=NOW)
    execute_staged_action(db, staged.id, now=NOW)
    message = db.query(OutboxMessage).one()

    cancelled = cancel_outbox_message(db, message.id, now=NOW)

    assert cancelled.status == "cancelled"
    assert cancelled.cancelled_at == NOW
    # Cancelling twice is a no-op, not an error.
    assert cancel_outbox_message(db, message.id, now=NOW).status == "cancelled"


def test_caregiver_approval_is_a_second_gate_that_stays_local(db):
    call = _call(db)
    staged = _staged_message(db, call)
    confirm_staged_action(db, staged.id, now=NOW)
    execute_staged_action(db, staged.id, now=NOW)
    message = db.query(OutboxMessage).one()
    assert message.status == "queued_local"

    approved = approve_outbox_message(db, message.id, approved_by="caregiver", now=NOW)

    assert approved.status == "approved_local"
    assert approved.approved_by == "caregiver"
    assert approved.approved_at == NOW
    assert approved.sent_at is None  # approval never implies sending


def test_approval_only_transitions_from_queued(db):
    call = _call(db)
    staged = _staged_message(db, call)
    confirm_staged_action(db, staged.id, now=NOW)
    execute_staged_action(db, staged.id, now=NOW)
    message = db.query(OutboxMessage).one()
    cancel_outbox_message(db, message.id, now=NOW)

    result = approve_outbox_message(db, message.id, now=NOW)

    assert result.status == "cancelled"  # no resurrection via approve
    assert result.approved_at is None


def test_approved_message_can_still_be_cancelled(db):
    call = _call(db)
    staged = _staged_message(db, call)
    confirm_staged_action(db, staged.id, now=NOW)
    execute_staged_action(db, staged.id, now=NOW)
    message = db.query(OutboxMessage).one()
    approve_outbox_message(db, message.id, now=NOW)

    cancelled = cancel_outbox_message(db, message.id, now=NOW)

    assert cancelled.status == "cancelled"
    assert cancelled.cancelled_at == NOW


def test_approve_endpoint_and_404(db):
    call = _call(db)
    staged = _staged_message(db, call)
    confirm_staged_action(db, staged.id, now=NOW)
    execute_staged_action(db, staged.id, now=NOW)
    message_id = db.query(OutboxMessage).one().id
    client = TestClient(app)

    response = client.post(f"/parker/outbox/{message_id}/approve", json={"approved_by": "caregiver"})

    assert response.status_code == 200
    assert response.json()["status"] == "approved_local"
    assert response.json()["approved_by"] == "caregiver"

    missing = client.post("/parker/outbox/9999/approve", json={})
    assert missing.status_code == 404


def test_capture_intent_tool_accepts_recipient(db):
    call = _call(db)

    result = execute_tool(
        db,
        call.id,
        "capture_intent",
        {
            "intent_text": "The plumber came today and it's all fixed.",
            "requested_action": "message",
            "recipient": "my son",
        },
    )

    assert result["status"] == "captured"
    saved = db.get(CapturedIntent, result["captured_intent_id"])
    assert saved.recipient == "my son"
    assert saved.requested_action == "message"


def test_message_flow_end_to_end_via_api(db):
    call = _call(db)
    _captured_message(db, call, recipient="Sarah", text="Dinner Sunday?")
    client = TestClient(app)

    tick = client.post("/parker/tick", json={"now": NOW.isoformat()})
    assert tick.json()["staged"] == 1

    resurface = client.get("/parker/resurface", params={"now": NOW.isoformat()})
    action = resurface.json()["actions"][0]
    # Confirmation restates exactly what will happen: recipient + message text.
    assert action["recipient"] == "Sarah"
    assert action["message_text"] == "Dinner Sunday?"

    client.post(f"/parker/actions/{action['id']}/confirm", json={"confirmed_by": "patient"})
    execute = client.post(f"/parker/actions/{action['id']}/execute", json={"now": NOW.isoformat()})
    assert execute.json()["status"] == "executed"

    outbox = client.get("/parker/outbox")
    messages = outbox.json()["messages"]
    assert len(messages) == 1
    assert messages[0]["status"] == "queued_local"
    assert messages[0]["body"] == "Dinner Sunday?"

    cancel = client.post(f"/parker/outbox/{messages[0]['id']}/cancel")
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"

    queued = client.get("/parker/outbox", params={"status": "queued_local"})
    assert queued.json()["messages"] == []


def test_cancel_missing_outbox_message_returns_404(db):
    client = TestClient(app)

    response = client.post("/parker/outbox/9999/cancel")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()
