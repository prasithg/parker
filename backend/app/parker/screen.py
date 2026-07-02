"""Screen state for the live patient screen (the "dad screen").

One row, overwritten on every exchange: what Parker heard, what it said,
and any choices waiting for a spoken number. The screen mirrors the
moment — it is deliberately not a transcript log, so nothing older than
the current exchange is ever stored here (pinned by tests).

The store is written by ``TextSession`` (text loop, talk loop, demo
replay all route through it) and read by the ``/parker/screen`` page.
The writer and the reader are usually different processes sharing the
local SQLite file, which is why this is a table and not session memory.

Privacy posture: the row holds only what Parker just said aloud in the
room — the heard utterance, the spoken reply, and choice labels by
position. Choice enrichment internals (parsed recipients, intent text)
never reach this table.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db.database import Base

SCREEN_STATE_ROW_ID = 1

# What the screen is waiting for from the person's voice (never a tap).
AWAITING_NOTHING = ""
AWAITING_CHOICE = "choices"  # numbered repair/confirmation choices are pending
AWAITING_YES_NO = "yes_no"  # a staged action awaits the spoken yes/no


class ScreenState(Base):
    """The single current exchange shown on the live patient screen."""

    __tablename__ = "screen_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Last utterance Parker heard; empty when Parker spoke first
    # (a confirmation offer) or the window was silence.
    heard: Mapped[str] = mapped_column(Text, default="")
    # What Parker said back, verbatim — the screen shows the spoken words.
    speech: Mapped[str] = mapped_column(Text, default="")
    # Response kind from the text loop (captured/choices/refused/...).
    kind: Mapped[str] = mapped_column(String(32), default="")
    # JSON list of {position, label} — exactly the numbered options that
    # were spoken aloud, nothing more.
    choices_json: Mapped[str] = mapped_column(Text, default="[]")
    awaiting: Mapped[str] = mapped_column(String(16), default=AWAITING_NOTHING)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


def publish_screen_state(
    db: Session,
    *,
    heard: str,
    speech: str,
    kind: str,
    choices: Optional[list[dict[str, Any]]] = None,
    awaiting: str = AWAITING_NOTHING,
    now: Optional[datetime] = None,
) -> ScreenState:
    """Overwrite the single screen-state row with the current exchange.

    ``choices`` may carry the full enriched choice dicts from the text
    loop; only ``position`` and ``label`` are kept — the screen shows the
    numbered cards that were spoken, never the capture internals.
    """

    stripped = [
        {"position": choice["position"], "label": choice["label"]}
        for choice in (choices or [])
    ]
    state = db.get(ScreenState, SCREEN_STATE_ROW_ID)
    if state is None:
        state = ScreenState(id=SCREEN_STATE_ROW_ID)
        db.add(state)
    state.heard = heard
    state.speech = speech
    state.kind = kind
    state.choices_json = json.dumps(stripped)
    state.awaiting = awaiting
    state.updated_at = now or datetime.utcnow()
    db.commit()
    db.refresh(state)
    return state


def get_screen_state(db: Session) -> Optional[ScreenState]:
    return db.get(ScreenState, SCREEN_STATE_ROW_ID)


def serialize_screen_state(state: ScreenState) -> dict[str, Any]:
    try:
        choices = json.loads(state.choices_json or "[]")
    except json.JSONDecodeError:
        choices = []
    return {
        "heard": state.heard,
        "speech": state.speech,
        "kind": state.kind,
        "choices": choices,
        "awaiting": state.awaiting,
        "updated_at": state.updated_at.isoformat() if state.updated_at else None,
    }
