"""Call triggering and history endpoints."""

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
async def call_history(limit: int = 20, db: Session = Depends(get_db)):
    """Return recent call logs."""
    calls = (
        db.query(CallLog)
        .order_by(CallLog.started_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": c.id,
            "call_sid": c.call_sid,
            "call_type": c.call_type,
            "started_at": c.started_at,
            "ended_at": c.ended_at,
            "duration_seconds": c.duration_seconds,
            "summary": c.summary,
            "mood": c.patient_mood,
            "dose_logs": [
                {
                    "medication_id": d.medication_id,
                    "med_name": _med_name(db, d.medication_id),
                    "confirmed": d.confirmed,
                }
                for d in c.dose_logs
            ],
        }
        for c in calls
    ]


def _med_name(db: Session, mid: int) -> str | None:
    m = db.get(Medication, mid)
    return m.name if m else None
