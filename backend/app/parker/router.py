"""FastAPI routes for Parker's v0 proactive-action loop."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import OutboxMessage, StagedAction
from app.parker.auth import require_dashboard_auth
from app.escalation.candidates import CANDIDATE_REASON_PREFIX, flag_non_response_candidates
from app.escalation.engine import get_open_escalations
from app.escalation.router import serialize_escalation
from app.parker.pipeline import (
    approve_outbox_message,
    cancel_outbox_message,
    cancel_staged_action,
    confirm_staged_action,
    execute_staged_action,
    get_due_resurfaced_actions,
    list_outbox_messages,
    resolve_captured_intents,
    stage_resolved_actions,
)
from app.parker.review_ui import REVIEW_PAGE_HTML

router = APIRouter()

RECENT_HISTORY_LIMIT = 10


class TickRequest(BaseModel):
    now: datetime | None = None


class ConfirmRequest(BaseModel):
    confirmed_by: str = "patient"
    now: datetime | None = None


class ExecuteRequest(BaseModel):
    now: datetime | None = None


class CancelRequest(BaseModel):
    cancelled_by: str = "caregiver"
    now: datetime | None = None


@router.post("/tick")
def run_parker_tick(payload: TickRequest, db: Session = Depends(get_db)) -> dict[str, int]:
    """Run one resolve→stage tick and flag non-response escalation candidates."""

    resolutions = resolve_captured_intents(db, now=payload.now)
    staged = stage_resolved_actions(db, now=payload.now)
    candidates = flag_non_response_candidates(db, now=payload.now)
    return {
        "resolved": len(resolutions),
        "staged": len(staged),
        "escalation_candidates": len(candidates),
    }


@router.get("/resurface")
def list_due_resurfaced_actions(
    now: datetime | None = None,
    db: Session = Depends(get_db),
) -> dict[str, list[dict[str, Any]]]:
    """List due actions that should be resurfaced for confirmation."""

    actions = get_due_resurfaced_actions(db, now=now)
    return {"actions": [_serialize_action(action) for action in actions]}


# Caregiver decision surface below: reads expose message content and the
# mutations are human gates, so all of it sits behind the (opt-in) auth
# seam. /tick and /resurface above stay open — assistant-loop surface.
@router.post("/actions/{staged_action_id}/confirm", dependencies=[Depends(require_dashboard_auth)])
def confirm_action(
    staged_action_id: int,
    payload: ConfirmRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Confirm a staged reversible action."""

    try:
        action = confirm_staged_action(
            db,
            staged_action_id,
            confirmed_by=payload.confirmed_by,
            now=payload.now,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _serialize_action(action)


@router.post("/actions/{staged_action_id}/execute", dependencies=[Depends(require_dashboard_auth)])
def execute_action(
    staged_action_id: int,
    payload: ExecuteRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Execute a confirmed reversible action."""

    try:
        action = execute_staged_action(db, staged_action_id, now=payload.now)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _serialize_action(action)


@router.post("/actions/{staged_action_id}/cancel", dependencies=[Depends(require_dashboard_auth)])
def cancel_action(
    staged_action_id: int,
    payload: CancelRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Cancel a staged/confirmed action before execution (caregiver control)."""

    try:
        action = cancel_staged_action(
            db,
            staged_action_id,
            cancelled_by=payload.cancelled_by,
            now=payload.now,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _serialize_action(action)


@router.get("/review", dependencies=[Depends(require_dashboard_auth)])
def caregiver_review(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Aggregated caregiver review feed: everything awaiting a human decision."""

    pending = (
        db.query(StagedAction)
        .filter(StagedAction.status.in_(["staged", "confirmed"]))
        .order_by(StagedAction.execute_after, StagedAction.created_at, StagedAction.id)
        .all()
    )
    queued = list_outbox_messages(db, status="queued_local")
    approved = list_outbox_messages(db, status="approved_local")
    escalations = [serialize_escalation(item) for item in get_open_escalations(db)]
    candidates = [
        item for item in escalations if item["reason"].startswith(CANDIDATE_REASON_PREFIX)
    ]
    others = [item for item in escalations if not item["reason"].startswith(CANDIDATE_REASON_PREFIX)]
    # The trust surface: what Parker actually did, newest first.
    history = (
        db.query(StagedAction)
        .filter(StagedAction.status == "executed")
        .order_by(StagedAction.executed_at.desc(), StagedAction.id.desc())
        .limit(RECENT_HISTORY_LIMIT)
        .all()
    )
    # The "changed my mind" audit: cancellations stay visible, not vanished.
    cancelled_actions = (
        db.query(StagedAction)
        .filter(StagedAction.status == "cancelled")
        .order_by(StagedAction.cancelled_at.desc(), StagedAction.id.desc())
        .limit(RECENT_HISTORY_LIMIT)
        .all()
    )
    cancelled_messages = (
        db.query(OutboxMessage)
        .filter(OutboxMessage.status == "cancelled")
        .order_by(OutboxMessage.cancelled_at.desc(), OutboxMessage.id.desc())
        .limit(RECENT_HISTORY_LIMIT)
        .all()
    )
    return {
        "pending_actions": [_serialize_action(action) for action in pending],
        "outbox_queued": [_serialize_outbox_message(message) for message in queued],
        "outbox_approved": [_serialize_outbox_message(message) for message in approved],
        "escalation_candidates": candidates,
        "open_escalations": others,
        "recent_history": [_serialize_action(action) for action in history],
        "recent_cancelled": [_serialize_action(action) for action in cancelled_actions],
        "outbox_cancelled": [_serialize_outbox_message(message) for message in cancelled_messages],
    }


@router.get(
    "/review/ui",
    response_class=HTMLResponse,
    include_in_schema=False,
    dependencies=[Depends(require_dashboard_auth)],
)
def caregiver_review_ui() -> str:
    """Local, single-file caregiver review page over the /parker review APIs."""

    return REVIEW_PAGE_HTML


@router.get("/outbox", dependencies=[Depends(require_dashboard_auth)])
def list_outbox(
    status: str | None = None,
    db: Session = Depends(get_db),
) -> dict[str, list[dict[str, Any]]]:
    """List locally queued family messages (v0 never sends them)."""

    messages = list_outbox_messages(db, status=status)
    return {"messages": [_serialize_outbox_message(message) for message in messages]}


class ApproveOutboxRequest(BaseModel):
    approved_by: str = "caregiver"
    now: datetime | None = None


@router.post("/outbox/{message_id}/approve", dependencies=[Depends(require_dashboard_auth)])
def approve_outbox(
    message_id: int,
    payload: ApproveOutboxRequest | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Caregiver approval for a queued message (second human gate; stays local)."""

    payload = payload or ApproveOutboxRequest()
    message = approve_outbox_message(
        db, message_id, approved_by=payload.approved_by, now=payload.now
    )
    if message is None:
        raise HTTPException(status_code=404, detail=f"Outbox message not found: {message_id}")
    return _serialize_outbox_message(message)


@router.post("/outbox/{message_id}/cancel", dependencies=[Depends(require_dashboard_auth)])
def cancel_outbox(message_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Cancel a queued or approved message before any (future, gated) delivery."""

    message = cancel_outbox_message(db, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail=f"Outbox message not found: {message_id}")
    return _serialize_outbox_message(message)


def _serialize_outbox_message(message: OutboxMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "staged_action_id": message.staged_action_id,
        "recipient": message.recipient,
        "body": message.body,
        "status": message.status,
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "approved_by": message.approved_by,
        "approved_at": message.approved_at.isoformat() if message.approved_at else None,
        "cancelled_at": message.cancelled_at.isoformat() if message.cancelled_at else None,
    }


def _serialize_action(action: StagedAction) -> dict[str, Any]:
    payload = _payload(action.action_payload)
    return {
        "id": action.id,
        "status": action.status,
        "action_type": action.action_type,
        "subject": payload.get("subject"),
        "recipient": payload.get("recipient"),
        "message_text": payload.get("intent_text") if action.action_type == "family_message" else None,
        "execute_after": action.execute_after.isoformat() if action.execute_after else None,
        "resurface_count": action.resurface_count,
        "last_resurfaced_at": action.last_resurfaced_at.isoformat() if action.last_resurfaced_at else None,
        "confirmed_by": action.confirmed_by,
        "executed_at": action.executed_at.isoformat() if action.executed_at else None,
        "cancelled_at": action.cancelled_at.isoformat() if action.cancelled_at else None,
        "cancelled_by": action.cancelled_by,
        "execution_result": action.execution_result,
    }


def _payload(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
