"""Escalation engine operations."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import case
from sqlalchemy.orm import Session

from app.escalation.models import Escalation
from app.escalation.notifier import dispatch_notifications, get_family_contacts

VALID_SEVERITIES = {"info", "warning", "urgent"}
VALID_STATUSES = {"open", "acknowledged", "resolved"}


def create_escalation(db: Session, call_log_id: int, reason: str, severity: str = "warning") -> Escalation:
    """Create an escalation, dispatch notifications, and persist notified contacts."""

    if severity not in VALID_SEVERITIES:
        raise ValueError(f"Invalid severity: {severity}")
    escalation = Escalation(call_log_id=call_log_id, reason=reason, severity=severity, status="open")
    db.add(escalation)
    db.commit()
    db.refresh(escalation)
    notified = dispatch_notifications(escalation, get_family_contacts())
    escalation.notified_contacts = json.dumps(notified)
    db.commit()
    db.refresh(escalation)
    return escalation


def acknowledge_escalation(db: Session, escalation_id: int) -> Escalation | None:
    """Mark an escalation acknowledged."""

    escalation = db.get(Escalation, escalation_id)
    if escalation is None:
        return None
    escalation.status = "acknowledged"
    escalation.acknowledged_at = datetime.utcnow()
    db.commit()
    db.refresh(escalation)
    return escalation


def resolve_escalation(db: Session, escalation_id: int, notes: str | None = None) -> Escalation | None:
    """Resolve an escalation with optional notes."""

    escalation = db.get(Escalation, escalation_id)
    if escalation is None:
        return None
    escalation.status = "resolved"
    escalation.resolution_notes = notes
    escalation.resolved_at = datetime.utcnow()
    db.commit()
    db.refresh(escalation)
    return escalation


def get_open_escalations(db: Session) -> list[Escalation]:
    """Return open escalations ordered urgent, warning, info, newest first."""

    severity_order = case(
        (Escalation.severity == "urgent", 0),
        (Escalation.severity == "warning", 1),
        else_=2,
    )
    return (
        db.query(Escalation)
        .filter(Escalation.status == "open")
        .order_by(severity_order, Escalation.created_at.desc())
        .all()
    )


def auto_escalate_check(db: Session) -> list[Escalation]:
    """Promote warning escalations open for >30 minutes to urgent."""

    cutoff = datetime.utcnow() - timedelta(minutes=30)
    escalations = (
        db.query(Escalation)
        .filter(Escalation.status == "open")
        .filter(Escalation.severity == "warning")
        .filter(Escalation.created_at < cutoff)
        .all()
    )
    for escalation in escalations:
        escalation.severity = "urgent"
        notified = dispatch_notifications(escalation, get_family_contacts())
        escalation.notified_contacts = json.dumps(notified)
    db.commit()
    for escalation in escalations:
        db.refresh(escalation)
    return escalations
