"""Call scheduling, Twilio webhook, and call history endpoints."""

from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.calls.handler import (
    handle_call_completion,
    handle_twilio_voice_webhook,
    trigger_outbound_call,
)
from app.db.database import get_db
from app.db.models import CallLog, Medication

router = APIRouter()


@router.post("/trigger")
async def trigger_call(
    request: Request,
    call_type: str = "check_in",
    db: Session = Depends(get_db),
):
    """Manually trigger an outbound call."""
    base_url = str(request.base_url).rstrip("/")
    call_log = trigger_outbound_call(
        db,
        call_type=call_type,
        webhook_url=f"{base_url}/calls/twilio/voice",
    )
    return {
        "status": "triggered" if call_log else "failed",
        "call_sid": call_log.call_sid if call_log else "",
        "call_type": call_type,
    }


@router.get("/history")
async def call_history(
    limit: int = 20,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """Return recent call logs."""
    calls = (
        db.query(CallLog)
        .order_by(CallLog.started_at.desc())
        .offset(offset)
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


@router.post("/twilio/voice")
async def twilio_voice(request: Request):
    """Twilio voice webhook for Say/Gather conversation flow."""

    twiml = await handle_twilio_voice_webhook(request)
    return Response(content=twiml, media_type="application/xml")


@router.post("/twilio/status")
async def twilio_status(request: Request):
    """Twilio status callback endpoint."""

    form = parse_qs((await request.body()).decode("utf-8"))
    call_sid = form.get("CallSid", [""])[0]
    status = form.get("CallStatus", [""])[0]
    duration_raw = form.get("CallDuration", [""])[0]
    recording_url = form.get("RecordingUrl", [None])[0]
    duration = int(duration_raw) if duration_raw.isdigit() else None
    if status in {"completed", "no-answer", "busy", "failed", "canceled"}:
        handle_call_completion(call_sid, duration, recording_url)
    return {"status": "ok"}


def _med_name(db: Session, mid: int) -> str | None:
    m = db.get(Medication, mid)
    return m.name if m else None
