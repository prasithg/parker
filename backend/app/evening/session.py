"""Local Parker evening-loop session state.

The recliner/TV loop is a gentle local check-in. It creates auditable rows for
caregiver review and never contacts an outside service.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db.database import Base

DEFAULT_ROUTINE_KEY = "recliner_tv"
EVENING_PROMPT_CLINICAL_DENYLIST = (
    "diagnosis",
    "treatment",
    "therapy",
    "medication",
    "symptom",
    "dose",
    "prescribed",
    "rehab",
)
_TERMINAL_STATUSES = {"declined", "completed", "timed_out", "cancelled"}


class NonResponseLadder(Protocol):
    """Future safety-spine seam for non-response handling."""

    def note_silence(self, session_id: int) -> None:
        """Record one local silence event for this session."""


class LocalEveningSession(Base):
    """Local recliner/TV evening-loop row for caregiver review.

    One row is created for each routine key and calendar evening. The row keeps
    the offer, repair prompt, state transitions, and caregiver review note on
    this machine only.
    """

    __tablename__ = "local_evening_sessions"
    __table_args__ = (
        UniqueConstraint("routine_key", "evening_date", name="uq_evening_session_routine_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    routine_key: Mapped[str] = mapped_column(String(64), default=DEFAULT_ROUTINE_KEY, index=True)
    evening_date: Mapped[str] = mapped_column(String(10), index=True)
    status: Mapped[str] = mapped_column(String(16), default="offered", index=True)
    prompt_card: Mapped[str] = mapped_column(Text)
    last_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    caregiver_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    engaged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    declined_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    timed_out_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    silence_noted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


def start_local_evening_session(
    db: Session,
    *,
    routine_key: str = DEFAULT_ROUTINE_KEY,
    now: datetime | str | None = None,
) -> LocalEveningSession:
    """Start or resume the local evening loop for one calendar evening."""

    current = _coerce_datetime(now) or datetime.utcnow()
    evening_date = current.date().isoformat()
    existing = (
        db.query(LocalEveningSession)
        .filter(LocalEveningSession.routine_key == routine_key)
        .filter(LocalEveningSession.evening_date == evening_date)
        .order_by(LocalEveningSession.id)
        .first()
    )
    if existing is not None:
        return existing

    session = LocalEveningSession(
        routine_key=routine_key,
        evening_date=evening_date,
        status="offered",
        prompt_card=_offer_prompt(),
        started_at=current,
        updated_at=current,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def record_evening_response(
    db: Session,
    session_id: int,
    response: str | None,
    *,
    now: datetime | str | None = None,
) -> LocalEveningSession:
    """Apply a local patient response to an evening-loop session."""

    session = _get_session(db, session_id)
    current = _coerce_datetime(now) or datetime.utcnow()
    text = (response or "").strip()

    if session.status in _TERMINAL_STATUSES:
        if text:
            session.last_response = text
            _append_caregiver_note(session, f"later response after {session.status}: {text}")
            session.updated_at = current
            db.commit()
            db.refresh(session)
        return session

    session.last_response = text
    session.updated_at = current
    lowered = text.lower()

    if _looks_like_decline(lowered):
        session.status = "declined"
        session.declined_at = current
        session.prompt_card = "No problem. We can skip the recliner and TV check-in tonight."
    elif _looks_like_cancel(lowered):
        session.status = "cancelled"
        session.cancelled_at = current
        session.prompt_card = "Okay, I cancelled tonight's local evening check-in."
    elif session.status == "engaged" and _looks_done(lowered):
        session.status = "completed"
        session.completed_at = current
        session.prompt_card = "All set. Have a quiet night. I'll leave this note for caregiver review."
    elif _looks_affirmative(lowered):
        session.status = "engaged"
        if session.engaged_at is None:
            session.engaged_at = current
        session.prompt_card = _engaged_prompt()
    else:
        session.prompt_card = _repair_prompt(text)

    db.commit()
    db.refresh(session)
    return session


def note_evening_silence(
    db: Session,
    session_id: int,
    *,
    non_response_ladder: NonResponseLadder | None = None,
    now: datetime | str | None = None,
) -> LocalEveningSession:
    """Mark a local silence timeout and notify the future ladder seam once."""

    session = _get_session(db, session_id)
    current = _coerce_datetime(now) or datetime.utcnow()
    if session.status not in _TERMINAL_STATUSES:
        session.status = "timed_out"
        session.timed_out_at = current
        session.prompt_card = (
            "I did not catch an answer, so I paused tonight's recliner and TV check-in "
            "and left it here for caregiver review."
        )
    if session.silence_noted_at is None:
        session.silence_noted_at = current
        if non_response_ladder is not None:
            non_response_ladder.note_silence(session.id)
    session.updated_at = current
    db.commit()
    db.refresh(session)
    return session


def complete_local_evening_session(
    db: Session,
    session_id: int,
    *,
    caregiver_note: str | None = None,
    now: datetime | str | None = None,
) -> LocalEveningSession | None:
    """Caregiver marks the local evening loop completed."""

    session = db.get(LocalEveningSession, session_id)
    if session is None:
        return None
    if session.status not in {"completed", "cancelled"}:
        current = _coerce_datetime(now) or datetime.utcnow()
        session.status = "completed"
        session.completed_at = current
        session.caregiver_note = caregiver_note
        session.updated_at = current
        db.commit()
        db.refresh(session)
    return session


def cancel_local_evening_session(
    db: Session,
    session_id: int,
    *,
    caregiver_note: str | None = None,
    now: datetime | str | None = None,
) -> LocalEveningSession | None:
    """Caregiver cancels a local evening-loop row before it is complete."""

    session = db.get(LocalEveningSession, session_id)
    if session is None:
        return None
    if session.status != "completed":
        current = _coerce_datetime(now) or datetime.utcnow()
        session.status = "cancelled"
        session.cancelled_at = current
        session.caregiver_note = caregiver_note
        session.updated_at = current
        db.commit()
        db.refresh(session)
    return session


def list_recent_local_evening_sessions(
    db: Session,
    *,
    limit: int = 10,
) -> list[LocalEveningSession]:
    """Return recent evening-loop rows for caregiver review."""

    return (
        db.query(LocalEveningSession)
        .order_by(LocalEveningSession.started_at.desc(), LocalEveningSession.id.desc())
        .limit(limit)
        .all()
    )


def _get_session(db: Session, session_id: int) -> LocalEveningSession:
    session = db.get(LocalEveningSession, session_id)
    if session is None:
        raise ValueError(f"Evening session not found: {session_id}")
    return session


def _offer_prompt() -> str:
    return (
        "Would you like a quick evening check-in? We can make sure the recliner feels "
        "comfortable and the TV is set up, or we can skip it for tonight."
    )


def _engaged_prompt() -> str:
    return (
        "Great. 1) Settle into the recliner and check that you feel steady. "
        "2) Tell me if the TV setup looks right. Say 'done' or 'goodnight' when you're all set."
    )


def _repair_prompt(response: str) -> str:
    prefix = "I didn't quite catch that" if response else "No rush"
    return (
        f"{prefix}. 1) Say 'yes' for the recliner and TV check-in, "
        "2) say 'not now' to skip tonight, or 3) say 'done' if you're already all set."
    )


def _looks_affirmative(text: str) -> bool:
    return text in {"yes", "yeah", "yep", "ok", "okay", "sure", "ready"} or text.startswith(
        ("yes ", "yeah ", "okay ", "ok ", "sure ", "let's ", "lets ")
    )


def _looks_like_decline(text: str) -> bool:
    return text in {"no", "nope", "not now", "skip", "later", "not tonight"} or text.startswith(
        ("no ", "not now", "skip ", "later ")
    )


def _looks_like_cancel(text: str) -> bool:
    return text in {"cancel", "stop", "changed my mind"} or text.startswith(("cancel ", "stop "))


def _looks_done(text: str) -> bool:
    return text in {"done", "all set", "goodnight", "good night", "finished"} or text.startswith(
        ("done ", "all set", "goodnight", "good night", "finished ")
    )


def _append_caregiver_note(session: LocalEveningSession, note: str) -> None:
    if not session.caregiver_note:
        session.caregiver_note = note
    elif note not in session.caregiver_note:
        session.caregiver_note = f"{session.caregiver_note}; {note}"


def _coerce_datetime(value: datetime | str | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)
