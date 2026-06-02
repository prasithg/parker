"""FastAPI routes for Parker's v0 proactive-action loop."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import StagedAction
from app.parker.pipeline import (
    confirm_staged_action,
    execute_staged_action,
    get_due_resurfaced_actions,
    resolve_captured_intents,
    stage_resolved_actions,
)

router = APIRouter()


class TickRequest(BaseModel):
    now: datetime | None = None


class ConfirmRequest(BaseModel):
    confirmed_by: str = "patient"
    now: datetime | None = None


class ExecuteRequest(BaseModel):
    now: datetime | None = None


@router.post("/tick")
def run_parker_tick(payload: TickRequest, db: Session = Depends(get_db)) -> dict[str, int]:
    """Run one resolve→stage tick for due captured intents."""

    resolutions = resolve_captured_intents(db, now=payload.now)
    staged = stage_resolved_actions(db, now=payload.now)
    return {"resolved": len(resolutions), "staged": len(staged)}


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

    action = confirm_staged_action(
        db,
        staged_action_id,
        confirmed_by=payload.confirmed_by,
        now=payload.now,
    )
    return _serialize_action(action)


@router.post("/actions/{staged_action_id}/execute")
def execute_action(
    staged_action_id: int,
    payload: ExecuteRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Execute a confirmed reversible action."""

    action = execute_staged_action(db, staged_action_id, now=payload.now)
    return _serialize_action(action)


def _serialize_action(action: StagedAction) -> dict[str, Any]:
    payload = _payload(action.action_payload)
    return {
        "id": action.id,
        "status": action.status,
        "action_type": action.action_type,
        "subject": payload.get("subject"),
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
