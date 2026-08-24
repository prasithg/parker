"""Voice-practice lifecycle, aggregate measurements, and optional local audio."""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import suppress
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db.database import Base
from app.exercises.session import LocalExerciseSession, start_local_exercise_session


class VoicePracticeSession(Base):
    """One patient-controlled visit to the Voice Practice page."""

    __tablename__ = "voice_practice_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    practice_session_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    local_exercise_session_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("local_exercise_sessions.id"),
        unique=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(16), default="started", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class VoicePracticeAttempt(Base):
    """One protocol-versioned, device-relative sustained-voice attempt.

    ``in_target_fraction`` is derived on the server as in-target frames over
    voiced frames. It is NULL when no voiced frame is detected. Levels are
    device-relative dBFS summaries, not calibrated sound pressure or a
    clinical measurement. ``self_rating`` is the fixed v1 ordinal:
    1 comfortable, 2 okay, 3 effortful.
    """

    __tablename__ = "voice_practice_attempts"
    __table_args__ = (
        UniqueConstraint(
            "practice_session_key",
            "sequence",
            name="uq_voice_practice_attempt_session_sequence",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_attempt_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    voice_practice_session_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("voice_practice_sessions.id"),
        index=True,
    )
    local_exercise_session_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("local_exercise_sessions.id"),
        index=True,
    )
    practice_session_key: Mapped[str] = mapped_column(String(64), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    exercise_key: Mapped[str] = mapped_column(String(64), index=True)
    protocol_version: Mapped[str] = mapped_column(String(64), index=True)
    prompt_text: Mapped[str] = mapped_column(Text)
    target_seconds: Mapped[float] = mapped_column(Float)
    duration_seconds: Mapped[float] = mapped_column(Float)
    average_dbfs: Mapped[float] = mapped_column(Float)
    peak_dbfs: Mapped[float] = mapped_column(Float)
    threshold_dbfs: Mapped[float] = mapped_column(Float)
    analyzed_sample_count: Mapped[int] = mapped_column(Integer)
    voiced_sample_count: Mapped[int] = mapped_column(Integer)
    in_target_sample_count: Mapped[int] = mapped_column(Integer)
    in_target_fraction: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    measurement_kind: Mapped[str] = mapped_column(String(64))
    measurement_algorithm_version: Mapped[str] = mapped_column(String(64))
    sample_rate_hz: Mapped[int] = mapped_column(Integer)
    channel_count: Mapped[int] = mapped_column(Integer)
    auto_gain_control: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    noise_suppression: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    echo_cancellation: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    self_rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(64), default="practice_ui")
    audio_artifact_policy: Mapped[str] = mapped_column(
        String(64),
        default="not_collected_v1",
    )
    payload_sha256: Mapped[str] = mapped_column(String(64))
    completed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class VoicePracticeAudioArtifact(Base):
    """Separately scoped local audio explicitly retained for one attempt."""

    __tablename__ = "voice_practice_audio_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    voice_practice_attempt_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("voice_practice_attempts.id"),
        unique=True,
        index=True,
    )
    relative_path: Mapped[str] = mapped_column(Text)
    mime: Mapped[str] = mapped_column(String(64))
    byte_count: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    capture_purpose: Mapped[str] = mapped_column(
        String(64),
        default="personal_practice_v1",
    )
    allowed_use: Mapped[str] = mapped_column(
        String(64),
        default="local_personalization_only_v1",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


_PRACTICE_AUDIO_EXTENSIONS = {
    "audio/aac": ".aac",
    "audio/mp4": ".m4a",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/webm": ".webm",
    "audio/x-wav": ".wav",
}


def record_voice_practice_attempt(
    db: Session,
    *,
    client_attempt_id: str,
    practice_session_key: str,
    sequence: int,
    exercise_key: str,
    protocol_version: str,
    prompt_text: str,
    target_seconds: float,
    duration_seconds: float,
    average_dbfs: float,
    peak_dbfs: float,
    threshold_dbfs: float,
    analyzed_sample_count: int,
    voiced_sample_count: int,
    in_target_sample_count: int,
    measurement_kind: str,
    measurement_algorithm_version: str,
    sample_rate_hz: int,
    channel_count: int,
    auto_gain_control: bool | None,
    noise_suppression: bool | None,
    echo_cancellation: bool | None,
    self_rating: int | None = None,
    source: str = "practice_ui",
    audio_content: bytes | None = None,
    audio_mime: str | None = None,
    now: datetime | str | None = None,
) -> tuple[VoicePracticeAttempt, bool]:
    """Persist one attempt; identical client retries return the existing row."""

    _validate_counts(
        analyzed=analyzed_sample_count,
        voiced=voiced_sample_count,
        in_target=in_target_sample_count,
    )
    if audio_content is not None:
        if not audio_content:
            raise ValueError("practice audio must not be empty")
        if audio_mime not in _PRACTICE_AUDIO_EXTENSIONS:
            raise ValueError(f"unsupported practice audio type: {audio_mime}")
    elif audio_mime is not None:
        raise ValueError("practice audio type requires audio content")

    completed_at = _coerce_datetime(now) or datetime.utcnow()
    audio_sha256 = hashlib.sha256(audio_content).hexdigest() if audio_content else None
    fingerprint = _payload_fingerprint(
        client_attempt_id=client_attempt_id,
        practice_session_key=practice_session_key,
        sequence=sequence,
        exercise_key=exercise_key,
        protocol_version=protocol_version,
        prompt_text=prompt_text,
        target_seconds=target_seconds,
        duration_seconds=duration_seconds,
        average_dbfs=average_dbfs,
        peak_dbfs=peak_dbfs,
        threshold_dbfs=threshold_dbfs,
        analyzed_sample_count=analyzed_sample_count,
        voiced_sample_count=voiced_sample_count,
        in_target_sample_count=in_target_sample_count,
        measurement_kind=measurement_kind,
        measurement_algorithm_version=measurement_algorithm_version,
        sample_rate_hz=sample_rate_hz,
        channel_count=channel_count,
        auto_gain_control=auto_gain_control,
        noise_suppression=noise_suppression,
        echo_cancellation=echo_cancellation,
        self_rating=self_rating,
        source=source,
        audio_mime=audio_mime,
        audio_sha256=audio_sha256,
    )

    existing = (
        db.query(VoicePracticeAttempt)
        .filter(VoicePracticeAttempt.client_attempt_id == client_attempt_id)
        .one_or_none()
    )
    if existing is not None:
        if existing.payload_sha256 != fingerprint:
            raise ValueError("conflicting practice attempt id")
        return existing, False

    sequence_collision = (
        db.query(VoicePracticeAttempt)
        .filter(
            VoicePracticeAttempt.practice_session_key == practice_session_key,
            VoicePracticeAttempt.sequence == sequence,
        )
        .first()
    )
    if sequence_collision is not None:
        raise ValueError("practice sequence already recorded")

    practice = (
        db.query(VoicePracticeSession)
        .filter(VoicePracticeSession.practice_session_key == practice_session_key)
        .one_or_none()
    )
    if practice is None:
        parent = start_local_exercise_session(
            db,
            subject="voice practice: sustained ah",
            now=completed_at,
            commit=False,
        )
        practice = VoicePracticeSession(
            practice_session_key=practice_session_key,
            local_exercise_session_id=parent.id,
            status="started",
            created_at=completed_at,
        )
        db.add(practice)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            current = (
                db.query(VoicePracticeSession)
                .filter(VoicePracticeSession.practice_session_key == practice_session_key)
                .one_or_none()
            )
            if current is not None and current.status != "started":
                raise ValueError("voice practice session is already closed")
            raise ValueError("voice practice session changed while saving; try again")
    elif practice.status != "started":
        raise ValueError("voice practice session is already closed")

    fraction = (
        in_target_sample_count / voiced_sample_count
        if voiced_sample_count > 0
        else None
    )
    policy = "optional_local_v1" if audio_content is not None else "not_collected_v1"
    attempt = VoicePracticeAttempt(
        client_attempt_id=client_attempt_id,
        voice_practice_session_id=practice.id,
        local_exercise_session_id=practice.local_exercise_session_id,
        practice_session_key=practice_session_key,
        sequence=sequence,
        exercise_key=exercise_key,
        protocol_version=protocol_version,
        prompt_text=prompt_text,
        target_seconds=target_seconds,
        duration_seconds=duration_seconds,
        average_dbfs=average_dbfs,
        peak_dbfs=peak_dbfs,
        threshold_dbfs=threshold_dbfs,
        analyzed_sample_count=analyzed_sample_count,
        voiced_sample_count=voiced_sample_count,
        in_target_sample_count=in_target_sample_count,
        in_target_fraction=fraction,
        measurement_kind=measurement_kind,
        measurement_algorithm_version=measurement_algorithm_version,
        sample_rate_hz=sample_rate_hz,
        channel_count=channel_count,
        auto_gain_control=auto_gain_control,
        noise_suppression=noise_suppression,
        echo_cancellation=echo_cancellation,
        self_rating=self_rating,
        source=source,
        audio_artifact_policy=policy,
        payload_sha256=fingerprint,
        completed_at=completed_at,
    )
    db.add(attempt)
    db.flush()

    saved_audio = None
    if audio_content is not None and audio_mime is not None:
        saved_audio = _save_voice_practice_audio(
            content=audio_content,
            mime=audio_mime,
            completed_at=completed_at,
        )
        db.add(
            VoicePracticeAudioArtifact(
                voice_practice_attempt_id=attempt.id,
                relative_path=saved_audio,
                mime=audio_mime,
                byte_count=len(audio_content),
                sha256=audio_sha256 or "",
                capture_purpose="personal_practice_v1",
                allowed_use="local_personalization_only_v1",
                created_at=completed_at,
            )
        )

    try:
        db.commit()
    except BaseException:
        db.rollback()
        if saved_audio is not None:
            from app import paths

            with suppress(OSError):
                (paths.parker_home() / saved_audio).unlink()
        raise
    db.refresh(attempt)
    return attempt, True


def complete_voice_practice_session(
    db: Session,
    practice_session_key: str,
    *,
    now: datetime | str | None = None,
) -> VoicePracticeSession | None:
    """Atomically complete a started practice and its generic parent."""

    practice = (
        db.query(VoicePracticeSession)
        .filter(VoicePracticeSession.practice_session_key == practice_session_key)
        .one_or_none()
    )
    if practice is None:
        return None
    if practice.status == "completed":
        return practice
    if practice.status != "started":
        raise ValueError("voice practice session is already abandoned")

    completed_at = _coerce_datetime(now) or datetime.utcnow()
    practice_updated = (
        db.query(VoicePracticeSession)
        .filter(
            VoicePracticeSession.id == practice.id,
            VoicePracticeSession.status == "started",
        )
        .update(
            {"status": "completed", "completed_at": completed_at},
            synchronize_session=False,
        )
    )
    parent_updated = (
        db.query(LocalExerciseSession)
        .filter(
            LocalExerciseSession.id == practice.local_exercise_session_id,
            LocalExerciseSession.status == "started",
        )
        .update(
            {"status": "completed", "completed_at": completed_at},
            synchronize_session=False,
        )
    )
    if practice_updated != 1 or parent_updated != 1:
        db.rollback()
        db.expire_all()
        current = (
            db.query(VoicePracticeSession)
            .filter(VoicePracticeSession.practice_session_key == practice_session_key)
            .one_or_none()
        )
        if current is not None and current.status == "completed":
            return current
        raise ValueError("voice practice session closed while finishing")
    db.commit()
    db.expire_all()
    return (
        db.query(VoicePracticeSession)
        .filter(VoicePracticeSession.practice_session_key == practice_session_key)
        .one()
    )


def abandon_voice_practice_session(
    db: Session,
    practice_session_key: str,
    *,
    now: datetime | str | None = None,
) -> VoicePracticeSession:
    """Atomically abandon a practice, creating a tombstone if save is in flight."""

    abandoned_at = _coerce_datetime(now) or datetime.utcnow()
    practice = (
        db.query(VoicePracticeSession)
        .filter(VoicePracticeSession.practice_session_key == practice_session_key)
        .one_or_none()
    )
    if practice is None:
        parent = start_local_exercise_session(
            db,
            subject="voice practice: sustained ah",
            now=abandoned_at,
            commit=False,
        )
        parent.status = "cancelled"
        parent.cancelled_at = abandoned_at
        parent.caregiver_note = "Voice Practice page closed while Save was finishing."
        practice = VoicePracticeSession(
            practice_session_key=practice_session_key,
            local_exercise_session_id=parent.id,
            status="abandoned",
            created_at=abandoned_at,
            completed_at=abandoned_at,
        )
        db.add(practice)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            current = (
                db.query(VoicePracticeSession)
                .filter(VoicePracticeSession.practice_session_key == practice_session_key)
                .one_or_none()
            )
            if current is None:
                raise
            return abandon_voice_practice_session(
                db,
                practice_session_key,
                now=abandoned_at,
            )
        db.refresh(practice)
        return practice
    if practice.status != "started":
        return practice

    practice_updated = (
        db.query(VoicePracticeSession)
        .filter(
            VoicePracticeSession.id == practice.id,
            VoicePracticeSession.status == "started",
        )
        .update(
            {"status": "abandoned", "completed_at": abandoned_at},
            synchronize_session=False,
        )
    )
    parent_updated = (
        db.query(LocalExerciseSession)
        .filter(
            LocalExerciseSession.id == practice.local_exercise_session_id,
            LocalExerciseSession.status == "started",
        )
        .update(
            {
                "status": "cancelled",
                "cancelled_at": abandoned_at,
                "caregiver_note": "Voice Practice page closed before Finish for today.",
            },
            synchronize_session=False,
        )
    )
    if practice_updated != 1 or parent_updated != 1:
        db.rollback()
        db.expire_all()
        current = (
            db.query(VoicePracticeSession)
            .filter(VoicePracticeSession.practice_session_key == practice_session_key)
            .one()
        )
        if current.status in {"abandoned", "completed"}:
            return current
        raise ValueError("voice practice session changed while abandoning")
    db.commit()
    db.expire_all()
    return (
        db.query(VoicePracticeSession)
        .filter(VoicePracticeSession.practice_session_key == practice_session_key)
        .one()
    )


def list_recent_voice_practice_attempts(
    db: Session,
    *,
    limit: int = 20,
) -> list[VoicePracticeAttempt]:
    return (
        db.query(VoicePracticeAttempt)
        .order_by(VoicePracticeAttempt.completed_at.desc(), VoicePracticeAttempt.id.desc())
        .limit(limit)
        .all()
    )


def _validate_counts(*, analyzed: int, voiced: int, in_target: int) -> None:
    if analyzed < 1 or voiced < 0 or in_target < 0:
        raise ValueError("practice sample counts must be non-negative")
    if voiced > analyzed or in_target > voiced:
        raise ValueError("practice sample counts are inconsistent")


def _payload_fingerprint(**payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _save_voice_practice_audio(
    *,
    content: bytes,
    mime: str,
    completed_at: datetime,
) -> str:
    from app import paths

    home = paths.ensure_parker_home()
    practice_dir = paths.voice_practice_dir()
    audio_dir = practice_dir / "audio"
    destination_dir = audio_dir / completed_at.date().isoformat()
    destination_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    for private_dir in (practice_dir, audio_dir, destination_dir):
        os.chmod(private_dir, 0o700)
    destination = destination_dir / f"{uuid4().hex}{_PRACTICE_AUDIO_EXTENSIONS[mime]}"
    temporary = destination.with_name(f".{destination.name}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()
    return str(destination.relative_to(home))


def _coerce_datetime(value: datetime | str | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)
