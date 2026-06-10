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
from app.escalation.candidates import CANDIDATE_REASON_PREFIX, flag_non_response_candidates
from app.escalation.engine import get_open_escalations
from app.escalation.router import serialize_escalation
from app.parker.pipeline import (
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


@router.post("/actions/{staged_action_id}/confirm")
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


@router.post("/actions/{staged_action_id}/execute")
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


@router.post("/actions/{staged_action_id}/cancel")
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


@router.get("/review")
def caregiver_review(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Aggregated caregiver review feed: everything awaiting a human decision."""

    pending = (
        db.query(StagedAction)
        .filter(StagedAction.status.in_(["staged", "confirmed"]))
        .order_by(StagedAction.execute_after, StagedAction.created_at, StagedAction.id)
        .all()
    )
    queued = list_outbox_messages(db, status="queued_local")
    escalations = [serialize_escalation(item) for item in get_open_escalations(db)]
    candidates = [
        item for item in escalations if item["reason"].startswith(CANDIDATE_REASON_PREFIX)
    ]
    others = [item for item in escalations if not item["reason"].startswith(CANDIDATE_REASON_PREFIX)]
    return {
        "pending_actions": [_serialize_action(action) for action in pending],
        "outbox_queued": [_serialize_outbox_message(message) for message in queued],
        "escalation_candidates": candidates,
        "open_escalations": others,
    }


@router.get("/review/ui", response_class=HTMLResponse, include_in_schema=False)
def caregiver_review_ui() -> str:
    """Local, single-file caregiver review page over the /parker review APIs."""

    return REVIEW_PAGE_HTML


@router.get("/outbox")
def list_outbox(
    status: str | None = None,
    db: Session = Depends(get_db),
) -> dict[str, list[dict[str, Any]]]:
    """List locally queued family messages (v0 never sends them)."""

    messages = list_outbox_messages(db, status=status)
    return {"messages": [_serialize_outbox_message(message) for message in messages]}


@router.post("/outbox/{message_id}/cancel")
def cancel_outbox(message_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Cancel a queued message before any (future, approval-gated) delivery."""

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
