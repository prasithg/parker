"""Tests for SQLAlchemy DB models."""

import json
from datetime import datetime

from app.db.models import CallLog, DoseLog, Medication, MoodEntry


def test_create_call_log(db):
    call = CallLog(call_sid="CA123", call_type="check_in")
    db.add(call)
    db.commit()

    saved = db.query(CallLog).first()
    assert saved.call_sid == "CA123"
    assert saved.call_type == "check_in"
    assert saved.started_at is not None
    assert saved.summary is None


def test_create_medication(db):
    med = Medication(
        name="Levodopa",
        dosage="100mg",
        schedule_times=json.dumps(["08:00", "14:00", "20:00"]),
        active=True,
    )
    db.add(med)
    db.commit()

    saved = db.query(Medication).first()
    assert saved.name == "Levodopa"
    times = json.loads(saved.schedule_times)
    assert len(times) == 3
    assert "08:00" in times


def test_create_dose_log(db):
    call = CallLog(call_sid="CA456", call_type="med_reminder")
    med = Medication(name="Sinemet", dosage="25/100", schedule_times='["09:00"]')
    db.add_all([call, med])
    db.commit()

    dose = DoseLog(
        call_log_id=call.id,
        medication_id=med.id,
        scheduled_time="09:00",
        confirmed=True,
        confirmed_at=datetime.utcnow(),
        patient_response="Yes I took it",
    )
    db.add(dose)
    db.commit()

    saved = db.query(DoseLog).first()
    assert saved.confirmed is True
    assert saved.call_log.call_sid == "CA456"


def test_create_mood_entry(db):
    call = CallLog(call_sid="CA789", call_type="evening_chat")
    db.add(call)
    db.commit()

    mood = MoodEntry(call_log_id=call.id, mood="good", notes="Feeling great today")
    db.add(mood)
    db.commit()

    saved = db.query(MoodEntry).first()
    assert saved.mood == "good"
    assert saved.notes == "Feeling great today"
    assert saved.call_log.call_type == "evening_chat"


def test_call_log_relationships(db):
    call = CallLog(call_sid="CA_REL", call_type="check_in")
    med = Medication(name="Pramipexole", dosage="0.5mg", schedule_times='["08:00"]')
    db.add_all([call, med])
    db.commit()

    dose = DoseLog(call_log_id=call.id, medication_id=med.id, scheduled_time="08:00", confirmed=False)
    mood = MoodEntry(call_log_id=call.id, mood="okay")
    db.add_all([dose, mood])
    db.commit()

    db.refresh(call)
    assert len(call.dose_logs) == 1
    assert len(call.mood_entries) == 1
