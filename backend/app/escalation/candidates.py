"""Candidate-only escalations from non-response signals.

Repeated resurfacing of a staged action with no confirmation suggests the
user may not be responding. This module turns that signal into an
escalation *candidate*: an open `info` escalation for family/operator
review through the existing /escalations flow.

Deliberately candidate-only:

- No notifications are dispatched here (``notified_contacts`` stays empty).
- Severity is ``info``, which `auto_escalate_check` never promotes, so a
  candidate can never silently become a dispatched ``urgent`` escalation.
- At most one candidate per staged action (``StagedAction.escalation_id``).

Escalation precision matters more than recall — noisy escalation burns
family trust. Thresholds live in settings, not code.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import StagedAction
from app.escalation.models import Escalation

CANDIDATE_SEVERITY = "info"
CANDIDATE_REASON_PREFIX = "Non-response candidate"


def flag_non_response_candidates(
    db: Session,
    now: Optional[datetime] = None,
    *,
    resurface_threshold: Optional[int] = None,
    quiet_minutes: Optional[int] = None,
) -> list[Escalation]:
    """Create candidate escalations for stale, repeatedly resurfaced actions.

    A staged action qualifies when it is still unconfirmed, has been
    resurfaced at least ``resurface_threshold`` times, the last resurface
    was at least ``quiet_minutes`` ago, and no candidate exists for it yet.
    """

    current = now or datetime.utcnow()
    threshold = (
        resurface_threshold
        if resurface_threshold is not None
        else settings.parker_non_response_resurface_threshold
    )
    quiet = quiet_minutes if quiet_minutes is not None else settings.parker_non_response_quiet_minutes
    quiet_cutoff = current - timedelta(minutes=quiet)

    stale_actions = (
        db.query(StagedAction)
        .filter(StagedAction.status == "staged")
        .filter(StagedAction.escalation_id.is_(None))
        .filter(StagedAction.resurface_count >= threshold)
        .filter(StagedAction.last_resurfaced_at.isnot(None))
        .filter(StagedAction.last_resurfaced_at <= quiet_cutoff)
        .order_by(StagedAction.last_resurfaced_at, StagedAction.id)
        .all()
    )

    created: list[Escalation] = []
    for action in stale_actions:
        call_log_id = _call_log_id_for(action)
        if call_log_id is None:
            # No traceable call to attach the escalation to; skip rather
            # than fabricate one. The action stays eligible if that changes.
            continue
        escalation = Escalation(
            call_log_id=call_log_id,
            severity=CANDIDATE_SEVERITY,
            status="open",
            reason=(
                f"{CANDIDATE_REASON_PREFIX}: staged action {action.id} "
                f"({_subject_for(action)}) resurfaced {action.resurface_count}x "
                f"with no confirmation since "
                f"{action.last_resurfaced_at:%Y-%m-%d %H:%M}."
            ),
            notified_contacts="[]",
        )
        db.add(escalation)
        db.flush()
        action.escalation_id = escalation.id
        created.append(escalation)

    db.commit()
    for escalation in created:
        db.refresh(escalation)
    return created


def _call_log_id_for(action: StagedAction) -> Optional[int]:
    resolution = action.resolution_result
    if resolution is None or resolution.captured_intent is None:
        return None
    return resolution.captured_intent.call_log_id


def _subject_for(action: StagedAction) -> str:
    try:
        payload = json.loads(action.action_payload or "{}")
    except json.JSONDecodeError:
        payload = {}
    if isinstance(payload, dict) and payload.get("subject"):
        return str(payload["subject"])
    return action.action_type
