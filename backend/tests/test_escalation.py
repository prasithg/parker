"""Tests for escalation engine and routes."""

import json
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.db.models import CallLog
from app.escalation.engine import (
    acknowledge_escalation,
    auto_escalate_check,
    create_escalation,
    get_open_escalations,
    resolve_escalation,
)
from app.escalation.models import Escalation
from app.escalation.notifier import FamilyContact, dispatch_notifications
from app.main import app


def _call(db, sid="CA_ESC"):
    call = CallLog(call_sid=sid, call_type="check_in")
    db.add(call)
    db.commit()
    return call


def test_create_escalation_with_mocked_dispatch(db, monkeypatch):
    call = _call(db)
    monkeypatch.setattr("app.escalation.engine.get_family_contacts", lambda: [FamilyContact("A", "+1", role="primary_caregiver")])
    monkeypatch.setattr("app.escalation.engine.dispatch_notifications", lambda escalation, contacts: [c.identifier for c in contacts])

    escalation = create_escalation(db, call.id, "fell today", "urgent")

    assert escalation.id is not None
    assert escalation.status == "open"
    assert escalation.severity == "urgent"
    assert json.loads(escalation.notified_contacts) == ["+1"]


def test_dispatch_notifications_routes_by_severity(monkeypatch):
    contacts = [
        FamilyContact("Primary", "1", role="primary_caregiver"),
        FamilyContact("Family", "2", role="family"),
        FamilyContact("Emergency", "3", role="emergency"),
    ]
    sent = []
    monkeypatch.setattr("app.escalation.notifier.notify_contact", lambda contact, escalation: sent.append(contact.identifier) or True)
    escalation = Escalation(call_log_id=1, severity="warning", reason="test")

    assert dispatch_notifications(escalation, contacts) == ["1", "2"]
    assert sent == ["1", "2"]


def test_open_escalations_order_ack_resolve(db, monkeypatch):
    call = _call(db, "CA_ORDER")
    monkeypatch.setattr("app.escalation.engine.dispatch_notifications", lambda escalation, contacts: [])
    info = create_escalation(db, call.id, "FYI", "info")
    urgent = create_escalation(db, call.id, "bad", "urgent")

    open_items = get_open_escalations(db)
    assert [item.id for item in open_items] == [urgent.id, info.id]

    acknowledged = acknowledge_escalation(db, urgent.id)
    assert acknowledged.status == "acknowledged"
    assert acknowledged.acknowledged_at is not None

    resolved = resolve_escalation(db, info.id, "handled")
    assert resolved.status == "resolved"
    assert resolved.resolution_notes == "handled"


def test_auto_escalate_warning_after_30_minutes(db, monkeypatch):
    call = _call(db, "CA_AUTO")
    monkeypatch.setattr("app.escalation.engine.dispatch_notifications", lambda escalation, contacts: ["notified"])
    escalation = Escalation(
        call_log_id=call.id,
        severity="warning",
        reason="missed meds",
        status="open",
        created_at=datetime.utcnow() - timedelta(minutes=31),
    )
    db.add(escalation)
    db.commit()

    escalated = auto_escalate_check(db)

    assert len(escalated) == 1
    assert escalated[0].severity == "urgent"
    assert json.loads(escalated[0].notified_contacts) == ["notified"]


def test_escalation_router_endpoints(db):
    call = _call(db, "CA_ROUTE")
    escalation = Escalation(call_log_id=call.id, severity="warning", reason="concern", status="open")
    db.add(escalation)
    db.commit()

    client = TestClient(app)
    response = client.get("/escalations/")
    assert response.status_code == 200
    assert response.json()[0]["reason"] == "concern"

    response = client.post(f"/escalations/{escalation.id}/acknowledge")
    assert response.status_code == 200
    assert response.json()["status"] == "acknowledged"

    response = client.post(f"/escalations/{escalation.id}/resolve", json={"notes": "ok"})
    assert response.status_code == 200
    assert response.json()["status"] == "resolved"

    response = client.get("/escalations/history")
    assert response.status_code == 200
    assert response.json()[0]["resolution_notes"] == "ok"


def test_conversation_tool_creates_escalation(db, monkeypatch):
    from app.conversation.tools import execute_tool

    call = _call(db, "CA_TOOL_ESC")
    monkeypatch.setattr("app.escalation.engine.dispatch_notifications", lambda escalation, contacts: [])

    result = execute_tool(
        db,
        call.id,
        "escalate_to_family",
        {"reason": "seemed confused", "severity": "warning"},
    )

    assert result["status"] == "escalated"
    assert result["escalation_id"]
    assert db.get(Escalation, result["escalation_id"]).reason == "seemed confused"
