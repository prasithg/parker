"""Exercise session persistence and recommendations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db.database import Base
from app.exercises.library import get_exercise_types, select_exercise


@dataclass
class ExerciseSession:
    """In-memory exercise session summary."""

    exercise_type: str
    started_at: datetime
    patient_response: str | None = None
    score: int | None = None
    completed: bool = False


class ExerciseResult(Base):
    """Persisted cognitive exercise result."""

    __tablename__ = "exercise_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_log_id: Mapped[int] = mapped_column(Integer, ForeignKey("call_logs.id"), index=True)
    exercise_type: Mapped[str] = mapped_column(String(64), index=True)
    difficulty: Mapped[str] = mapped_column(String(16))
    prompt_given: Mapped[str] = mapped_column(Text)
    patient_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class LocalExerciseSession(Base):
    """Local Parker exercise session started from a confirmed staged action.

    This is a product-facing lifecycle row for v0: it records a safe prompt
    card and local status only. It does not claim therapeutic or clinical
    effect, launch external media, or send anything outside the machine.
    """

    __tablename__ = "local_exercise_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    staged_action_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("staged_actions.id"), nullable=True, index=True
    )
    call_log_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("call_logs.id"), nullable=True, index=True
    )
    subject: Mapped[str] = mapped_column(String(256))
    category: Mapped[str] = mapped_column(String(64), index=True)
    difficulty: Mapped[str] = mapped_column(String(32), default="gentle")
    prompt_card: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="started", index=True)
    caregiver_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


def start_local_exercise_session(
    db: Session,
    *,
    subject: str,
    staged_action_id: int | None = None,
    call_log_id: int | None = None,
    now: datetime | str | None = None,
) -> LocalExerciseSession:
    """Persist a local Parker exercise session with a safe prompt card."""

    category, prompt_card = _prompt_card_for_subject(subject)
    session = LocalExerciseSession(
        staged_action_id=staged_action_id,
        call_log_id=call_log_id,
        subject=subject,
        category=category,
        difficulty="gentle",
        prompt_card=prompt_card,
        status="started",
        started_at=_coerce_datetime(now) or datetime.utcnow(),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def complete_local_exercise_session(
    db: Session,
    session_id: int,
    *,
    caregiver_note: str | None = None,
    now: datetime | str | None = None,
) -> LocalExerciseSession | None:
    """Mark a local exercise session completed, if it is still active."""

    session = db.get(LocalExerciseSession, session_id)
    if session is None:
        return None
    if session.status == "started":
        session.status = "completed"
        session.completed_at = _coerce_datetime(now) or datetime.utcnow()
        session.caregiver_note = caregiver_note
        db.commit()
        db.refresh(session)
    return session


def cancel_local_exercise_session(
    db: Session,
    session_id: int,
    *,
    caregiver_note: str | None = None,
    now: datetime | str | None = None,
) -> LocalExerciseSession | None:
    """Cancel a local exercise session before it is completed."""

    session = db.get(LocalExerciseSession, session_id)
    if session is None:
        return None
    if session.status == "started":
        session.status = "cancelled"
        session.cancelled_at = _coerce_datetime(now) or datetime.utcnow()
        session.caregiver_note = caregiver_note
        db.commit()
        db.refresh(session)
    return session


def list_recent_local_exercise_sessions(
    db: Session,
    *,
    limit: int = 10,
) -> list[LocalExerciseSession]:
    """Return recent local exercise sessions for caregiver review."""

    return (
        db.query(LocalExerciseSession)
        .order_by(LocalExerciseSession.started_at.desc(), LocalExerciseSession.id.desc())
        .limit(limit)
        .all()
    )


def _prompt_card_for_subject(subject: str) -> tuple[str, str]:
    normalized = (subject or "short practice").strip()
    lowered = normalized.lower()
    details = normalized.split(":", 1)[1].strip() if ":" in normalized else normalized
    if any(word in lowered for word in ("movement", "stretch", "walking", "balance")):
        return (
            "movement",
            f"Gentle movement practice — {details}: sit or stand where you feel steady, "
            "try one small comfortable motion, then pause. Stop if you feel tired or unsure.",
        )
    if any(word in lowered for word in ("cognitive", "memory", "word", "recall", "trivia")):
        return (
            "cognitive",
            f"Gentle thinking practice — {details}: answer one short prompt, take your time, "
            "and skip it if it feels frustrating.",
        )
    return (
        "speech",
        f"Strong voice practice — {details}: sit comfortably, take one breath, "
        "say one short phrase clearly three times, then rest.",
    )


def _coerce_datetime(value: datetime | str | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def start_exercise(db: Session, call_log_id: int, exercise_type: str | None = None) -> tuple[ExerciseResult, str]:
    """Start an exercise and return the result row plus follow-up prompt."""

    exercise = select_exercise(exercise_type)
    result = ExerciseResult(
        call_log_id=call_log_id,
        exercise_type=exercise.name,
        difficulty=exercise.difficulty,
        prompt_given=exercise.prompt_template,
        completed=False,
    )
    db.add(result)
    db.commit()
    db.refresh(result)
    return result, exercise.follow_up_prompt


def complete_exercise(db: Session, result_id: int, patient_response: str, score: int | None = None) -> ExerciseResult | None:
    """Complete an exercise result with the patient's response and score."""

    if score is not None and not 1 <= score <= 5:
        raise ValueError("score must be between 1 and 5")
    result = db.get(ExerciseResult, result_id)
    if result is None:
        return None
    result.patient_response = patient_response
    result.score = score
    result.completed = True
    db.commit()
    db.refresh(result)
    return result


def get_exercise_history(db: Session, days: int = 30) -> list[ExerciseResult]:
    """Return recent exercise results."""

    since = datetime.utcnow() - timedelta(days=days)
    return (
        db.query(ExerciseResult)
        .filter(ExerciseResult.created_at >= since)
        .order_by(ExerciseResult.created_at.desc())
        .all()
    )


def get_recommended_exercise(db: Session) -> str:
    """Recommend the exercise type used least recently."""

    recent = get_exercise_history(db, days=30)
    by_type = {item.exercise_type: item.created_at for item in recent}
    for exercise_type in get_exercise_types():
        if exercise_type not in by_type:
            return exercise_type
    return min(by_type, key=lambda key: by_type[key])
