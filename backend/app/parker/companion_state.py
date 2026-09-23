"""Persisted companion settings: the power switch and closed captions.

Power is a real product state, not page state (companion take 2,
2026-09-01): powered off must survive a restart so Parker never silently
re-enables listening, and powered on must come back after the living-room
machine reboots. One row, one household — the same feature-owned
single-row pattern as ``loop_state``/``screen``.

Privacy posture: two booleans and a timestamp. Nothing about what was
heard or said.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Boolean, DateTime, Integer
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db.database import Base

logger = logging.getLogger("parker.companion_state")

COMPANION_ROW_ID = 1


class CompanionSettings(Base):
    __tablename__ = "companion_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    power_on: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cc_on: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )


def get_companion_settings(db: Session) -> dict[str, Any]:
    row = db.get(CompanionSettings, COMPANION_ROW_ID)
    if row is None:
        # Fresh install: off until someone deliberately turns Parker on.
        return {"power_on": False, "cc_on": False, "updated_at": None}
    return {
        "power_on": bool(row.power_on),
        "cc_on": bool(row.cc_on),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def set_companion_settings(
    db: Session,
    *,
    power_on: Optional[bool] = None,
    cc_on: Optional[bool] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Persist the provided fields; omitted fields keep their value."""

    row = db.get(CompanionSettings, COMPANION_ROW_ID)
    if row is None:
        row = CompanionSettings(id=COMPANION_ROW_ID, power_on=False, cc_on=False)
        db.add(row)
    if power_on is not None:
        row.power_on = bool(power_on)
    if cc_on is not None:
        row.cc_on = bool(cc_on)
    row.updated_at = now or datetime.utcnow()
    db.commit()
    return get_companion_settings(db)
