"""Deterministic demo seed: a believable family day for the review UI.

Drives state through the real pipeline functions (capture → resolve →
stage → resurface → confirm → execute) rather than raw row inserts, so the
seeded data always reflects actual system behavior. Run after
``make reset-db``; seeding is guarded against running twice.

Resulting review-page state:

- a reminder and a drafted family message awaiting confirmation;
- one stale reminder that became a non-response escalation candidate;
- one confirmed message queued to the local outbox (cancellable);
- one executed reminder for history;
- one cancelled reminder for the "Changed my mind" audit list.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import CallLog
from app.escalation.candidates import flag_non_response_candidates
from app.parker.pipeline import (
    cancel_staged_action,
    capture_intent,
    confirm_staged_action,
    execute_staged_action,
    get_due_resurfaced_actions,
    resolve_captured_intents,
    stage_resolved_actions,
)

SEED_CALL_SID = "DEMO-SEED"


def seed_demo_data(db: Session, now: datetime | None = None) -> dict[str, Any]:
    """Seed the demo scenario; returns a summary, or skips if already seeded."""

    current = now or datetime.utcnow()
    existing = db.query(CallLog).filter(CallLog.call_sid == SEED_CALL_SID).first()
    if existing is not None:
        return {"skipped": True, "reason": "demo data already seeded (run make reset-db first)"}

    call = CallLog(call_sid=SEED_CALL_SID, call_type="check_in")
    db.add(call)
    db.commit()
    db.refresh(call)

    def hours_ago(value: float) -> datetime:
        return current - timedelta(hours=value)

    # 1. Stale reminder → resurfaced three times, never confirmed → candidate.
    capture_intent(
        db,
        call_log_id=call.id,
        intent_text="Remind me to do the afternoon stretches.",
        requested_action="remind",
        subject="afternoon stretches",
        due_at=hours_ago(4),
    )
    resolve_captured_intents(db, now=hours_ago(4))
    stage_resolved_actions(db, now=hours_ago(4))
    for offset in (3, 2.5, 2):
        get_due_resurfaced_actions(db, now=hours_ago(offset))

    # 2. Yesterday's reminder, fully confirmed and executed (history).
    capture_intent(
        db,
        call_log_id=call.id,
        intent_text="Remind me to call the pharmacy about the refill pickup time.",
        requested_action="remind",
        subject="call the pharmacy",
        due_at=hours_ago(26),
    )
    resolve_captured_intents(db, now=hours_ago(26))
    executed = stage_resolved_actions(db, now=hours_ago(26))[0]
    confirm_staged_action(db, executed.id, confirmed_by="patient", now=hours_ago(25.9))
    execute_staged_action(db, executed.id, now=hours_ago(25.8))

    # 3. Confirmed family message → queued to the local outbox (cancellable).
    capture_intent(
        db,
        call_log_id=call.id,
        intent_text="The physio visit went well today, much steadier on the left side.",
        requested_action="message",
        subject="message Sarah",
        recipient="Sarah",
        due_at=hours_ago(0.2),
    )
    resolve_captured_intents(db, now=hours_ago(0.2))
    queued = stage_resolved_actions(db, now=hours_ago(0.2))[0]
    confirm_staged_action(db, queued.id, confirmed_by="patient", now=hours_ago(0.1))
    execute_staged_action(db, queued.id, now=hours_ago(0.1))

    # 4. A reminder the patient changed their mind about ("Changed my mind" audit).
    capture_intent(
        db,
        call_log_id=call.id,
        intent_text="Remind me to set up the card table for bridge tonight.",
        requested_action="remind",
        subject="set up the card table",
        due_at=hours_ago(6),
    )
    resolve_captured_intents(db, now=hours_ago(6))
    cancelled = stage_resolved_actions(db, now=hours_ago(6))[0]
    cancel_staged_action(db, cancelled.id, cancelled_by="patient", now=hours_ago(5.5))

    # 5. Reminder awaiting confirmation right now.
    capture_intent(
        db,
        call_log_id=call.id,
        intent_text="Remind me to water the tomato plants this evening.",
        requested_action="remind",
        subject="water the tomato plants",
        due_at=current - timedelta(minutes=5),
    )
    # 6. Drafted family message awaiting confirmation right now.
    capture_intent(
        db,
        call_log_id=call.id,
        intent_text="Dinner on Sunday would be lovely, can the kids come too?",
        requested_action="message",
        subject="message Rohan",
        recipient="Rohan",
        due_at=current - timedelta(minutes=5),
    )
    resolve_captured_intents(db, now=current)
    stage_resolved_actions(db, now=current)

    candidates = flag_non_response_candidates(db, now=current)

    return {
        "skipped": False,
        "call_log_id": call.id,
        "pending_confirmation": 3,  # stale stretches + tomato plants + Rohan draft
        "outbox_queued": 1,
        "escalation_candidates": len(candidates),
        "executed_history": 2,  # pharmacy reminder + Sarah message
        "cancelled": 1,  # the bridge card table, changed mind
    }


def main() -> None:  # pragma: no cover — CLI entry point
    from app.db.database import SessionLocal, create_tables

    create_tables()
    db = SessionLocal()
    summary = seed_demo_data(db)
    db.close()
    if summary["skipped"]:
        print(f"Seed skipped: {summary['reason']}")
    else:
        print(
            "Demo data seeded: "
            f"{summary['pending_confirmation']} actions awaiting confirmation, "
            f"{summary['outbox_queued']} message queued locally, "
            f"{summary['escalation_candidates']} non-response candidate(s), "
            f"{summary['cancelled']} cancelled item in the audit list."
        )


if __name__ == "__main__":  # pragma: no cover
    main()
