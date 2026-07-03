"""Voice-loop runtime state for the desktop shell's tray icon.

One row, overwritten on every transition: is the talk loop idle,
listening, processing, or speaking right now? The writer is the talk
process (``parker talk`` / ``make talk-loop``); the reader is the engine
server (``GET /parker/loop/state``), which the desktop shell polls to
color its tray icon. Different processes, one local SQLite file — the
same reason the screen state is a table.

Privacy posture: the row holds a state word and a timestamp. No
utterances, no audio, nothing about *what* was heard — only whether
Parker is listening.

A publish failure must never break the voice loop: the publisher rolls
back and stays quiet, exactly like the screen-state publisher.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db.database import Base

logger = logging.getLogger("parker.loop_state")

LOOP_STATE_ROW_ID = 1

STATE_IDLE = "idle"
STATE_LISTENING = "listening"
STATE_PROCESSING = "processing"
STATE_SPEAKING = "speaking"

LOOP_STATES = (STATE_IDLE, STATE_LISTENING, STATE_PROCESSING, STATE_SPEAKING)

# A row older than this is treated as left over from a dead talk process.
STALE_AFTER_SECONDS = 120


class LoopState(Base):
    """The single current voice-loop state row."""

    __tablename__ = "loop_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    state: Mapped[str] = mapped_column(String(16), default=STATE_IDLE)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


def publish_loop_state(db: Session, state: str, *, now: Optional[datetime] = None) -> None:
    """Overwrite the loop-state row; never raises into the voice loop."""

    if state not in LOOP_STATES:
        logger.debug("ignoring unknown loop state %r", state)
        return
    try:
        row = db.get(LoopState, LOOP_STATE_ROW_ID)
        stamp = now or datetime.utcnow()
        if row is None:
            row = LoopState(id=LOOP_STATE_ROW_ID, state=state, updated_at=stamp)
            db.add(row)
        else:
            row.state = state
            row.updated_at = stamp
        db.commit()
    except Exception as exc:  # noqa: BLE001 — the loop must survive any publish failure
        try:
            db.rollback()
        except Exception:  # pragma: no cover — best effort
            pass
        logger.debug("loop-state publish failed: %s", exc)


def get_loop_state(db: Session, *, now: Optional[datetime] = None) -> dict[str, Any]:
    """The current loop state for the shell: stale rows read as idle."""

    row = db.get(LoopState, LOOP_STATE_ROW_ID)
    if row is None:
        return {"state": STATE_IDLE, "updated_at": None, "stale": False}
    reference = now or datetime.utcnow()
    age = max(0.0, (reference - row.updated_at).total_seconds())
    stale = age > STALE_AFTER_SECONDS
    return {
        "state": STATE_IDLE if stale else row.state,
        "updated_at": row.updated_at.isoformat(),
        "stale": stale,
    }
