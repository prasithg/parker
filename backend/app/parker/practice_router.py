"""Narrow patient-facing routes for Parker Voice Practice."""

from __future__ import annotations

import base64
import binascii
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.exercises.voice_practice import (
    VoicePracticeAttempt,
    abandon_voice_practice_session,
    complete_voice_practice_session,
    list_recent_voice_practice_attempts,
    record_voice_practice_attempt,
)
from app.parker.practice_ui import PRACTICE_PAGE_HTML


router = APIRouter()
MAX_PRACTICE_AUDIO_BYTES = 2 * 1024 * 1024


class VoicePracticeAttemptRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    client_attempt_id: str = Field(min_length=1, max_length=64)
    practice_session_key: str = Field(min_length=1, max_length=64)
    sequence: int = Field(ge=1, le=20)
    exercise_key: Literal["sustained_ah"]
    protocol_version: Literal["sustained-ah-v1"]
    prompt_text: Literal["Take a comfortable breath, then say ah steadily."]
    target_seconds: float = Field(gt=0, le=60)
    duration_seconds: float = Field(gt=0)
    average_dbfs: float = Field(ge=-160, le=0)
    peak_dbfs: float = Field(ge=-160, le=0)
    threshold_dbfs: float = Field(ge=-160, le=0)
    analyzed_sample_count: int = Field(ge=1)
    voiced_sample_count: int = Field(ge=0)
    in_target_sample_count: int = Field(ge=0)
    measurement_kind: Literal["device_relative_dbfs"]
    measurement_algorithm_version: Literal["rms-frame-v1"]
    sample_rate_hz: int = Field(ge=8_000, le=384_000)
    channel_count: int = Field(ge=1, le=8)
    auto_gain_control: bool | None = None
    noise_suppression: bool | None = None
    echo_cancellation: bool | None = None
    self_rating: int | None = Field(default=None, ge=1, le=3)
    audio_mime: Literal[
        "audio/aac", "audio/mp4", "audio/ogg", "audio/wav", "audio/webm", "audio/x-wav"
    ] | None = None
    audio_base64: str | None = Field(default=None, max_length=2_900_000)

    @model_validator(mode="after")
    def validate_measurement_relationships(self) -> "VoicePracticeAttemptRequest":
        if self.target_seconds != 10.0 or self.threshold_dbfs != -35.0:
            raise ValueError("sustained-ah-v1 requires its fixed target and threshold")
        if self.average_dbfs > self.peak_dbfs:
            raise ValueError("average_dbfs must not exceed peak_dbfs")
        if self.voiced_sample_count > self.analyzed_sample_count:
            raise ValueError("voiced samples must not exceed analyzed samples")
        if self.in_target_sample_count > self.voiced_sample_count:
            raise ValueError("in-target samples must not exceed voiced samples")
        if (self.audio_base64 is None) != (self.audio_mime is None):
            raise ValueError("audio_mime and audio_base64 must be provided together")
        return self


@router.get("/practice", response_class=HTMLResponse, include_in_schema=False)
def voice_practice_page() -> str:
    return PRACTICE_PAGE_HTML


@router.get("/practice/attempts")
def recent_voice_practice_attempts(
    limit: int = 20,
    db: Session = Depends(get_db),
) -> dict[str, list[dict[str, Any]]]:
    bounded_limit = max(1, min(limit, 100))
    return {
        "attempts": [
            serialize_voice_practice_attempt(attempt)
            for attempt in list_recent_voice_practice_attempts(db, limit=bounded_limit)
        ]
    }


@router.post("/practice/attempts", status_code=201)
def create_voice_practice_attempt(
    payload: VoicePracticeAttemptRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    audio_content = None
    if payload.audio_base64 is not None:
        try:
            audio_content = base64.b64decode(payload.audio_base64.encode("ascii"), validate=True)
        except (UnicodeEncodeError, ValueError, binascii.Error):
            raise HTTPException(status_code=422, detail="audio_base64 is not valid base64")
        if not audio_content:
            raise HTTPException(status_code=422, detail="practice audio must not be empty")
        if len(audio_content) > MAX_PRACTICE_AUDIO_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"practice audio exceeds {MAX_PRACTICE_AUDIO_BYTES} bytes",
            )

    try:
        attempt, created = record_voice_practice_attempt(
            db,
            client_attempt_id=payload.client_attempt_id,
            practice_session_key=payload.practice_session_key,
            sequence=payload.sequence,
            exercise_key=payload.exercise_key,
            protocol_version=payload.protocol_version,
            prompt_text=payload.prompt_text,
            target_seconds=payload.target_seconds,
            duration_seconds=payload.duration_seconds,
            average_dbfs=payload.average_dbfs,
            peak_dbfs=payload.peak_dbfs,
            threshold_dbfs=payload.threshold_dbfs,
            analyzed_sample_count=payload.analyzed_sample_count,
            voiced_sample_count=payload.voiced_sample_count,
            in_target_sample_count=payload.in_target_sample_count,
            measurement_kind=payload.measurement_kind,
            measurement_algorithm_version=payload.measurement_algorithm_version,
            sample_rate_hz=payload.sample_rate_hz,
            channel_count=payload.channel_count,
            auto_gain_control=payload.auto_gain_control,
            noise_suppression=payload.noise_suppression,
            echo_cancellation=payload.echo_cancellation,
            self_rating=payload.self_rating,
            audio_content=audio_content,
            audio_mime=payload.audio_mime,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not created:
        response.status_code = 200
    return serialize_voice_practice_attempt(attempt)


@router.post("/practice/sessions/{practice_session_key}/complete")
def complete_practice_session(
    practice_session_key: str = Path(min_length=1, max_length=64),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        practice = complete_voice_practice_session(db, practice_session_key)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if practice is None:
        raise HTTPException(status_code=404, detail="Voice practice session not found")
    return {
        "practice_session_key": practice.practice_session_key,
        "local_exercise_session_id": practice.local_exercise_session_id,
        "status": practice.status,
        "completed_at": practice.completed_at.isoformat() if practice.completed_at else None,
    }


@router.post("/practice/sessions/{practice_session_key}/abandon")
def abandon_practice_session(
    practice_session_key: str = Path(min_length=1, max_length=64),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        practice = abandon_voice_practice_session(db, practice_session_key)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if practice is None:
        raise HTTPException(status_code=404, detail="Voice practice session not found")
    return {
        "practice_session_key": practice.practice_session_key,
        "local_exercise_session_id": practice.local_exercise_session_id,
        "status": practice.status,
        "completed_at": practice.completed_at.isoformat() if practice.completed_at else None,
    }


def serialize_voice_practice_attempt(attempt: VoicePracticeAttempt) -> dict[str, Any]:
    return {
        "id": attempt.id,
        "client_attempt_id": attempt.client_attempt_id,
        "practice_session_key": attempt.practice_session_key,
        "sequence": attempt.sequence,
        "exercise_key": attempt.exercise_key,
        "protocol_version": attempt.protocol_version,
        "prompt_text": attempt.prompt_text,
        "target_seconds": attempt.target_seconds,
        "duration_seconds": attempt.duration_seconds,
        "average_dbfs": attempt.average_dbfs,
        "peak_dbfs": attempt.peak_dbfs,
        "threshold_dbfs": attempt.threshold_dbfs,
        "analyzed_sample_count": attempt.analyzed_sample_count,
        "voiced_sample_count": attempt.voiced_sample_count,
        "in_target_sample_count": attempt.in_target_sample_count,
        "in_target_fraction": attempt.in_target_fraction,
        "measurement_kind": attempt.measurement_kind,
        "measurement_algorithm_version": attempt.measurement_algorithm_version,
        "sample_rate_hz": attempt.sample_rate_hz,
        "channel_count": attempt.channel_count,
        "auto_gain_control": attempt.auto_gain_control,
        "noise_suppression": attempt.noise_suppression,
        "echo_cancellation": attempt.echo_cancellation,
        "self_rating": attempt.self_rating,
        "source": attempt.source,
        "audio_saved": attempt.audio_artifact_policy == "optional_local_v1",
        "audio_artifact_policy": attempt.audio_artifact_policy,
        "completed_at": attempt.completed_at.isoformat() if attempt.completed_at else None,
    }
