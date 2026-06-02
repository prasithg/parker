"""Tests for dose verification workflow."""

import json
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.db.models import CallLog, DoseLog, DoseVerification, Medication
from app.escalation.models import Escalation
from app.main import app
from app.meds.tracker import log_dose
from app.meds.verification import process_due_verification_windows


def _medication(db):
    med = Medication(
        name="Levodopa",
        dosage="100mg",
        schedule_times=json.dumps(["08:00"]),
        active=True,
    )
    db.add(med)
    db.commit()
    db.refresh(med)
    return med


def _call(db, call_type="med_reminder", started_at=None):
    call = CallLog(
        call_sid=f"CA_DV_{datetime.utcnow().timestamp()}",
        call_type=call_type,
        started_at=started_at or datetime.utcnow(),
    )
    db.add(call)
    db.commit()
    db.refresh(call)
    return call


def _dose(db, confirmed=False, call_type="med_reminder", started_at=None):
    med = _medication(db)
    call = _call(db, call_type=call_type, started_at=started_at)
    return log_dose(db, call.id, med.id, "08:00", confirmed=confirmed)


def test_create_verification_happy_path(db):
    dose = _dose(db)
    client = TestClient(app)

    response = client.post(
        f"/doses/{dose.id}/verifications",
        json={
            "verification_type": "photo",
            "image_path": "/tmp/dose.jpg",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["dose_id"] == dose.id
    assert body["verification_type"] == "photo"
    assert body["status"] == "verified"
    assert db.get(DoseLog, dose.id).confirmed is True


def test_verify_dose_inside_window_transitions_dose_status(db):
    dose = _dose(db, started_at=datetime.utcnow() - timedelta(minutes=10))
    client = TestClient(app)

    response = client.post(
        f"/calls/{dose.call_log_id}/verify-dose",
        json={
            "dose_id": dose.id,
            "verification_type": "text",
            "text_attestation": "Patient showed and confirmed the dose.",
        },
    )

    assert response.status_code == 200
    saved = db.get(DoseLog, dose.id)
    assert saved.confirmed is True
    assert saved.confirmed_at is not None
    assert response.json()["status"] == "verified"


def test_missed_window_produces_missed_dose_escalation(db, monkeypatch):
    monkeypatch.setattr("app.escalation.engine.dispatch_notifications", lambda escalation, contacts: [])
    dose = _dose(db, started_at=datetime.utcnow() - timedelta(minutes=45))

    escalations = process_due_verification_windows(db, now=datetime.utcnow(), window_minutes=30)

    assert len(escalations) == 1
    assert escalations[0].severity == "missed-dose"
    assert db.query(Escalation).filter(Escalation.severity == "missed-dose").count() == 1
    marker = db.query(DoseVerification).filter(DoseVerification.dose_id == dose.id).one()
    assert marker.status == "missed"


def test_caregiver_attested_path(db):
    dose = _dose(db)
    client = TestClient(app)

    response = client.post(
        f"/doses/{dose.id}/verifications",
        json={
            "verification_type": "caregiver_attested",
            "text_attestation": "Caregiver watched him take it.",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["verification_type"] == "caregiver_attested"
    assert body["text_attestation"] == "Caregiver watched him take it."
    assert db.get(DoseLog, dose.id).confirmed is True


def test_list_verifications_for_dose(db):
    dose = _dose(db)
    client = TestClient(app)
    client.post(
        f"/doses/{dose.id}/verifications",
        json={"verification_type": "text", "text_attestation": "first"},
    )
    client.post(
        f"/doses/{dose.id}/verifications",
        json={"verification_type": "caregiver_attested", "text_attestation": "second"},
    )

    response = client.get(f"/doses/{dose.id}/verifications")

    assert response.status_code == 200
    body = response.json()
    assert [item["text_attestation"] for item in body if item["status"] == "verified"] == [
        "first",
        "second",
    ]
