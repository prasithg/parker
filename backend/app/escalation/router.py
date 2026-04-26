"""FastAPI routes for family escalations."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.escalation.engine import acknowledge_escalation, get_open_escalations, resolve_escalation
from app.escalation.models import Escalation

router = APIRouter()


class ResolveRequest(BaseModel):
    notes: str | None = None


def serialize_escalation(escalation: Escalation) -> dict[str, Any]:
    """Serialize escalation for API responses."""

    return {
        "id": escalation.id,
        "call_log_id": escalation.call_log_id,
        "severity": escalation.severity,
        "reason": escalation.reason,
        "status": escalation.status,
        "notified_contacts": escalation.notified_contacts,
        "resolution_notes": escalation.resolution_notes,
        "created_at": escalation.created_at.isoformat() if escalation.created_at else None,
        "acknowledged_at": escalation.acknowledged_at.isoformat() if escalation.acknowledged_at else None,
        "resolved_at": escalation.resolved_at.isoformat() if escalation.resolved_at else None,
    }


@router.get("/")
def list_open_escalations(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """List currently open escalations."""

    return [serialize_escalation(item) for item in get_open_escalations(db)]


@router.post("/{escalation_id}/acknowledge")
def acknowledge(escalation_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Acknowledge an escalation."""

    escalation = acknowledge_escalation(db, escalation_id)
    if escalation is None:
        raise HTTPException(status_code=404, detail="Escalation not found")
    return serialize_escalation(escalation)


@router.post("/{escalation_id}/resolve")
def resolve(escalation_id: int, request: ResolveRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Resolve an escalation."""

    escalation = resolve_escalation(db, escalation_id, request.notes)
    if escalation is None:
        raise HTTPException(status_code=404, detail="Escalation not found")
    return serialize_escalation(escalation)


@router.get("/history")
def escalation_history(
    days: int = 7,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """Return paginated escalation history, defaulting to the last seven days."""

    since = datetime.utcnow() - timedelta(days=days)
    rows = (
        db.query(Escalation)
        .filter(Escalation.created_at >= since)
        .order_by(Escalation.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [serialize_escalation(row) for row in rows]
