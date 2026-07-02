"""Parker capture → resolve → stage → resurface v0 pipeline."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import CapturedIntent, OutboxMessage, ResolutionResult, StagedAction
from app.exercises.session import start_local_exercise_session
from app.parker.policy import executable_v0_action_types

# Action types v0 may execute LOCALLY after confirmation. Every entry's
# execution artifact is local and reversible (reminders resurface locally;
# exercise starts are local/auditable; family messages queue/release to the
# cancellable local outbox — no send path exists).
EXECUTABLE_V0_ACTION_TYPES = executable_v0_action_types()
# Backwards-compatible alias for earlier naming.
REVERSIBLE_ACTION_TYPES = EXECUTABLE_V0_ACTION_TYPES


def currently_executable_action_types() -> set[str]:
    """The live execution surface: local types plus gateway-backed types.

    Gateway-backed types (app.parker.hands) exist only while the family's
    OpenClaw gateway advertises an enabled skill AND the policy taxonomy
    classifies the type local-reversible with user confirmation. With no
    gateway configured this is exactly the local v0 set.
    """

    from app.parker.hands import gateway_executable_action_types

    return set(EXECUTABLE_V0_ACTION_TYPES) | set(gateway_executable_action_types())
REQUESTED_ACTION_TO_ACTION_TYPE = {
    "remind": "reminder",
    "reminder": "reminder",
    "exercise": "exercise_start",
    "exercise_start": "exercise_start",
    "speech_exercise": "exercise_start",
    "movement_exercise": "exercise_start",
    "message": "family_message",
    "family_message": "family_message",
}


def capture_intent(
    db: Session,
    *,
    call_log_id: int | None,
    intent_text: str,
    requested_action: str = "remind",
    due_at: datetime | str | None = None,
    subject: str | None = None,
    recipient: str | None = None,
) -> CapturedIntent:
    """Persist a patient/caregiver intent for later resolution."""

    captured = CapturedIntent(
        call_log_id=call_log_id,
        intent_text=intent_text,
        requested_action=requested_action,
        subject=subject,
        recipient=recipient,
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
    executable = currently_executable_action_types()
    results: list[ResolutionResult] = []
    for intent in due_intents:
        action_type = REQUESTED_ACTION_TO_ACTION_TYPE.get(intent.requested_action, intent.requested_action)
        reversible = action_type in executable
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
    executable = currently_executable_action_types()
    staged: list[StagedAction] = []
    for resolution in resolutions:
        if not resolution.reversible or resolution.action_type not in executable:
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
                    "recipient": captured.recipient,
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


def cancel_staged_action(
    db: Session,
    staged_action_id: int,
    *,
    cancelled_by: str = "caregiver",
    now: datetime | None = None,
) -> StagedAction:
    """Cancel a staged or confirmed action before execution."""

    action = _get_action(db, staged_action_id)
    if action.status not in {"staged", "confirmed"}:
        return action
    action.status = "cancelled"
    moment = now or datetime.utcnow()
    action.cancelled_at = moment
    action.cancelled_by = cancelled_by
    when = moment.isoformat(timespec="seconds")
    action.execution_result = f"cancelled by {cancelled_by} before execution at {when}"
    db.commit()
    db.refresh(action)
    return action


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
    """Execute only confirmed actions on the current execution surface.

    Local types execute locally: reminders resurface, exercise sessions
    start, family messages queue/release to the local outbox — nothing is
    sent anywhere in v0. Gateway-backed types forward the APPROVED intent
    to the family's OpenClaw skill (app.parker.hands); a skill error
    becomes a ``failed`` review row and is never silently retried.
    """

    action = _get_action(db, staged_action_id)
    if action.status in {"executed", "cancelled", "failed"}:
        # Terminal states are never re-run or overwritten: a failed skill
        # invocation stays failed (no retry, audit intact), an executed
        # action stays executed, a cancellation stands.
        return action
    if not action.reversible or action.action_type not in currently_executable_action_types():
        action.status = "blocked"
        action.execution_result = (
            "Only reversible actions on the current execution surface can be executed."
        )
    elif action.status != "confirmed":
        action.status = "blocked"
        action.execution_result = "Action requires confirmation before execution."
    elif action.action_type == "family_message":
        _execute_family_message(db, action, now=now)
    elif action.action_type == "exercise_start":
        payload = _json_payload(action.action_payload)
        subject = payload.get("subject") or action.resolution_result.summary
        call_log_id = action.resolution_result.captured_intent.call_log_id
        session = start_local_exercise_session(
            db,
            staged_action_id=action.id,
            call_log_id=call_log_id,
            subject=subject,
            now=now,
        )
        action.status = "executed"
        action.executed_at = now or datetime.utcnow()
        action.execution_result = f"local exercise session started: {subject} (exercise session {session.id})"
    elif action.action_type in EXECUTABLE_V0_ACTION_TYPES:
        payload = _json_payload(action.action_payload)
        subject = payload.get("subject") or action.resolution_result.summary
        action.status = "executed"
        action.executed_at = now or datetime.utcnow()
        action.execution_result = f"reminder resurfaced: {subject}"
    else:
        _execute_via_hands(db, action, now=now)
    db.commit()
    db.refresh(action)
    return action


def _execute_via_hands(db: Session, action: StagedAction, now: datetime | None = None) -> None:
    """Forward one confirmed gateway-backed intent to its OpenClaw skill.

    Exactly one invocation attempt. Success relays the skill's speakable
    detail; failure marks the action ``failed`` with the reason — a review
    row plus spoken failure, never a silent retry with side effects.
    """

    from app.parker.hands import configured_hands

    del db  # symmetry with siblings; caller commits
    hands = configured_hands()
    if hands is None:
        action.status = "blocked"
        action.execution_result = (
            f"No OpenClaw skill is currently enabled for {action.action_type}."
        )
        return
    payload = _json_payload(action.action_payload)
    result = hands.invoke(
        action.action_type,
        {
            "subject": payload.get("subject"),
            "intent_text": payload.get("intent_text"),
            "recipient": payload.get("recipient"),
        },
        idempotency_key=f"staged-action-{action.id}",
    )
    if result.ok:
        action.status = "executed"
        action.executed_at = now or datetime.utcnow()
        action.execution_result = f"openclaw skill completed: {result.detail}"
    else:
        action.status = "failed"
        action.execution_result = (
            f"openclaw skill failed (no retry was attempted): {result.detail}"
        )


def _execute_family_message(db: Session, action: StagedAction, now: datetime | None = None) -> None:
    """Write a confirmed family message to the local outbox (never sends).

    Capability trust model: a recipient on the admin's family-contact
    allowlist releases on the patient's confirmation alone — the row is
    created ``released_local`` with the capability policy recorded, visible
    in review. Anyone else (or no allowlist configured) stays
    ``queued_local`` behind the per-message caregiver approval gate. Either
    way nothing leaves the machine: v0 has no send transport at all.
    """

    from app.parker.contacts import RELEASED_BY_CAPABILITY_POLICY, is_allowlisted_recipient

    payload = _json_payload(action.action_payload)
    recipient = (payload.get("recipient") or "").strip()
    body = (payload.get("intent_text") or payload.get("subject") or "").strip()
    if not recipient or not body:
        action.status = "blocked"
        action.execution_result = "Family message requires a recipient and message text."
        return
    moment = now or datetime.utcnow()
    message = OutboxMessage(staged_action_id=action.id, recipient=recipient, body=body)
    if is_allowlisted_recipient(recipient):
        message.status = "released_local"
        message.released_at = moment
        message.released_by = RELEASED_BY_CAPABILITY_POLICY
    db.add(message)
    db.flush()
    action.status = "executed"
    action.executed_at = moment
    if message.status == "released_local":
        action.execution_result = (
            f"family message released for {recipient} (family-contact capability; "
            f"outbox {message.id}; no send transport exists in v0 — it stays local)"
        )
    else:
        action.execution_result = (
            f"family message queued locally for {recipient} (outbox {message.id}; awaiting family approval)"
        )


def list_outbox_messages(db: Session, status: str | None = None) -> list[OutboxMessage]:
    """List outbox messages, optionally filtered by status."""

    query = db.query(OutboxMessage)
    if status:
        query = query.filter(OutboxMessage.status == status)
    return query.order_by(OutboxMessage.created_at, OutboxMessage.id).all()


def approve_outbox_message(
    db: Session,
    message_id: int,
    *,
    approved_by: str = "caregiver",
    now: datetime | None = None,
) -> OutboxMessage | None:
    """Caregiver-approve a queued message; it still never leaves the machine.

    The per-message gate for OFF-allowlist recipients (allowlisted ones
    release by capability policy and never wait here). A future sender
    (which does not exist in v0) must only ever consider approved/released
    rows behind an explicit config flag.
    """

    message = db.get(OutboxMessage, message_id)
    if message is None:
        return None
    if message.status == "queued_local":
        message.status = "approved_local"
        message.approved_by = approved_by
        message.approved_at = now or datetime.utcnow()
        db.commit()
        db.refresh(message)
    return message


def cancel_outbox_message(db: Session, message_id: int, now: datetime | None = None) -> OutboxMessage | None:
    """Cancel a queued/released/approved message; the reversibility story for v0 messages."""

    message = db.get(OutboxMessage, message_id)
    if message is None:
        return None
    if message.status in {"queued_local", "released_local", "approved_local"}:
        message.status = "cancelled"
        message.cancelled_at = now or datetime.utcnow()
        db.commit()
        db.refresh(message)
    return message


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
