"""Dashboard API endpoints for the family view."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import CallLog, Medication, MoodEntry
from app.meds.tracker import get_adherence_rate

router = APIRouter()


@router.get("/summary")
async def dashboard_summary(db: Session = Depends(get_db)):
    """Return the top-level dashboard summary."""

    since = datetime.utcnow() - timedelta(days=7)
    recent_calls_count = db.query(CallLog).filter(CallLog.started_at >= since).count()
    active_meds = db.query(Medication).filter(Medication.active.is_(True)).all()
    rates = [get_adherence_rate(db, med.id, days=7) for med in active_meds]
    latest_mood = db.query(MoodEntry).order_by(MoodEntry.recorded_at.desc()).first()
    last_call = db.query(CallLog).order_by(CallLog.started_at.desc()).first()
    alerts: list[dict] = []
    if rates and sum(rates) / len(rates) < 0.75:
        alerts.append({"type": "adherence", "message": "Medication adherence is below 75%."})
    if latest_mood and latest_mood.mood.lower() in {"low", "sad", "distressed"}:
        alerts.append({"type": "mood", "message": "Latest mood entry may need follow-up."})

    return {
        "recent_calls_count": recent_calls_count,
        "adherence_rate_7d": round(sum(rates) / len(rates), 4) if rates else 0.0,
        "latest_mood": latest_mood.mood if latest_mood else None,
        "last_call_at": last_call.started_at if last_call else None,
        "alerts": alerts,
    }


@router.get("/calls")
async def dashboard_calls(limit: int = 20, db: Session = Depends(get_db)):
    """Return recent calls with medication confirmations."""

    calls = db.query(CallLog).order_by(CallLog.started_at.desc()).limit(limit).all()
    return [
        {
            "id": call.id,
            "started_at": call.started_at,
            "duration": call.duration_seconds,
            "call_type": call.call_type,
            "summary": call.summary,
            "mood": call.patient_mood,
            "dose_logs": [
                {
                    "med_name": _med_name(db, dose.medication_id),
                    "confirmed": dose.confirmed,
                }
                for dose in call.dose_logs
            ],
        }
        for call in calls
    ]


@router.get("/medications")
async def dashboard_medications(db: Session = Depends(get_db)):
    """Return active medication schedule and adherence metrics."""

    medications = db.query(Medication).order_by(Medication.name.asc()).all()
    return [
        {
            "id": med.id,
            "name": med.name,
            "dosage": med.dosage,
            "schedule_times": _schedule_times(med.schedule_times),
            "active": med.active,
            "adherence_7d": get_adherence_rate(db, med.id, days=7),
            "adherence_30d": get_adherence_rate(db, med.id, days=30),
        }
        for med in medications
    ]


def _schedule_times(raw: str) -> list[str]:
    try:
        values = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [value for value in values if isinstance(value, str)] if isinstance(values, list) else []


def _med_name(db: Session, medication_id: int) -> str | None:
    med = db.get(Medication, medication_id)
    return med.name if med else None
