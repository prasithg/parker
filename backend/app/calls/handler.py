"""Twilio call creation, webhooks, and completion handling."""

from __future__ import annotations

import html
import logging
from datetime import datetime
from typing import Callable, Optional, Union
from urllib.parse import parse_qs

from fastapi import Request
from sqlalchemy.orm import Session

from app.config import settings
from app.conversation.agent import ConversationAgent
from app.db.database import SessionLocal
from app.db.models import CallLog

logger = logging.getLogger("parker.calls")

_agent = ConversationAgent(SessionLocal)


def get_twilio_client():
    """Return a Twilio Client if credentials are configured, else None."""

    if not (
        settings.twilio_account_sid
        and settings.twilio_auth_token
        and settings.twilio_phone_number
    ):
        return None
    try:
        from twilio.rest import Client
    except ImportError:
        logger.warning("twilio package not installed")
        return None
    return Client(settings.twilio_account_sid, settings.twilio_auth_token)


def trigger_outbound_call(
    to_number: Optional[Union[str, Session]] = None,
    call_type: str = "check_in",
    webhook_url: str = "/calls/twilio/voice",
    db_session_factory: Callable[[], Session] | None = None,
    db: Optional[Session] = None,
) -> Union[CallLog, str]:
    """Create a CallLog and initiate an outbound call when Twilio is configured."""

    return_call_log = False
    if isinstance(to_number, Session):
        db = to_number
        to_number = None
        return_call_log = True
    if db is not None:
        return_call_log = True

    owns_session = db is None
    if db is None:
        db = db_session_factory() if db_session_factory else SessionLocal()

    to = to_number or settings.patient_phone_number
    try:
        call_log = CallLog(
            call_sid=f"pending-{datetime.utcnow().timestamp()}",
            call_type=call_type,
        )
        db.add(call_log)
        db.commit()
        db.refresh(call_log)

        client = get_twilio_client()
        if client and to:
            twilio_call = client.calls.create(
                to=to,
                from_=settings.twilio_phone_number,
                url=webhook_url,
                status_callback=webhook_url.replace("/voice", "/status"),
                status_callback_event=["completed", "no-answer", "busy", "failed"],
            )
            call_log.call_sid = twilio_call.sid
        else:
            call_log.call_sid = f"LOCAL{call_log.id}"
            logger.info("Twilio not configured; created local call %s", call_log.call_sid)

        db.commit()
        db.refresh(call_log)
        logger.info("Outbound %s call placed: %s -> %s", call_type, call_log.call_sid, to)
        return call_log if return_call_log else call_log.call_sid
    except Exception:
        logger.exception("Failed to trigger outbound call")
        db.rollback()
        return None if return_call_log else ""
    finally:
        if owns_session:
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
    duration_seconds: Optional[int] = None,
    recording_url: Optional[str] = None,
    duration: Optional[int] = None,
) -> Optional[CallLog]:
    """Update a call log when a call finishes and summarize the transcript."""

    del recording_url
    if duration_seconds is None:
        duration_seconds = duration

    db = SessionLocal()
    try:
        call_log = db.query(CallLog).filter(CallLog.call_sid == call_sid).first()
        if not call_log:
            logger.warning("No call log found for SID: %s", call_sid)
            return None
        call_log.ended_at = datetime.utcnow()
        call_log.duration_seconds = duration_seconds
        db.commit()
        db.refresh(call_log)
        _agent.end_conversation(call_log.id)
        logger.info("Call completed: %s (%s seconds)", call_sid, duration_seconds)
        return call_log
    finally:
        db.close()


async def _read_twilio_form(request: Request) -> dict[str, List[str]]:
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
