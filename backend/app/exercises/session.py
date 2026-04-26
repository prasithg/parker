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
