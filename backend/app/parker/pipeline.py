"""Parker capture → resolve → stage → resurface v0 pipeline."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import CapturedIntent, ResolutionResult, StagedAction

REVERSIBLE_ACTION_TYPES = {"reminder"}
REQUESTED_ACTION_TO_ACTION_TYPE = {
    "remind": "reminder",
    "reminder": "reminder",
}


def capture_intent(
    db: Session,
    *,
    call_log_id: int | None,
    intent_text: str,
    requested_action: str = "remind",
    due_at: datetime | str | None = None,
    subject: str | None = None,
) -> CapturedIntent:
    """Persist a patient/caregiver intent for later resolution."""

    captured = CapturedIntent(
        call_log_id=call_log_id,
        intent_text=intent_text,
        requested_action=requested_action,
        subject=subject,
        due_at=_coerce_datetime(due_at),
        status="pending",
    )
    db.add(captured)
    db.commit()
    db.refresh(captured)
    return captured


def resolve_captured_intents(db: Session, now: datetime | None = None) -> list[ResolutionResult]:
    """Resolve pending intents whose due time has arrived into concrete action candidates."""

    current = now or datetime.utcnow()
    due_intents = (
        db.query(CapturedIntent)
        .filter(CapturedIntent.status == "pending")
        .filter((CapturedIntent.due_at.is_(None)) | (CapturedIntent.due_at <= current))
        .order_by(CapturedIntent.created_at, CapturedIntent.id)
        .all()
    )
    results: list[ResolutionResult] = []
    for intent in due_intents:
        action_type = REQUESTED_ACTION_TO_ACTION_TYPE.get(intent.requested_action, intent.requested_action)
        reversible = action_type in REVERSIBLE_ACTION_TYPES
        subject = intent.subject or intent.intent_text
        result = ResolutionResult(
            captured_intent_id=intent.id,
            status="resolved",
            action_type=action_type,
            reversible=reversible,
            summary=(
                f"Ready to resurface reminder: {subject}"
                if reversible
                else f"Resolved as non-reversible action: {subject}"
            ),
            execute_after=intent.due_at,
        )
        intent.status = "resolved"
        intent.resolved_at = current
        db.add(result)
        results.append(result)
    db.commit()
    for result in results:
        db.refresh(result)
    return results


def stage_resolved_actions(db: Session, now: datetime | None = None) -> list[StagedAction]:
    """Stage reversible resolved actions; reject non-reversible candidates in v0."""

    current = now or datetime.utcnow()
    del current  # reserved for future staging windows/audit details
    resolutions = (
        db.query(ResolutionResult)
        .filter(ResolutionResult.status == "resolved")
        .order_by(ResolutionResult.created_at, ResolutionResult.id)
        .all()
    )
    staged: list[StagedAction] = []
    for resolution in resolutions:
        if not resolution.reversible or resolution.action_type not in REVERSIBLE_ACTION_TYPES:
            resolution.status = "rejected"
            resolution.summary = _append_reversible_rejection(resolution.summary)
            continue

        captured = resolution.captured_intent
        action = StagedAction(
            resolution_result_id=resolution.id,
            status="staged",
            action_type=resolution.action_type,
            reversible=True,
            execute_after=resolution.execute_after,
            action_payload=json.dumps(
                {
                    "captured_intent_id": captured.id,
                    "subject": captured.subject or captured.intent_text,
                    "intent_text": captured.intent_text,
                }
            ),
        )
        resolution.status = "staged"
        db.add(action)
        staged.append(action)
    db.commit()
    for action in staged:
        db.refresh(action)
    return staged


def get_due_resurfaced_actions(db: Session, now: datetime | None = None) -> list[StagedAction]:
    """Return due staged actions and mark that they were resurfaced."""

    current = now or datetime.utcnow()
    actions = (
        db.query(StagedAction)
        .filter(StagedAction.status == "staged")
        .filter((StagedAction.execute_after.is_(None)) | (StagedAction.execute_after <= current))
        .order_by(StagedAction.execute_after, StagedAction.created_at, StagedAction.id)
        .all()
    )
    for action in actions:
        action.last_resurfaced_at = current
        action.resurface_count += 1
    db.commit()
    for action in actions:
        db.refresh(action)
    return actions


def confirm_staged_action(
    db: Session,
    staged_action_id: int,
    *,
    confirmed_by: str = "patient",
    now: datetime | None = None,
) -> StagedAction:
    """Confirm a staged action before execution."""

    action = _get_action(db, staged_action_id)
    if action.status not in {"staged", "confirmed"}:
        return action
    action.status = "confirmed"
    action.confirmed_by = confirmed_by
    action.confirmed_at = now or datetime.utcnow()
    db.commit()
    db.refresh(action)
    return action


def execute_staged_action(db: Session, staged_action_id: int, now: datetime | None = None) -> StagedAction:
    """Execute only confirmed, reversible v0 actions."""

    action = _get_action(db, staged_action_id)
    if not action.reversible or action.action_type not in REVERSIBLE_ACTION_TYPES:
        action.status = "blocked"
        action.execution_result = "Only reversible actions can be executed in Parker v0."
    elif action.status != "confirmed":
        action.status = "blocked"
        action.execution_result = "Action requires confirmation before execution."
    else:
        payload = _json_payload(action.action_payload)
        subject = payload.get("subject") or action.resolution_result.summary
        action.status = "executed"
        action.executed_at = now or datetime.utcnow()
        action.execution_result = f"reminder resurfaced: {subject}"
    db.commit()
    db.refresh(action)
    return action


def _get_action(db: Session, staged_action_id: int) -> StagedAction:
    action = db.get(StagedAction, staged_action_id)
    if action is None:
        raise ValueError(f"Staged action not found: {staged_action_id}")
    return action


def _coerce_datetime(value: datetime | str | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def _json_payload(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _append_reversible_rejection(summary: str) -> str:
    suffix = "Rejected: only reversible actions can be staged in Parker v0."
    if "reversible" in summary:
        return summary
    return f"{summary} {suffix}"
