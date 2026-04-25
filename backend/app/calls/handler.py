"""Twilio call creation and webhook handling."""

from __future__ import annotations

import html
import logging
from datetime import datetime
from typing import Callable
from urllib.parse import parse_qs

from fastapi import Request
from sqlalchemy.orm import Session

from app.config import settings
from app.conversation.agent import ConversationAgent
from app.db.database import SessionLocal
from app.db.models import CallLog

logger = logging.getLogger("parkinsclaw.calls")

_agent = ConversationAgent(SessionLocal)


def get_twilio_client():
    """Return a Twilio client when credentials are configured, else None."""

    if not (
        settings.twilio_account_sid
        and settings.twilio_auth_token
        and settings.twilio_phone_number
    ):
        return None
    try:
        from twilio.rest import Client
    except Exception:
        logger.exception("Twilio package is unavailable")
        return None
    return Client(settings.twilio_account_sid, settings.twilio_auth_token)


def trigger_outbound_call(
    to_number: str,
    call_type: str,
    webhook_url: str,
    db_session_factory: Callable[[], Session] = SessionLocal,
) -> str:
    """Create a CallLog and initiate an outbound Twilio call if configured."""

    db = db_session_factory()
    should_close = db_session_factory is SessionLocal
    try:
        call = CallLog(
            call_sid=f"pending-{datetime.utcnow().timestamp()}",
            call_type=call_type,
        )
        db.add(call)
        db.commit()
        db.refresh(call)

        client = get_twilio_client()
        if client is None or not to_number:
            call.call_sid = f"LOCAL{call.id}"
            db.commit()
            logger.info("Twilio not configured; created local call %s", call.call_sid)
            return call.call_sid

        twilio_call = client.calls.create(
            to=to_number,
            from_=settings.twilio_phone_number,
            url=webhook_url,
            status_callback=webhook_url.replace("/voice", "/status"),
            status_callback_event=["completed", "no-answer", "busy", "failed"],
        )
        call.call_sid = twilio_call.sid
        db.commit()
        return twilio_call.sid
    except Exception:
        logger.exception("Failed to trigger outbound call")
        db.rollback()
        return ""
    finally:
        if should_close:
            db.close()


async def handle_twilio_voice_webhook(request: Request) -> str:
    """Return TwiML for a simple Say/Gather conversation loop."""

    form = await _read_twilio_form(request)
    call_sid = form.get("CallSid", [""])[0]
    speech_result = form.get("SpeechResult", [""])[0] or form.get("Digits", [""])[0]

    db = SessionLocal()
    try:
        call = _get_or_create_call(db, call_sid, form.get("call_type", ["check_in"])[0])
        if speech_result:
            assistant_text = _agent.handle_user_turn(call.id, speech_result)
        else:
            messages = _agent.start_conversation(
                call_log_id=call.id,
                call_type=call.call_type,
                patient_name=settings.patient_name,
            )
            assistant_text = messages[-1]["content"]
    finally:
        db.close()

    action_url = html.escape(str(request.url))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Gather input="speech dtmf" action="{action_url}" method="POST" '
        'speechTimeout="auto" timeout="5">'
        f"<Say>{html.escape(assistant_text)}</Say>"
        "</Gather>"
        "<Say>I will check in again later. Goodbye.</Say>"
        "</Response>"
    )


def handle_call_completion(
    call_sid: str,
    duration_seconds: int | None,
    recording_url: str | None = None,
) -> None:
    """Update a completed call and summarize its transcript."""

    del recording_url
    db = SessionLocal()
    try:
        call = db.query(CallLog).filter(CallLog.call_sid == call_sid).first()
        if call is None:
            logger.warning("Completion received for unknown call SID %s", call_sid)
            return
        call.ended_at = datetime.utcnow()
        call.duration_seconds = duration_seconds
        db.commit()
        _agent.end_conversation(call.id)
    finally:
        db.close()


async def _read_twilio_form(request: Request) -> dict[str, list[str]]:
    body = await request.body()
    if not body:
        return {}
    return parse_qs(body.decode("utf-8"))


def _get_or_create_call(db: Session, call_sid: str, call_type: str) -> CallLog:
    call = db.query(CallLog).filter(CallLog.call_sid == call_sid).first() if call_sid else None
    if call:
        return call
    call = CallLog(
        call_sid=call_sid or f"WEBHOOK{datetime.utcnow().timestamp()}",
        call_type=call_type or "check_in",
    )
    db.add(call)
    db.commit()
    db.refresh(call)
    return call
