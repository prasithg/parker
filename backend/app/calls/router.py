"""Call scheduling, triggering, and history endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.calls.handler import trigger_outbound_call
from app.db.database import get_db
from app.db.models import CallLog, Medication

router = APIRouter()


@router.post("/trigger")
async def trigger_call(
    call_type: str = "check_in",
    db: Session = Depends(get_db),
):
    """Manually trigger an outbound call."""
    call_log = trigger_outbound_call(db, call_type=call_type)
    return {
        "status": "triggered",
        "call_sid": call_log.call_sid,
        "call_type": call_log.call_type,
    }


@router.get("/history")
async def call_history(
    limit: int = 20,
    db: Session = Depends(get_db),
):
    """Return recent call logs with dose-log details."""
    calls = (
        db.query(CallLog)
        .order_by(CallLog.started_at.desc())
        .limit(limit)
        .all()
    )
    return [_call_payload(db, call) for call in calls]


def _call_payload(db: Session, call: CallLog) -> dict:
    return {
        "id": call.id,
        "call_sid": call.call_sid,
        "call_type": call.call_type,
        "started_at": call.started_at,
        "ended_at": call.ended_at,
        "duration_seconds": call.duration_seconds,
        "summary": call.summary,
        "mood": call.patient_mood,
        "dose_logs": [
            {
                "id": dose.id,
                "medication_id": dose.medication_id,
                "med_name": _med_name(db, dose.medication_id),
                "scheduled_time": dose.scheduled_time,
                "confirmed": dose.confirmed,
            }
            for dose in call.dose_logs
        ],
    }


def _med_name(db: Session, medication_id: int) -> str | None:
    medication = db.get(Medication, medication_id)
    return medication.name if medication else None
