"""APScheduler setup for routine ParkinsClaw calls."""

from __future__ import annotations

import json
import logging
from datetime import datetime, time, timedelta
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session

from app.calls.handler import trigger_outbound_call
from app.config import settings
from app.db.database import SessionLocal
from app.db.models import Medication

logger = logging.getLogger("parkinsclaw.scheduler")

_scheduler: BackgroundScheduler | None = None
_db_session_factory: Callable[[], Session] = SessionLocal


def setup_scheduler(
    scheduler: BackgroundScheduler,
    db_session_factory: Callable[[], Session],
) -> None:
    """Register recurring call jobs on an APScheduler instance."""

    global _db_session_factory, _scheduler
    _db_session_factory = db_session_factory
    _scheduler = scheduler

    db = db_session_factory()
    try:
        schedule = compute_call_schedule(db)
    finally:
        db.close()

    for entry in schedule:
        hour, minute = _split_hhmm(entry["time"])
        scheduler.add_job(
            _initiate_with_retry,
            "cron",
            hour=hour,
            minute=minute,
            id=f"{entry['call_type']}-{entry['time']}",
            replace_existing=True,
            kwargs={"call_type": entry["call_type"]},
        )


def _is_quiet_hours(now: datetime | None = None) -> bool:
    """Return true before 8:00 AM or after 9:00 PM."""

    current = (now or datetime.now()).time()
    return current < time(8, 0) or current > time(21, 0)


def compute_call_schedule(db: Session) -> list[dict]:
    """Compute daily check-ins and deduped med reminders from active meds."""

    medication_times: dict[str, list[int]] = {}
    medications = (
        db.query(Medication).filter(Medication.active.is_(True)).order_by(Medication.id).all()
    )
    for medication in medications:
        for scheduled_time in _parse_schedule(medication.schedule_times):
            reminder_time = _minus_minutes(scheduled_time, 15)
            medication_times.setdefault(reminder_time, []).append(medication.id)

    schedule = [
        {"time": "08:30", "call_type": "check_in", "medication_ids": []},
        {"time": "19:00", "call_type": "evening_chat", "medication_ids": []},
    ]
    for reminder_time, medication_ids in sorted(medication_times.items()):
        schedule.append(
            {
                "time": reminder_time,
                "call_type": "med_reminder",
                "medication_ids": medication_ids,
            }
        )
    return sorted(schedule, key=lambda item: item["time"])


def _initiate_with_retry(call_type: str, max_retries: int = 2, attempt: int = 0) -> str:
    """Initiate a scheduled call; retry later if initiation fails."""

    if _is_quiet_hours():
        logger.info("Skipping %s during quiet hours", call_type)
        return ""

    call_sid = trigger_outbound_call(
        to_number=settings.patient_phone_number,
        call_type=call_type,
        webhook_url="/calls/twilio/voice",
        db_session_factory=_db_session_factory,
    )
    if not call_sid and attempt < max_retries and _scheduler is not None:
        _scheduler.add_job(
            _initiate_with_retry,
            "date",
            run_date=datetime.now() + timedelta(minutes=15),
            kwargs={
                "call_type": call_type,
                "max_retries": max_retries,
                "attempt": attempt + 1,
            },
        )
    return call_sid


def start_scheduler(
    db_session_factory: Callable[[], Session] = SessionLocal,
) -> BackgroundScheduler:
    """Create, configure, and start the global scheduler."""

    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler
    scheduler = BackgroundScheduler(timezone="America/New_York")
    setup_scheduler(scheduler, db_session_factory)
    scheduler.start()
    _scheduler = scheduler
    return scheduler


def stop_scheduler() -> None:
    """Shutdown the global scheduler if it is running."""

    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None


def is_quiet_hours(value: time | datetime | None = None) -> bool:
    """Backward-compatible quiet-hours wrapper."""

    if isinstance(value, time):
        return value < time(8, 0) or value > time(21, 0)
    return _is_quiet_hours(value)


def should_retry(attempt: int, max_retries: int = 2) -> bool:
    """Return whether another retry is allowed."""

    return attempt < max_retries


def build_call_schedule(med_times: list[str]) -> list[dict]:
    """Build a schedule from raw medication times without a database."""

    entries = [
        {"time": "08:30", "call_type": "check_in", "medication_ids": []},
        {"time": "19:00", "call_type": "evening_chat", "medication_ids": []},
    ]
    for med_time in sorted(set(med_times)):
        entries.append(
            {
                "time": _minus_minutes(med_time, 15),
                "call_type": "med_reminder",
                "medication_ids": [],
            }
        )
    return sorted(entries, key=lambda item: item["time"])


def _parse_schedule(raw: str) -> list[str]:
    try:
        values = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(values, list):
        return []
    return [value for value in values if isinstance(value, str) and _valid_hhmm(value)]


def _valid_hhmm(value: str) -> bool:
    try:
        _split_hhmm(value)
    except ValueError:
        return False
    return True


def _split_hhmm(value: str) -> tuple[int, int]:
    hour_str, minute_str = value.split(":", 1)
    hour = int(hour_str)
    minute = int(minute_str)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Invalid time: {value}")
    return hour, minute


def _minus_minutes(value: str, minutes: int) -> str:
    hour, minute = _split_hhmm(value)
    base = datetime(2000, 1, 1, hour, minute)
    adjusted = base - timedelta(minutes=minutes)
    return adjusted.strftime("%H:%M")
