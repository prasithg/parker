"""Living-room first-session contract: setup -> existing talk loop -> Dad Screen.

All audio/process/device boundaries are injected. These tests prove local behavior only;
they do not claim packaged WKWebView/TCC or home-device evidence.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import paths
from app.config import Settings, settings
from app.conversation.outcomes import (
    OUTCOME_AMBIENT_NOOP,
    OUTCOME_UNDERSTOOD_FIRST_TRY,
    InteractionOutcome,
)
from app.db.models import StagedAction
from app.demo.talk import run_talk_loop
from app.main import app
from app.parker import family_config, setup_api

client = TestClient(app)


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path))
    monkeypatch.setattr(paths, "hf_cache_dir", lambda: tmp_path / "no-hf-cache")
    return tmp_path


@pytest.fixture
def restore_settings():
    snapshot = settings.model_dump()
    yield settings
    for key, value in snapshot.items():
        setattr(settings, key, value)


@pytest.fixture(autouse=True)
def fresh_first_session_manager(monkeypatch):
    manager = setup_api.FirstSessionManager()
    monkeypatch.setattr(setup_api, "first_session_manager", manager)
    return manager


def _fake_model(root, size="base"):
    snap = root / f"models--Systran--faster-whisper-{size}" / "snapshots" / "rev1"
    snap.mkdir(parents=True)
    (snap / "model.bin").write_bytes(b"synthetic-model-marker")


def _complete_addressed_setup(home, *, mode="wake", wake_name="  Living-Room Parker!!!  "):
    _fake_model(paths.models_dir())
    response = client.post(
        "/setup/config",
        json={
            "settings": {
                "parker_address_mode": mode,
                "parker_wake_name": wake_name,
                "onboarding_completed": True,
            }
        },
    )
    assert response.status_code == 200
    return response.json()["written"]


def test_address_settings_are_strict_sanitized_persisted_and_reloadable(
    home, restore_settings, monkeypatch
):
    monkeypatch.delenv("PARKER_ADDRESS_MODE", raising=False)
    monkeypatch.delenv("PARKER_WAKE_NAME", raising=False)

    written = family_config.write_family_config(
        {
            "parker_address_mode": " WAKE ",
            "parker_wake_name": "  Living-Room Parker!!!  ",
        }
    )

    assert written == {
        "parker_address_mode": "wake",
        "parker_wake_name": "living room parker",
    }
    assert json.loads((home / "config.json").read_text())["parker_wake_name"] == (
        "living room parker"
    )
    assert settings.parker_address_mode == "wake"
    assert settings.parker_wake_name == "living room parker"

    relaunched = Settings(_env_file=None)
    assert relaunched.parker_address_mode == "wake"
    assert relaunched.parker_wake_name == "living room parker"

    with pytest.raises(family_config.ConfigWriteError, match="open or wake"):
        family_config.write_family_config({"parker_address_mode": "always"})


def test_status_distinguishes_historical_open_default_from_explicit_setup(home, restore_settings):
    initial = client.get("/setup/status").json()
    assert initial["addressing_configured"] is False
    assert initial["address_mode"] == "open"
    assert initial["wake_name"] == "parker"

    written = _complete_addressed_setup(home)
    assert written["parker_address_mode"] == "wake"
    assert written["parker_wake_name"] == "living room parker"

    configured = client.get("/setup/status").json()
    assert configured["addressing_configured"] is True
    assert configured["address_mode"] == "wake"
    assert configured["wake_name"] == "living room parker"


def test_first_session_state_never_claims_listening_before_shell_ack(
    home, restore_settings, fresh_first_session_manager
):
    _complete_addressed_setup(home, wake_name="Parker!")

    requested = client.post("/setup/first-session/start", json={}).json()
    request_id = requested["request_id"]
    assert requested["state"] == "requested"
    assert requested["listening"] is False
    assert "listening" not in requested["message"].lower()

    starting = client.post(
        "/setup/first-session/result",
        json={"request_id": request_id, "state": "starting", "message": "Starting locally."},
    ).json()
    assert starting["state"] == "starting"
    assert starting["listening"] is False

    listening = client.post(
        "/setup/first-session/result",
        json={
            "request_id": request_id,
            "state": "listening",
            "message": "Parker is listening. The Dad Screen is open.",
        },
    ).json()
    assert listening["state"] == "listening"
    assert listening["listening"] is True

    stale = client.post(
        "/setup/first-session/result",
        json={"request_id": request_id - 1, "state": "error", "message": "stale"},
    )
    assert stale.status_code == 409
    assert client.get("/setup/first-session/status").json()["state"] == "listening"


def test_cancelled_or_timed_out_request_cannot_later_claim_or_start_listening(
    home, restore_settings
):
    _complete_addressed_setup(home, wake_name="Parker!")
    requested = client.post("/setup/first-session/start", json={}).json()

    cancelled = client.post(
        "/setup/first-session/cancel", json={"request_id": requested["request_id"]}
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "error"
    assert cancelled.json()["listening"] is False

    late_shell = client.post(
        "/setup/first-session/result",
        json={
            "request_id": requested["request_id"],
            "state": "listening",
            "message": "late",
        },
    )
    assert late_shell.status_code == 409
    assert client.get("/setup/first-session/status").json()["listening"] is False


def test_cancel_during_starting_waits_for_shell_cleanup_ack(
    home, restore_settings
):
    _complete_addressed_setup(home, wake_name="Parker!")
    requested = client.post("/setup/first-session/start", json={}).json()
    request_id = requested["request_id"]
    assert client.post(
        "/setup/first-session/result",
        json={"request_id": request_id, "state": "starting", "message": "claimed"},
    ).status_code == 200

    cancelled = client.post(
        "/setup/first-session/cancel", json={"request_id": request_id}
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "cancel_requested"
    assert cancelled.json()["listening"] is False
    assert cancelled.json()["can_retry"] is False
    assert "not listening" not in cancelled.json()["message"].lower()

    repeated = client.post(
        "/setup/first-session/cancel", json={"request_id": request_id}
    )
    assert repeated.status_code == 200
    assert repeated.json()["state"] == "cancel_requested"

    late_listening = client.post(
        "/setup/first-session/result",
        json={"request_id": request_id, "state": "listening", "message": "late"},
    )
    assert late_listening.status_code == 409

    cleaned = client.post(
        "/setup/first-session/result",
        json={
            "request_id": request_id,
            "state": "error",
            "message": "Startup cancelled. Nothing is listening.",
        },
    )
    assert cleaned.status_code == 200
    assert cleaned.json()["state"] == "error"
    assert cleaned.json()["can_retry"] is True
    assert "nothing is listening" in cleaned.json()["message"].lower()


def test_pagehide_can_cancel_client_request_before_start_response(
    home, restore_settings
):
    _complete_addressed_setup(home, wake_name="Parker!")
    request_id = 9_001

    cancelled = client.post(
        "/setup/first-session/cancel", json={"request_id": request_id}
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["request_id"] == request_id
    assert cancelled.json()["state"] == "error"
    assert "nothing started" in cancelled.json()["message"].lower()

    late_start = client.post(
        "/setup/first-session/start", json={"request_id": request_id}
    )
    assert late_start.status_code == 200
    assert late_start.json()["request_id"] == request_id
    assert late_start.json()["state"] == "error"

    late_shell = client.post(
        "/setup/first-session/result",
        json={"request_id": request_id, "state": "starting", "message": "late"},
    )
    assert late_shell.status_code == 409


def test_first_session_start_fails_closed_until_setup_and_model_are_ready(
    home, restore_settings
):
    not_configured = client.post("/setup/first-session/start", json={})
    assert not_configured.status_code == 409
    assert "address" in not_configured.json()["detail"].lower()

    family_config.write_family_config(
        {
            "parker_address_mode": "wake",
            "parker_wake_name": "parker",
            "onboarding_completed": True,
        }
    )
    missing_model = client.post("/setup/first-session/start", json={})
    assert missing_model.status_code == 409
    assert "speech model" in missing_model.json()["detail"].lower()
    assert client.get("/setup/first-session/status").json()["listening"] is False


def test_setup_page_requires_explicit_address_choice_and_one_truthful_final_action():
    html = client.get("/setup/ui").text

    assert 'id="step-addressing"' in html
    assert 'name="address_mode"' in html
    assert 'value="wake"' in html
    assert 'value="open"' in html
    assert 'id="wake_name"' in html
    assert "Living room" in html
    assert "Desk / push-to-talk" in html
    assert "Start first session" in html
    assert 'id="first-session-status"' in html
    assert 'aria-live="polite"' in html
    assert "/setup/first-session/start" in html
    assert "/setup/first-session/status" in html
    assert "Check speech model" in html
    assert "Review microphone and model" in html
    # No static checked mode: an old install cannot silently inherit open mode.
    address_step = html.split('id="step-addressing"', 1)[1].split("</div>", 1)[0]
    assert "checked" not in address_step
    # Success wording is state-gated, not shown as the initial done-state claim.
    assert "Setup is saved" in html
    assert "Parker is ready" not in html


def test_setup_timeout_and_pagehide_share_cleanup_and_wait_for_shell_ack():
    html = client.get("/setup/ui").text

    assert 'state.state === "cancel_requested"' in html
    assert "requestFirstSessionCancellation" in html
    # Definition plus timeout and pagehide callers: one cleanup request path.
    assert html.count("requestFirstSessionCancellation(") == 3
    assert "nextFirstSessionRequestId" in html
    assignment = "currentFirstSessionRequest = requestId;"
    start_post = 'post("/setup/first-session/start", {request_id: requestId})'
    assert assignment in html and start_post in html
    assert html.index(assignment) < html.index(start_post)
    assert "Waiting for Parker.app to confirm cleanup" in html
    assert "Cleanup is not confirmed" in html


def test_wake_first_session_trace_is_silent_then_executes_one_local_reminder(
    db, monkeypatch
):
    monkeypatch.setattr(settings, "parker_address_mode", "wake")
    monkeypatch.setattr(settings, "parker_wake_name", "parker")
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "parker_openclaw_gateway_url", "")

    turns = iter(
        [
            ["On tonight's show you should remember to water the plants after this break"],
            ["Parker, remind me to water the plants"],
            ["yes"],
        ]
    )
    recording_paths = []
    spoken_exchanges = []

    def recorder(path, seconds):
        recording_paths.append(path)
        path.write_bytes(b"RIFF synthetic first-session audio")

    def transcriber(path):
        return next(turns)

    exchanges = run_talk_loop(
        db,
        seconds=1.0,
        recorder=recorder,
        transcriber=transcriber,
        call_sid="TEST-LIVING-ROOM-FIRST-SESSION",
        max_turns=3,
        on_exchange=spoken_exchanges.append,
    )

    assert [exchange["kind"] for exchange in exchanges] == [
        "ambient_noop",
        "captured",
        "confirm_offer",
        "executed",
    ]
    assert [exchange["kind"] for exchange in spoken_exchanges] == [
        "captured",
        "confirm_offer",
        "executed",
    ]

    actions = db.query(StagedAction).all()
    assert len(actions) == 1
    assert actions[0].action_type == "reminder"
    assert actions[0].status == "executed"
    assert actions[0].confirmed_by == "patient"

    outcomes = db.query(InteractionOutcome).all()
    assert [row.outcome for row in outcomes].count(OUTCOME_AMBIENT_NOOP) == 1
    directed = [row for row in outcomes if row.outcome != OUTCOME_AMBIENT_NOOP]
    assert len(directed) == 1
    assert directed[0].outcome == OUTCOME_UNDERSTOOD_FIRST_TRY

    assert len(recording_paths) == 3
    assert all(not path.exists() and not path.parent.exists() for path in recording_paths)


def test_talk_startup_preflight_loads_model_and_probes_mic_before_returning(monkeypatch):
    from app.demo import talk_loop

    calls = []
    fake_recorder = object()
    fake_transcriber = object()

    recorder, transcriber, hint = talk_loop.prepare_talk_dependencies(
        vad_loader=lambda: calls.append("recorder") or fake_recorder,
        fixed_loader=lambda: pytest.fail("fixed fallback should not run"),
        transcriber_loader=lambda: calls.append("model") or fake_transcriber,
        mic_probe=lambda seconds: calls.append(("mic", seconds)) or {"heard_anything": False},
        seconds=6.0,
    )

    assert recorder is fake_recorder
    assert transcriber is fake_transcriber
    assert "pause" in hint
    assert calls == ["model", ("mic", 0.2), "recorder"]


def test_talk_startup_preflight_surfaces_microphone_failure_without_recorder(monkeypatch):
    from app.demo import talk_loop

    with pytest.raises(RuntimeError, match="permission denied"):
        talk_loop.prepare_talk_dependencies(
            vad_loader=lambda: pytest.fail("recorder must not load after failed mic probe"),
            fixed_loader=lambda: pytest.fail("fallback must not load"),
            transcriber_loader=lambda: object(),
            mic_probe=lambda seconds: (_ for _ in ()).throw(RuntimeError("permission denied")),
            seconds=6.0,
        )
