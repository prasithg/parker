"""Conversation memory store utilities."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import CallLog, DoseLog
from app.memory.models import CallContext, ConversationMemory

VALID_MEMORY_TYPES = {"fact", "preference", "event", "topic"}


def save_memory(
    db: Session,
    content: str,
    memory_type: str,
    call_log_id: int | None = None,
    source: str = "call",
) -> ConversationMemory:
    """Store a conversation memory."""

    if memory_type not in VALID_MEMORY_TYPES:
        raise ValueError(f"Invalid memory_type: {memory_type}")
    memory = ConversationMemory(
        call_log_id=call_log_id,
        memory_type=memory_type,
        content=content,
        source=source,
    )
    db.add(memory)
    db.commit()
    db.refresh(memory)
    return memory


def get_recent_memories(db: Session, limit: int = 20) -> list[ConversationMemory]:
    """Return latest memories."""

    return db.query(ConversationMemory).order_by(ConversationMemory.created_at.desc()).limit(limit).all()


def search_memories(db: Session, query: str, limit: int = 5) -> list[ConversationMemory]:
    """Simple case-insensitive content search."""

    pattern = f"%{query}%"
    return (
        db.query(ConversationMemory)
        .filter(ConversationMemory.content.ilike(pattern))
        .order_by(ConversationMemory.created_at.desc())
        .limit(limit)
        .all()
    )


def save_call_context(db: Session, call_log_id: int, context_dict: dict[str, Any]) -> list[CallContext]:
    """Persist structured context key/value pairs for a call."""

    rows: list[CallContext] = []
    for key, value in context_dict.items():
        encoded = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
        row = CallContext(call_log_id=call_log_id, key=str(key), value=encoded)
        db.add(row)
        rows.append(row)
    db.commit()
    for row in rows:
        db.refresh(row)
    return rows


def get_call_context(db: Session, call_log_id: int) -> dict[str, str]:
    """Retrieve structured context for a call."""

    rows = db.query(CallContext).filter(CallContext.call_log_id == call_log_id).all()
    return {row.key: row.value for row in rows}


def get_context_for_next_call(db: Session) -> str:
    """Build context text for the next call."""

    lines: list[str] = []
    memories = get_recent_memories(db, limit=5)
    if memories:
        lines.append("Recent memories:")
        for memory in memories:
            lines.append(f"- [{memory.memory_type}] {memory.content}")

    last_call = db.query(CallLog).order_by(CallLog.started_at.desc()).first()
    if last_call and last_call.patient_mood:
        lines.append(f"Last recorded mood: {last_call.patient_mood}")

    concerns = (
        db.query(CallContext)
        .filter(CallContext.key == "concerns_raised")
        .order_by(CallContext.id.desc())
        .limit(3)
        .all()
    )
    if concerns:
        lines.append("Ongoing concerns:")
        for concern in concerns:
            lines.append(f"- {concern.value}")

    streak = _adherence_streak(db)
    lines.append(f"Medication adherence streak: {streak} confirmed recent dose(s).")
    return "\n".join(lines) if lines else "No prior context yet."


def _adherence_streak(db: Session) -> int:
    recent = (
        db.query(DoseLog)
        .order_by(DoseLog.id.desc())
        .limit(30)
        .all()
    )
    streak = 0
    for dose in recent:
        if not dose.confirmed:
            break
        streak += 1
    return streak
