"""Tests for medication tracking logic."""

import json
from datetime import datetime, timedelta

from app.db.models import CallLog, DoseLog, Medication
from app.meds.tracker import (
    get_adherence_calendar,
    get_adherence_rate,
    get_due_medications,
    log_dose,
)


def _add_medication(db, name="Levodopa", dosage="100mg", times=None):
    times = times or ["08:00", "14:00", "20:00"]
    med = Medication(name=name, dosage=dosage, schedule_times=json.dumps(times), active=True)
    db.add(med)
    db.commit()
    db.refresh(med)
    return med


def _add_call(db, call_type="med_reminder"):
    call = CallLog(call_sid=f"CA_{datetime.utcnow().timestamp()}", call_type=call_type)
    db.add(call)
    db.commit()
    db.refresh(call)
    return call


class TestGetDueMedications:
    def test_returns_med_within_30_min(self, db):
        _add_medication(db, times=["08:00"])
        now = datetime(2026, 4, 25, 7, 45)  # 15 min before 08:00
        due = get_due_medications(db, now)
        assert len(due) == 1
        medication, scheduled_time = due[0]
        assert medication.name == "Levodopa"
        assert scheduled_time == "08:00"

    def test_excludes_med_outside_window(self, db):
        _add_medication(db, times=["08:00"])
        now = datetime(2026, 4, 25, 7, 0)  # 60 min before
        due = get_due_medications(db, now)
        assert len(due) == 0

    def test_excludes_inactive_medication(self, db):
        med = _add_medication(db, times=["08:00"])
        med.active = False
        db.commit()
        now = datetime(2026, 4, 25, 7, 45)
        due = get_due_medications(db, now)
        assert len(due) == 0

    def test_multiple_times_returns_only_due(self, db):
        _add_medication(db, times=["08:00", "14:00", "20:00"])
        now = datetime(2026, 4, 25, 13, 45)  # 15 min before 14:00
        due = get_due_medications(db, now)
        assert len(due) == 1


class TestLogDose:
    def test_log_confirmed_dose(self, db):
        med = _add_medication(db)
        call = _add_call(db)
        dose = log_dose(db, call.id, med.id, "08:00", confirmed=True, patient_response="Took it")
        assert dose.confirmed is True
        assert dose.confirmed_at is not None
        assert dose.patient_response == "Took it"

    def test_log_missed_dose(self, db):
        med = _add_medication(db)
        call = _add_call(db)
        dose = log_dose(db, call.id, med.id, "08:00", confirmed=False, patient_response="Forgot")
        assert dose.confirmed is False
        assert dose.confirmed_at is None


class TestGetAdherenceRate:
    def test_perfect_adherence(self, db):
        med = _add_medication(db, times=["08:00"])
        call = _add_call(db)
        # 7 days of confirmed doses
        for i in range(7):
            dose = DoseLog(
                call_log_id=call.id,
                medication_id=med.id,
                scheduled_time="08:00",
                confirmed=True,
                confirmed_at=datetime.utcnow() - timedelta(days=i),
            )
            db.add(dose)
        db.commit()
        rate = get_adherence_rate(db, med.id, days=7)
        assert rate == 1.0

    def test_partial_adherence(self, db):
        med = _add_medication(db, times=["08:00"])
        call = _add_call(db)
        for i in range(7):
            dose = DoseLog(
                call_log_id=call.id,
                medication_id=med.id,
                scheduled_time="08:00",
                confirmed=(i % 2 == 0),  # 4 out of 7
                confirmed_at=datetime.utcnow() - timedelta(days=i) if i % 2 == 0 else None,
            )
            db.add(dose)
        db.commit()
        rate = get_adherence_rate(db, med.id, days=7)
        assert 0.50 < rate < 0.60  # 4/7 ≈ 57.1%

    def test_no_logs_returns_zero(self, db):
        med = _add_medication(db, times=["08:00"])
        rate = get_adherence_rate(db, med.id, days=7)
        assert rate == 0.0


class TestGetAdherenceCalendar:
    def test_returns_entries_for_each_day(self, db):
        med = _add_medication(db, times=["08:00"])
        call = _add_call(db)
        # Add a confirmed dose for today
        dose = DoseLog(
            call_log_id=call.id,
            medication_id=med.id,
            scheduled_time="08:00",
            confirmed=True,
            confirmed_at=datetime.utcnow(),
        )
        db.add(dose)
        db.commit()
        calendar = get_adherence_calendar(db, med.id, days=7)
        assert isinstance(calendar, list)
        assert len(calendar) == 7
        # Today should show taken
        today_entry = next(e for e in calendar if e["date"] == datetime.utcnow().strftime("%Y-%m-%d"))
        assert today_entry["scheduled"] == 1
        assert today_entry["confirmed"] == 1
