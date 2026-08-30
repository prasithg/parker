"""Behavior tests for Parker's patient-facing voice-practice data loop."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.exercises.session import LocalExerciseSession
from app.exercises.voice_practice import (
    VoicePracticeAttempt,
    VoicePracticeAudioArtifact,
    VoicePracticeSession,
    record_voice_practice_attempt,
)
from app.main import app
from app.parker.practice_router import VoicePracticeAttemptRequest


client = TestClient(app)


def _attempt_payload(**overrides):
    payload = {
        "client_attempt_id": "attempt-001",
        "practice_session_key": "practice-session-001",
        "sequence": 1,
        "exercise_key": "sustained_ah",
        "protocol_version": "sustained-ah-v1",
        "prompt_text": "Take a comfortable breath, then say ah steadily.",
        "target_seconds": 10.0,
        "duration_seconds": 8.4,
        "average_dbfs": -28.0,
        "peak_dbfs": -12.0,
        "threshold_dbfs": -35.0,
        "analyzed_sample_count": 504,
        "voiced_sample_count": 400,
        "in_target_sample_count": 304,
        "measurement_kind": "device_relative_dbfs",
        "measurement_algorithm_version": "rms-frame-v1",
        "sample_rate_hz": 48_000,
        "channel_count": 1,
        "auto_gain_control": False,
        "noise_suppression": False,
        "echo_cancellation": False,
        "self_rating": 2,
    }
    payload.update(overrides)
    return payload


def _record_kwargs(**overrides):
    payload = _attempt_payload(**overrides)
    return {
        key: payload[key]
        for key in (
            "client_attempt_id",
            "practice_session_key",
            "sequence",
            "exercise_key",
            "protocol_version",
            "prompt_text",
            "target_seconds",
            "duration_seconds",
            "average_dbfs",
            "peak_dbfs",
            "threshold_dbfs",
            "analyzed_sample_count",
            "voiced_sample_count",
            "in_target_sample_count",
            "measurement_kind",
            "measurement_algorithm_version",
            "sample_rate_hz",
            "channel_count",
            "auto_gain_control",
            "noise_suppression",
            "echo_cancellation",
            "self_rating",
        )
    }


def test_first_attempt_creates_parent_and_server_derives_fraction(db):
    response = client.post("/parker/practice/attempts", json=_attempt_payload())

    assert response.status_code == 201
    recorded = response.json()
    assert recorded["client_attempt_id"] == "attempt-001"
    assert recorded["practice_session_key"] == "practice-session-001"
    assert recorded["sequence"] == 1
    assert recorded["in_target_fraction"] == 0.76
    assert recorded["analyzed_sample_count"] == 504
    assert recorded["voiced_sample_count"] == 400
    assert recorded["in_target_sample_count"] == 304
    assert recorded["measurement_algorithm_version"] == "rms-frame-v1"
    assert recorded["sample_rate_hz"] == 48_000
    assert recorded["audio_saved"] is False
    assert recorded["audio_artifact_policy"] == "not_collected_v1"
    assert "audio_relative_path" not in recorded
    assert "audio_sha256" not in recorded

    practice = db.query(VoicePracticeSession).one()
    parent = db.get(LocalExerciseSession, practice.local_exercise_session_id)
    attempt = db.query(VoicePracticeAttempt).one()
    assert parent is not None
    assert parent.category == "speech"
    assert parent.status == "started"
    assert attempt.voice_practice_session_id == practice.id
    assert attempt.local_exercise_session_id == parent.id
    assert db.query(VoicePracticeAudioArtifact).count() == 0


def test_multiple_rounds_share_parent_and_completion_closes_lifecycle(db):
    first = client.post("/parker/practice/attempts", json=_attempt_payload())
    second = client.post(
        "/parker/practice/attempts",
        json=_attempt_payload(client_attempt_id="attempt-002", sequence=2),
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert db.query(LocalExerciseSession).count() == 1
    assert db.query(VoicePracticeSession).count() == 1
    assert db.query(VoicePracticeAttempt).count() == 2

    completed = client.post("/parker/practice/sessions/practice-session-001/complete")
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    practice = db.query(VoicePracticeSession).one()
    parent = db.get(LocalExerciseSession, practice.local_exercise_session_id)
    assert practice.status == "completed"
    assert parent is not None and parent.status == "completed"

    late_abandon = client.post("/parker/practice/sessions/practice-session-001/abandon")
    assert late_abandon.status_code == 200
    assert late_abandon.json()["status"] == "completed"
    db.refresh(practice)
    db.refresh(parent)
    assert practice.status == "completed"
    assert parent.status == "completed"

    blocked = client.post(
        "/parker/practice/attempts",
        json=_attempt_payload(client_attempt_id="attempt-003", sequence=3),
    )
    assert blocked.status_code == 409


def test_attempt_retry_is_idempotent_and_conflicting_reuse_is_rejected(db):
    first = client.post("/parker/practice/attempts", json=_attempt_payload())
    retry = client.post("/parker/practice/attempts", json=_attempt_payload())
    conflict = client.post(
        "/parker/practice/attempts",
        json=_attempt_payload(duration_seconds=9.4),
    )

    assert first.status_code == 201
    assert retry.status_code == 200
    assert retry.json()["id"] == first.json()["id"]
    assert conflict.status_code == 409
    assert db.query(VoicePracticeAttempt).count() == 1
    assert db.query(LocalExerciseSession).count() == 1


def test_sequence_collision_is_rejected(db):
    assert client.post("/parker/practice/attempts", json=_attempt_payload()).status_code == 201
    collision = client.post(
        "/parker/practice/attempts",
        json=_attempt_payload(client_attempt_id="attempt-other", sequence=1),
    )
    assert collision.status_code == 409
    assert db.query(VoicePracticeAttempt).count() == 1


def test_zero_voiced_samples_yield_null_fraction(db):
    response = client.post(
        "/parker/practice/attempts",
        json=_attempt_payload(
            voiced_sample_count=0,
            in_target_sample_count=0,
            average_dbfs=-100.0,
            peak_dbfs=-100.0,
        ),
    )
    assert response.status_code == 201
    assert response.json()["in_target_fraction"] is None


def test_long_manual_attempt_is_still_saveable(db):
    response = client.post(
        "/parker/practice/attempts",
        json=_attempt_payload(
            duration_seconds=3_600.0,
            analyzed_sample_count=216_000,
            voiced_sample_count=180_000,
            in_target_sample_count=120_000,
        ),
    )
    assert response.status_code == 201
    assert response.json()["duration_seconds"] == 3_600.0


def test_voice_practice_attempt_api_rejects_impossible_measurements(db):
    invalid_payloads = [
        _attempt_payload(voiced_sample_count=505),
        _attempt_payload(in_target_sample_count=401),
        _attempt_payload(voiced_sample_count=0, in_target_sample_count=1),
        _attempt_payload(peak_dbfs=4.0),
        _attempt_payload(duration_seconds=0.0),
        _attempt_payload(protocol_version="unknown-v9"),
        _attempt_payload(average_dbfs=-10.0, peak_dbfs=-20.0),
    ]
    for payload in invalid_payloads:
        assert client.post("/parker/practice/attempts", json=payload).status_code == 422
    with pytest.raises(ValidationError):
        VoicePracticeAttemptRequest.model_validate(
            _attempt_payload(duration_seconds=float("nan"))
        )
    assert db.query(VoicePracticeAttempt).count() == 0


def test_recent_attempts_are_newest_first_without_artifact_internals(db):
    assert client.post("/parker/practice/attempts", json=_attempt_payload()).status_code == 201
    assert client.post(
        "/parker/practice/attempts",
        json=_attempt_payload(client_attempt_id="attempt-002", sequence=2, duration_seconds=11.2),
    ).status_code == 201

    history = client.get("/parker/practice/attempts?limit=1")
    assert history.status_code == 200
    attempts = history.json()["attempts"]
    assert len(attempts) == 1
    assert attempts[0]["client_attempt_id"] == "attempt-002"
    assert attempts[0]["duration_seconds"] == 11.2
    assert "audio_relative_path" not in attempts[0]
    assert "audio_sha256" not in attempts[0]


def test_page_abandonment_closes_practice_and_parent(db):
    assert client.post("/parker/practice/attempts", json=_attempt_payload()).status_code == 201

    abandoned = client.post("/parker/practice/sessions/practice-session-001/abandon")
    assert abandoned.status_code == 200
    assert abandoned.json()["status"] == "abandoned"
    practice = db.query(VoicePracticeSession).one()
    parent = db.get(LocalExerciseSession, practice.local_exercise_session_id)
    assert practice.status == "abandoned"
    assert parent is not None and parent.status == "cancelled"

    complete = client.post("/parker/practice/sessions/practice-session-001/complete")
    assert complete.status_code == 409
    db.refresh(practice)
    db.refresh(parent)
    assert practice.status == "abandoned"
    assert parent.status == "cancelled"

    blocked = client.post(
        "/parker/practice/attempts",
        json=_attempt_payload(client_attempt_id="attempt-002", sequence=2),
    )
    assert blocked.status_code == 409


def test_abandon_beacon_before_inflight_save_creates_terminal_tombstone(db):
    abandoned = client.post("/parker/practice/sessions/practice-session-001/abandon")
    assert abandoned.status_code == 200
    assert abandoned.json()["status"] == "abandoned"

    late_save = client.post("/parker/practice/attempts", json=_attempt_payload())
    assert late_save.status_code == 409
    practice = db.query(VoicePracticeSession).one()
    parent = db.get(LocalExerciseSession, practice.local_exercise_session_id)
    assert practice.status == "abandoned"
    assert parent is not None and parent.status == "cancelled"
    assert db.query(VoicePracticeAttempt).count() == 0


def test_response_loss_after_committed_save_can_still_close_lifecycle(db):
    # Simulate the server committing while the browser never processes the response.
    client.post("/parker/practice/attempts", json=_attempt_payload())

    abandoned = client.post("/parker/practice/sessions/practice-session-001/abandon")
    assert abandoned.status_code == 200
    practice = db.query(VoicePracticeSession).one()
    parent = db.get(LocalExerciseSession, practice.local_exercise_session_id)
    assert practice.status == "abandoned"
    assert parent is not None and parent.status == "cancelled"


def test_optional_practice_audio_uses_separate_private_artifact(db, tmp_path, monkeypatch):
    monkeypatch.setenv("PARKER_HOME", str(tmp_path))
    audio = b"short-local-webm-sample"

    response = client.post(
        "/parker/practice/attempts",
        json=_attempt_payload(
            audio_mime="audio/webm",
            audio_base64=base64.b64encode(audio).decode("ascii"),
        ),
    )

    assert response.status_code == 201
    recorded = response.json()
    attempt = db.query(VoicePracticeAttempt).one()
    artifact = db.query(VoicePracticeAudioArtifact).one()
    assert recorded["audio_saved"] is True
    assert recorded["audio_artifact_policy"] == "optional_local_v1"
    assert "audio_relative_path" not in recorded
    assert "audio_sha256" not in recorded
    assert artifact.voice_practice_attempt_id == attempt.id
    assert artifact.sha256 == hashlib.sha256(audio).hexdigest()
    assert artifact.capture_purpose == "personal_practice_v1"
    assert artifact.allowed_use == "local_personalization_only_v1"
    relative = Path(artifact.relative_path)
    assert not relative.is_absolute()
    saved = tmp_path / relative
    assert saved.is_file()
    assert saved.read_bytes() == audio
    assert (tmp_path / "voice-practice").stat().st_mode & 0o777 == 0o700
    assert saved.parent.stat().st_mode & 0o777 == 0o700
    assert saved.stat().st_mode & 0o777 == 0o600
    assert saved.resolve().is_relative_to(tmp_path.resolve())

    retry = client.post(
        "/parker/practice/attempts",
        json=_attempt_payload(
            audio_mime="audio/webm",
            audio_base64=base64.b64encode(audio).decode("ascii"),
        ),
    )
    assert retry.status_code == 200
    assert retry.json()["id"] == recorded["id"]
    assert db.query(VoicePracticeAudioArtifact).count() == 1


def test_practice_audio_validation_fails_before_writing(db, tmp_path, monkeypatch):
    monkeypatch.setenv("PARKER_HOME", str(tmp_path))
    bad_mime = client.post(
        "/parker/practice/attempts",
        json=_attempt_payload(
            audio_mime="text/plain",
            audio_base64=base64.b64encode(b"not audio").decode("ascii"),
        ),
    )
    bad_encoding = client.post(
        "/parker/practice/attempts",
        json=_attempt_payload(audio_mime="audio/webm", audio_base64="not-base64!"),
    )
    too_large = client.post(
        "/parker/practice/attempts",
        json=_attempt_payload(
            audio_mime="audio/webm",
            audio_base64=base64.b64encode(b"x" * (2 * 1024 * 1024 + 1)).decode("ascii"),
        ),
    )

    assert bad_mime.status_code == 422
    assert bad_encoding.status_code == 422
    assert too_large.status_code == 413
    assert db.query(VoicePracticeAttempt).count() == 0
    assert not (tmp_path / "voice-practice").exists()


def test_practice_audio_is_removed_if_attempt_commit_fails(db, tmp_path, monkeypatch):
    monkeypatch.setenv("PARKER_HOME", str(tmp_path))

    def fail_commit():
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(db, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="simulated database failure"):
        record_voice_practice_attempt(
            db,
            **_record_kwargs(),
            audio_content=b"short-local-webm-sample",
            audio_mime="audio/webm",
        )

    assert not [path for path in tmp_path.rglob("*") if path.is_file()]
    assert db.query(VoicePracticeAttempt).count() == 0
    assert db.query(VoicePracticeSession).count() == 0
    assert db.query(LocalExerciseSession).count() == 0


def test_voice_practice_page_is_manual_accessible_and_provenance_aware(db):
    response = client.get("/parker/practice")

    assert response.status_code == 200
    html = response.text
    assert "Parker Voice Practice" in html
    assert "Sustained ah" in html
    assert 'id="start"' in html
    assert 'id="stop"' in html
    assert 'id="next"' in html
    assert 'id="finish"' in html
    assert 'id="save-audio"' in html
    assert 'aria-live="polite"' in html
    assert "You choose when to stop" in html
    assert "device-relative" in html
    assert "on this parker" in html.lower()
    assert "never uploaded" in html.lower()
    assert "/parker/practice/attempts" in html
    assert "/parker/practice/sessions/" in html
    assert "client_attempt_id" in html
    assert "voiced_sample_count" in html
    assert "measurement_algorithm_version" in html
    assert "getSettings()" in html
    assert "pagehide" in html
    assert "sendBeacon" in html
    assert "/abandon" in html
    assert "saveInFlight = true" in html
    assert "savedRoundCount > 0 || saveInFlight" in html
    assert "saveMayHaveReachedServer = true" in html
    assert "saveMayHaveReachedServer)" in html
    assert "if (!beaconQueued)" in html
    assert "keepalive: true" in html
    assert "Content-Security-Policy" in html
    assert "future personalization" in html
    assert "does not train from it yet" in html
    assert "Promise.race" in html
    assert "recorder.addEventListener('error'" in html
    assert "setTimeout" in html
    assert "setTimeout(next" not in html
    assert "autoplay" not in html


def test_practice_page_remains_available_when_caregiver_dashboard_is_locked(db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "dashboard_password", "open-sesame")

    assert client.get("/parker/review").status_code == 401
    assert client.get("/parker/practice").status_code == 200
    assert client.post("/parker/practice/attempts", json=_attempt_payload()).status_code == 201


def test_desktop_tray_pauses_listening_before_voice_practice():
    desktop = Path(__file__).resolve().parents[2] / "desktop" / "src-tauri"
    source = (desktop / "src" / "lib.rs").read_text()
    info_plist = (desktop / "Info.plist").read_text()

    branch = source.split('"voice-practice" => {', 1)[1].split("},", 1)[0]
    assert "state.manager.is_running(TALK)" in branch
    assert "state.manager.kill(TALK)" in branch
    assert branch.index("state.manager.kill(TALK)") < branch.index("open_engine_window")
    assert '"/parker/practice"' in branch
    assert '"Parker — Voice Practice"' in branch
    assert "voice-practice recordings are saved locally only when you choose" in info_plist
    assert "never stored or sent anywhere" not in info_plist
