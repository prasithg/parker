"""Caregiver review surface tests: cancel control, review feed, HTML page."""

from datetime import datetime

from fastapi.testclient import TestClient

from app.db.models import CallLog, CapturedIntent, OutboxMessage
from app.escalation.candidates import flag_non_response_candidates
from app.main import app
from app.parker.pipeline import (
    cancel_staged_action,
    confirm_staged_action,
    execute_staged_action,
    resolve_captured_intents,
    stage_resolved_actions,
)

NOW = datetime(2026, 6, 10, 9, 0, 0)


def _call(db, sid="CA_REVIEW"):
    call = CallLog(call_sid=sid, call_type="check_in")
    db.add(call)
    db.commit()
    db.refresh(call)
    return call


def _staged(db, call, requested_action="remind", subject="stretch", recipient=None, text=None):
    db.add(
        CapturedIntent(
            call_log_id=call.id,
            intent_text=text or f"Please {subject}",
            requested_action=requested_action,
            subject=subject,
            recipient=recipient,
            due_at=NOW,
        )
    )
    db.commit()
    resolve_captured_intents(db, now=NOW)
    return stage_resolved_actions(db, now=NOW)[-1]


def test_cancel_staged_action_before_confirmation(db):
    call = _call(db)
    staged = _staged(db, call)

    cancelled = cancel_staged_action(db, staged.id, cancelled_by="caregiver", now=NOW)

    assert cancelled.status == "cancelled"
    assert "cancelled by caregiver" in cancelled.execution_result


def test_cancel_confirmed_action_before_execution(db):
    call = _call(db)
    staged = _staged(db, call)
    confirm_staged_action(db, staged.id, now=NOW)

    cancelled = cancel_staged_action(db, staged.id, now=NOW)

    assert cancelled.status == "cancelled"
    # Cancelled actions cannot be executed afterwards.
    executed = execute_staged_action(db, staged.id, now=NOW)
    assert executed.status == "blocked"


def test_cancel_executed_action_is_a_no_op(db):
    call = _call(db)
    staged = _staged(db, call)
    confirm_staged_action(db, staged.id, now=NOW)
    execute_staged_action(db, staged.id, now=NOW)

    result = cancel_staged_action(db, staged.id, now=NOW)

    assert result.status == "executed"


def test_action_endpoints_return_typed_404_for_missing_ids(db):
    client = TestClient(app)

    for path in ("confirm", "execute", "cancel"):
        response = client.post(f"/parker/actions/9999/{path}", json={})
        assert response.status_code == 404, path
        assert "not found" in response.json()["detail"].lower()


def test_cancel_endpoint_records_canceller(db):
    call = _call(db)
    staged = _staged(db, call)
    client = TestClient(app)

    response = client.post(
        f"/parker/actions/{staged.id}/cancel", json={"cancelled_by": "caregiver"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert "caregiver" in response.json()["execution_result"]


def test_review_feed_aggregates_everything_awaiting_a_decision(db):
    call = _call(db)
    reminder = _staged(db, call, subject="water plants")
    message = _staged(
        db, call, requested_action="message", subject="msg", recipient="Sarah", text="Dinner Sunday?"
    )
    confirm_staged_action(db, message.id, now=NOW)
    execute_staged_action(db, message.id, now=NOW)  # → outbox queued_local
    # Manufacture a non-response candidate.
    stale = _staged(db, call, subject="afternoon walk")
    stale.resurface_count = 3
    stale.last_resurfaced_at = datetime(2026, 6, 10, 7, 0, 0)
    db.commit()
    flag_non_response_candidates(db, now=NOW, resurface_threshold=3, quiet_minutes=30)
    client = TestClient(app)

    review = client.get("/parker/review").json()

    pending_ids = {item["id"] for item in review["pending_actions"]}
    assert reminder.id in pending_ids
    assert stale.id in pending_ids
    assert message.id not in pending_ids  # executed actions need no decision
    assert len(review["outbox_queued"]) == 1
    assert review["outbox_queued"][0]["recipient"] == "Sarah"
    assert review["outbox_approved"] == []
    assert len(review["escalation_candidates"]) == 1
    assert "Non-response candidate" in review["escalation_candidates"][0]["reason"]
    assert review["open_escalations"] == []


def test_review_ui_serves_local_html_page(db):
    client = TestClient(app)

    response = client.get("/parker/review/ui")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "caregiver review" in response.text
    assert "never sent" in response.text
    # The page only talks to local endpoints.
    assert "http://" not in response.text.replace("http://localhost", "")
    assert "https://" not in response.text


def test_approved_message_moves_between_review_buckets(db):
    call = _call(db)
    message = _staged(
        db, call, requested_action="message", subject="msg", recipient="Sarah", text="Hi"
    )
    confirm_staged_action(db, message.id, now=NOW)
    execute_staged_action(db, message.id, now=NOW)
    client = TestClient(app)
    outbox_id = db.query(OutboxMessage).one().id

    client.post(f"/parker/outbox/{outbox_id}/approve", json={"approved_by": "caregiver"})
    review = client.get("/parker/review").json()

    assert review["outbox_queued"] == []
    assert len(review["outbox_approved"]) == 1
    assert review["outbox_approved"][0]["approved_by"] == "caregiver"


def test_review_ui_includes_approve_control(db):
    client = TestClient(app)

    page = client.get("/parker/review/ui").text

    assert "Approve (stays local)" in page
    assert "/approve" in page
    assert "still local only" in page


def test_cancelled_outbox_message_leaves_review_feed(db):
    call = _call(db)
    message = _staged(
        db, call, requested_action="message", subject="msg", recipient="Sarah", text="Hi"
    )
    confirm_staged_action(db, message.id, now=NOW)
    execute_staged_action(db, message.id, now=NOW)
    client = TestClient(app)
    outbox_id = db.query(OutboxMessage).one().id

    client.post(f"/parker/outbox/{outbox_id}/cancel")
    review = client.get("/parker/review").json()

    assert review["outbox_queued"] == []
