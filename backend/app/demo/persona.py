"""Ravi, 77 — the north-star persona's data, seeded for real.

Ravi is the synthetic pilot user (docs/personas/ravi.md): retired engineer
with Parkinson's, old Hindi songs, tennis, YouTube medicine videos he
pauses to ask questions about, morning walks before the heat. This seed
writes the data the fast-voice orchestrator's context card actually reads
— memories, medications with schedules, a dose streak, a prior live
session — so a live conversation opens *knowing him*.

Deliberate detail: one memory contains a dosage ("25-100 mg refill").
The context worker must drop that line (the post-hoc speech guard would
cancel Parker mid-word for reading a dose aloud); seeding it keeps that
filter honest in demos, not just in tests.

Run: ``make seed-persona`` (idempotent; guarded by the seed call sid).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import CallLog, DoseLog, Medication
from app.memory.store import save_call_context, save_memory

PERSONA_CALL_SID = "DEMO-RAVI-LIVE"


def seed_persona_data(db: Session, now: datetime | None = None) -> dict[str, Any]:
    """Seed Ravi's world; returns a summary, or skips if already seeded."""

    current = now or datetime.utcnow()
    existing = db.query(CallLog).filter(CallLog.call_sid == PERSONA_CALL_SID).first()
    if existing is not None:
        return {"skipped": True, "reason": "persona data already seeded"}

    # Medications: names + schedules the card may speak; doses stay in the
    # dosage column (and one memory below) where the card filter governs.
    levodopa = Medication(
        name="Carbidopa-Levodopa",
        dosage="25-100 mg",
        schedule_times=json.dumps(["08:00", "14:00", "20:00"]),
        active=True,
    )
    pramipexole = Medication(
        name="Pramipexole",
        dosage="0.5 mg",
        schedule_times=json.dumps(["20:00"]),
        active=True,
    )
    db.add_all([levodopa, pramipexole])
    db.commit()

    # Yesterday's live session, as the orchestrator's finalize would leave it.
    yesterday = current - timedelta(hours=26)
    call = CallLog(
        call_sid=PERSONA_CALL_SID,
        call_type="realtime",
        started_at=yesterday,
        ended_at=yesterday + timedelta(minutes=6),
        duration_seconds=360,
        summary=(
            "Live conversation, 5 exchange(s). Asked about: when Alcaraz plays "
            "next at the US Open; what channel the tennis is on"
        ),
        patient_mood="cheerful",
    )
    db.add(call)
    db.commit()
    db.refresh(call)

    # A confirmed dose streak so the card's adherence line has substance.
    for i, scheduled in enumerate(["08:00", "14:00", "20:00"]):
        db.add(
            DoseLog(
                call_log_id=call.id,
                medication_id=levodopa.id,
                scheduled_time=scheduled,
                confirmed=True,
                confirmed_at=yesterday + timedelta(hours=i),
            )
        )
    db.commit()

    # Order matters: the context card reads the 5 MOST RECENT memories, so
    # the highest-value lines are seeded last (newest).
    memories = [
        ("fact", "Daughter Sarah visits on Sundays; son Anil calls in the evenings."),
        # The line the context card must DROP (dosage → spoken-guard trip);
        # seeded inside the recent-5 window on purpose:
        ("event", "The pharmacist said his 25-100 mg refill is ready for pickup."),
        ("fact", "Walks in the morning and likes to be back before it gets hot, around 10am."),
        (
            "event",
            "Paused a YouTube video about how levodopa works in the brain and had questions about it.",
        ),
        ("preference", "Loves old Hindi songs — Kishore Kumar and Mohammed Rafi especially."),
        ("topic", "Following the US Open closely; doesn't want to miss Alcaraz's matches."),
    ]
    for memory_type, content in memories:
        save_memory(db, content=content, memory_type=memory_type, call_log_id=call.id, source="seed")

    save_call_context(
        db,
        call.id,
        {"concerns_raised": "Felt a bit unsteady on the back steps on Tuesday."},
    )

    return {
        "skipped": False,
        "call_log_id": call.id,
        "medications": 2,
        "memories": len(memories),
        "dose_streak": 3,
    }


def main() -> None:  # pragma: no cover — CLI entry point
    from app.db.database import SessionLocal, create_tables

    create_tables()
    db = SessionLocal()
    summary = seed_persona_data(db)
    db.close()
    if summary["skipped"]:
        print(f"Persona seed skipped: {summary['reason']}")
    else:
        print(
            "Ravi seeded: "
            f"{summary['medications']} medications, {summary['memories']} memories, "
            f"a {summary['dose_streak']}-dose streak, and yesterday's live session."
        )


if __name__ == "__main__":  # pragma: no cover
    main()
