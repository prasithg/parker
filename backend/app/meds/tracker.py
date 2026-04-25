"""Medication tracking logic for schedules, dose logs, and adherence."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import CallLog, DoseLog, Medication

logger = logging.getLogger("parkinsclaw.meds")


def get_due_medications(
    db: Session,
    now: datetime | None = None,
    window_minutes: int = 30,
) -> list[Medication]:
    """Return active medications due within the next window_minutes of now."""
    now = now or datetime.utcnow()
    window_end = now + timedelta(minutes=window_minutes)
    due: list[Medication] = []

    for med in db.query(Medication).filter(Medication.active.is_(True)).all():
        for sched_time in _parse_schedule_times(med.schedule_times):
            scheduled_at = _datetime_for_time(now, sched_time)
            if now <= scheduled_at <= window_end:
                due.append(med)
                break
    return due


def log_dose(
    db: Session,
    call_log_id: int,
    medication_id: int,
    scheduled_time: str,
    confirmed: bool,
    patient_response: str | None = None,
) -> DoseLog:
    """Create a dose log row for a scheduled medication dose."""
    dose = DoseLog(
        call_log_id=call_log_id,
        medication_id=medication_id,
        scheduled_time=scheduled_time,
        confirmed=confirmed,
        confirmed_at=datetime.utcnow() if confirmed else None,
        patient_response=patient_response,
    )
    db.add(dose)
    db.commit()
    db.refresh(dose)
    return dose


def get_adherence_rate(db: Session, medication_id: int, days: int = 7) -> float:
    """Return confirmed dose percentage (0-100) for the medication over last N days."""
    logs = _dose_logs_in_window(db, medication_id, days)
    if not logs:
        return 0.0
    confirmed = sum(1 for dose in logs if dose.confirmed)
    return round((confirmed / len(logs)) * 100, 1)


def get_adherence_calendar(
    db: Session,
    medication_id: int,
    days: int = 30,
) -> list[dict[str, Any]]:
    """Return daily taken/missed counts for each day in the period."""
    today = datetime.utcnow().date()
    start_date = today - timedelta(days=days - 1)
    logs = _dose_logs_since(db, medication_id, start_date)

    buckets: dict[date, dict[str, Any]] = {}
    for offset in range(days):
        day = start_date + timedelta(days=offset)
        buckets[day] = {"date": day.isoformat(), "taken": 0, "missed": 0}

    for dose in logs:
        day = _dose_event_datetime(dose).date()
        if day not in buckets:
            continue
        if dose.confirmed:
            buckets[day]["taken"] += 1
        else:
            buckets[day]["missed"] += 1

    return [buckets[start_date + timedelta(days=offset)] for offset in range(days)]


def _parse_schedule_times(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, str) and _parse_hhmm(item) is not None]


def _parse_hhmm(value: str) -> time | None:
    try:
        hour, minute = value.split(":", 1)
        return time(hour=int(hour), minute=int(minute))
    except (TypeError, ValueError):
        return None


def _datetime_for_time(now: datetime, value: str) -> datetime:
    parsed = _parse_hhmm(value)
    if parsed is None:
        return now + timedelta(days=3650)
    return now.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)


def _dose_logs_in_window(db: Session, medication_id: int, days: int) -> list[DoseLog]:
    start_at = datetime.utcnow() - timedelta(days=days)
    return (
        db.query(DoseLog)
        .join(CallLog, DoseLog.call_log_id == CallLog.id)
        .filter(DoseLog.medication_id == medication_id)
        .filter(func.coalesce(DoseLog.confirmed_at, CallLog.started_at) >= start_at)
        .all()
    )


def _dose_logs_since(db: Session, medication_id: int, start_date: date) -> list[DoseLog]:
    start_at = datetime.combine(start_date, time.min)
    return (
        db.query(DoseLog)
        .join(CallLog, DoseLog.call_log_id == CallLog.id)
        .filter(DoseLog.medication_id == medication_id)
        .filter(func.coalesce(DoseLog.confirmed_at, CallLog.started_at) >= start_at)
        .all()
    )


def _dose_event_datetime(dose: DoseLog) -> datetime:
    if dose.confirmed_at:
        return dose.confirmed_at
    if dose.call_log and dose.call_log.started_at:
        return dose.call_log.started_at
    return datetime.utcnow()
