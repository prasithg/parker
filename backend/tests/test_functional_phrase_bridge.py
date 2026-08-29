"""Contract tests for the voluntary Voice Practice → Functional Phrase bridge."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.conversation.outcomes import InteractionOutcome
from app.db.models import CallLog, CapturedIntent, StagedAction
from app.exercises.voice_practice import VoicePracticeAudioArtifact
from app.main import app
from app.parker import practice_router
from app.parker.pipeline import capture_intent
from app.parker.rollup import build_weekly_rollup


client = TestClient(app)


def _round_payload(**overrides):
    payload = {
        "client_attempt_id": "round-001",
        "practice_session_key": "practice-session-phrase",
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


def _save_round(**overrides):
    response = client.post("/parker/practice/attempts", json=_round_payload(**overrides))
    assert response.status_code == 201
    return response


def _phrase_payload(**overrides):
    payload = {
        "client_attempt_id": "phrase-001",
        "practice_session_key": "practice-session-phrase",
        "audio_mime": "audio/webm",
        "audio_base64": base64.b64encode(b"synthetic-browser-audio").decode("ascii"),
    }
    payload.update(overrides)
    return payload


def _inject_transcript(monkeypatch, lines: list[str], observed: list[Path] | None = None):
    def fake(path: Path) -> list[str]:
        assert path.is_file()
        assert path.read_bytes() == b"synthetic-browser-audio"
        if observed is not None:
            observed.append(path)
        return lines

    monkeypatch.setattr(practice_router, "functional_phrase_transcriber", fake, raising=False)


def test_functional_phrase_config_has_safe_default_and_family_override(db, monkeypatch):
    default = client.get("/parker/practice/functional-phrase")

    assert default.status_code == 200
    assert default.json() == {
        "phrase": "Remind me to water the plants this evening.",
        "audio_handling": "ephemeral_local_transcription",
    }

    monkeypatch.setattr(settings, "parker_functional_phrase", "Remind me to call Alex after lunch.")
    configured = client.get("/parker/practice/functional-phrase")
    assert configured.status_code == 200
    assert configured.json()["phrase"] == "Remind me to call Alex after lunch."


def test_phrase_requires_a_saved_sustained_round_before_transcription(db, monkeypatch):
    called = False

    def must_not_transcribe(path: Path) -> list[str]:
        nonlocal called
        called = True
        return ["Remind me to water the plants this evening"]

    monkeypatch.setattr(
        practice_router,
        "functional_phrase_transcriber",
        must_not_transcribe,
        raising=False,
    )

    response = client.post("/parker/practice/functional-phrase/attempts", json=_phrase_payload())

    assert response.status_code == 409
    assert called is False
    assert db.query(CallLog).count() == 0
    assert db.query(CapturedIntent).count() == 0


def test_phrase_audio_is_ephemeral_and_clear_request_only_stages_confirmation(db, monkeypatch):
    _save_round()
    observed: list[Path] = []
    _inject_transcript(
        monkeypatch,
        ["Remind me to water the plants this evening"],
        observed,
    )

    response = client.post("/parker/practice/functional-phrase/attempts", json=_phrase_payload())

    assert response.status_code == 200
    result = response.json()
    assert result["heard"] == "Remind me to water the plants this evening"
    assert result["kind"] == "confirm_offer"
    assert result["awaiting_confirmation"] is True
    assert "water the plants this evening" in result["speech"]
    assert result["choices"] == []
    assert "audio_base64" not in result
    assert "audio_path" not in result
    assert "audio_sha256" not in result
    assert observed and not observed[0].exists()

    captured = db.query(CapturedIntent).one()
    staged = db.query(StagedAction).one()
    assert captured.requested_action == "remind"
    assert captured.status == "resolved"
    assert staged.status == "staged"
    assert staged.confirmed_at is None
    assert staged.executed_at is None
    assert db.query(VoicePracticeAudioArtifact).count() == 0
    outcome = db.query(InteractionOutcome).one()
    assert outcome.source == "functional_phrase_practice"
    natural_use = build_weekly_rollup(
        db,
        week_of=datetime.now(timezone.utc).date(),
        tz=timezone.utc,
    )
    assert natural_use["totals"]["all_interactions"] == 0


def test_phrase_submission_is_idempotency_bounded_and_does_not_stage_unrelated_work(db, monkeypatch):
    _save_round()
    unrelated_call = CallLog(call_sid="unrelated-call", call_type="text_loop")
    db.add(unrelated_call)
    db.commit()
    db.refresh(unrelated_call)
    unrelated = capture_intent(
        db,
        call_log_id=unrelated_call.id,
        intent_text="Remind me to check the mail",
        requested_action="remind",
        subject="check the mail",
    )
    _inject_transcript(monkeypatch, ["Remind me to water the plants this evening"])

    first = client.post("/parker/practice/functional-phrase/attempts", json=_phrase_payload())
    duplicate = client.post("/parker/practice/functional-phrase/attempts", json=_phrase_payload())

    assert first.status_code == 200
    assert duplicate.status_code == 409
    db.refresh(unrelated)
    assert unrelated.status == "pending"
    assert db.query(StagedAction).count() == 1


def test_explicit_yes_uses_existing_confirmation_path_and_no_cancels(db, monkeypatch):
    _save_round()
    _inject_transcript(monkeypatch, ["Remind me to water the plants this evening"])
    assert client.post(
        "/parker/practice/functional-phrase/attempts",
        json=_phrase_payload(),
    ).status_code == 200

    yes = client.post(
        "/parker/practice/functional-phrase/decision",
        json={
            "client_attempt_id": "phrase-001",
            "practice_session_key": "practice-session-phrase",
            "decision": "yes",
        },
    )

    assert yes.status_code == 200
    assert yes.json()["kind"] == "executed"
    staged = db.query(StagedAction).one()
    assert staged.status == "executed"
    assert staged.confirmed_by == "patient"
    assert staged.executed_at is not None

    _inject_transcript(monkeypatch, ["Remind me to close the window"])
    second = client.post(
        "/parker/practice/functional-phrase/attempts",
        json=_phrase_payload(client_attempt_id="phrase-002"),
    )
    assert second.status_code == 200
    no = client.post(
        "/parker/practice/functional-phrase/decision",
        json={
            "client_attempt_id": "phrase-002",
            "practice_session_key": "practice-session-phrase",
            "decision": "no",
        },
    )
    assert no.status_code == 200
    assert no.json()["kind"] == "cancelled"
    assert db.query(StagedAction).order_by(StagedAction.id.desc()).first().status == "cancelled"


def test_wrong_readback_uses_existing_confirmation_repair_and_never_executes(db, monkeypatch):
    _save_round()
    _inject_transcript(monkeypatch, ["Remind me to water the plants this evening"])
    assert client.post(
        "/parker/practice/functional-phrase/attempts",
        json=_phrase_payload(),
    ).status_code == 200

    wrong = client.post(
        "/parker/practice/functional-phrase/decision",
        json={
            "client_attempt_id": "phrase-001",
            "practice_session_key": "practice-session-phrase",
            "decision": "none_of_these",
        },
    )

    assert wrong.status_code == 200
    assert wrong.json()["kind"] == "confirmation_repair"
    staged = db.query(StagedAction).one()
    assert staged.status == "cancelled"
    assert staged.executed_at is None


def test_degraded_phrase_holds_with_existing_repair_choices_then_allows_manual_retry(db, monkeypatch):
    _save_round()
    _inject_transcript(monkeypatch, ["Call... the... you know... the one with the garden..."])

    degraded = client.post(
        "/parker/practice/functional-phrase/attempts",
        json=_phrase_payload(client_attempt_id="phrase-vague"),
    )

    assert degraded.status_code == 200
    result = degraded.json()
    assert result["kind"] == "choices"
    assert result["awaiting_confirmation"] is False
    assert len(result["choices"]) >= 2
    assert all(set(choice) == {"position", "label"} for choice in result["choices"])
    assert db.query(CapturedIntent).count() == 0
    assert db.query(StagedAction).count() == 0

    _inject_transcript(monkeypatch, ["Remind me to call the neighbour with the garden"])
    repaired = client.post(
        "/parker/practice/functional-phrase/attempts",
        json=_phrase_payload(client_attempt_id="phrase-retry"),
    )
    assert repaired.status_code == 200
    assert repaired.json()["kind"] == "confirm_offer"
    assert db.query(StagedAction).one().status == "staged"


@pytest.mark.parametrize(
    "transcript, expected_kind",
    [
        ("Double my levodopa tomorrow", "refused"),
        ("Read my bank account balance", "refused"),
        ("Order that walker with the card on file", "needs_human_approval"),
        ("Call 911 for me because I can't get up", "emergency_redirect"),
    ],
)
def test_phrase_bridge_does_not_expand_sensitive_or_external_authority(
    db,
    monkeypatch,
    transcript,
    expected_kind,
):
    _save_round()
    _inject_transcript(monkeypatch, [transcript])

    response = client.post("/parker/practice/functional-phrase/attempts", json=_phrase_payload())

    assert response.status_code == 200
    assert response.json()["kind"] == expected_kind
    assert response.json()["awaiting_confirmation"] is False
    assert db.query(CapturedIntent).count() == 0
    assert db.query(StagedAction).count() == 0


def test_phrase_rejects_multiple_utterances_invalid_audio_and_unavailable_local_asr(db, monkeypatch):
    _save_round()
    _inject_transcript(
        monkeypatch,
        ["Remind me to water the plants", "Tell Sarah I am finished"],
    )
    multiple = client.post("/parker/practice/functional-phrase/attempts", json=_phrase_payload())
    assert multiple.status_code == 422
    assert db.query(CapturedIntent).count() == 0

    bad_base64 = client.post(
        "/parker/practice/functional-phrase/attempts",
        json=_phrase_payload(client_attempt_id="phrase-bad", audio_base64="not-base64!"),
    )
    assert bad_base64.status_code == 422

    # Local ASR reports itself unavailable with RuntimeError (the documented
    # voice-deps signal from app.voice.transcribe). Simulating it with a
    # raising transcriber keeps this deterministic on machines that DO have
    # faster-whisper installed — leaving the seam as None would lazily load
    # the real model inside this unit test.
    def unavailable_transcriber(path):
        raise RuntimeError("faster-whisper is not installed (simulated)")

    monkeypatch.setattr(
        practice_router,
        "functional_phrase_transcriber",
        unavailable_transcriber,
        raising=False,
    )
    unavailable = client.post(
        "/parker/practice/functional-phrase/attempts",
        json=_phrase_payload(client_attempt_id="phrase-no-asr"),
    )
    assert unavailable.status_code == 503
    assert db.query(CapturedIntent).count() == 0


def test_phrase_page_is_voluntary_manual_keyboard_native_and_keeps_finish_path(db):
    response = client.get("/parker/practice")

    assert response.status_code == 200
    html = response.text
    for control in (
        "phrase-intro",
        "phrase-start",
        "phrase-stop",
        "phrase-live-finish",
        "phrase-retry",
        "phrase-skip",
        "phrase-confirm",
        "phrase-cancel",
        "phrase-wrong",
        "finish",
    ):
        assert f'id="{control}"' in html
    assert "Try my everyday phrase" in html
    assert "Nothing from this phrase recording is kept" in html
    assert "offerFunctionalPhrase()" in html
    assert "setTimeout(next" not in html
    assert "autoplay" not in html
    assert "aria-live=\"polite\"" in html


def test_phrase_microphone_start_is_single_owner_and_finish_errors_stay_visible(db):
    html = client.get("/parker/practice").text

    assert "let phraseStartGeneration = 0" in html
    assert "let phraseStarting = false" in html
    assert "if (phraseStarting || phraseRecorder) return" in html
    assert "const startGeneration = ++phraseStartGeneration" in html
    assert "if (startGeneration !== phraseStartGeneration || sessionClosed)" in html
    assert "phraseStartGeneration += 1" in html
    assert "async function skipFunctionalPhrase()" in html
    assert "function setFinishError" in html
    assert "$('phrase-live-finish').addEventListener('click', skipFunctionalPhrase)" in html
