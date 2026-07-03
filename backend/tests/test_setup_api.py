"""The /setup surface the desktop shell drives.

Status tells the shell 'wizard or tray'; config writes are allowlisted
(secrets 400); the model download runs in the background with pollable
status. Downloads are faked — no network, no voice deps.
"""

import json
import time

import pytest
from fastapi.testclient import TestClient

from app import paths
from app.config import settings
from app.main import app
from app.parker import setup_api
from app.parker.setup_api import ModelDownloadManager

client = TestClient(app)


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path))
    return tmp_path


@pytest.fixture
def restore_settings():
    snapshot = settings.model_dump()
    yield settings
    for key, value in snapshot.items():
        setattr(settings, key, value)


def _fake_model(root, size):
    snap = root / f"models--Systran--faster-whisper-{size}" / "snapshots" / "rev1"
    snap.mkdir(parents=True)
    (snap / "model.bin").write_bytes(b"weights" * 10)


def test_status_on_a_fresh_home(home, monkeypatch):
    monkeypatch.setattr(paths, "hf_cache_dir", lambda: home / "no-hf-cache")
    response = client.get("/setup/status")
    assert response.status_code == 200
    body = response.json()
    assert body["needs_onboarding"] is True
    assert body["onboarding_completed"] is False
    assert body["config_exists"] is False
    assert body["parker_home"] == str(home)
    assert body["model"]["state"] == "missing"
    assert body["version"]


def test_config_write_flips_status_and_live_settings(home, restore_settings):
    response = client.post(
        "/setup/config",
        json={"settings": {"patient_name": "Ravi", "onboarding_completed": True}},
    )
    assert response.status_code == 200
    assert response.json()["written"]["patient_name"] == "Ravi"

    assert settings.patient_name == "Ravi"
    status = client.get("/setup/status").json()
    assert status["needs_onboarding"] is False
    assert status["onboarding_completed"] is True
    assert status["config_exists"] is True


def test_config_write_refuses_secrets(home):
    response = client.post(
        "/setup/config", json={"settings": {"anthropic_api_key": "sk-nope"}}
    )
    assert response.status_code == 400
    assert "secrets" in response.json()["detail"]
    assert not (home / "config.json").exists()


def test_config_write_refuses_unknown_keys(home):
    response = client.post(
        "/setup/config", json={"settings": {"database_url": "sqlite:///x.db"}}
    )
    assert response.status_code == 400
    assert "unknown" in response.json()["detail"]


def test_model_download_rejects_unknown_size(home):
    response = client.post("/setup/model/download", json={"model_size": "enormous"})
    assert response.status_code == 400


def test_model_download_manager_success_path(home, monkeypatch):
    monkeypatch.setattr(paths, "hf_cache_dir", lambda: home / "no-hf-cache")
    started = []

    def fake_download(size):
        started.append(size)
        _fake_model(paths.models_dir(), size)

    manager = ModelDownloadManager(downloader=fake_download)
    assert manager.status()["state"] == "missing"

    manager.start("base")
    manager._thread.join(timeout=5)
    status = manager.status()
    assert started == ["base"]
    assert status["state"] == "ready"
    assert status["location"] == "parker_models"
    assert status["bytes_downloaded"] > 0


def test_model_download_manager_error_path(home, monkeypatch):
    monkeypatch.setattr(paths, "hf_cache_dir", lambda: home / "no-hf-cache")

    def broken_download(size):
        raise RuntimeError("network is a lie")

    manager = ModelDownloadManager(downloader=broken_download)
    manager.start("base")
    manager._thread.join(timeout=5)
    status = manager.status()
    assert status["state"] == "error"
    assert "network is a lie" in status["error"]


def test_model_download_manager_is_idempotent_while_running(home, monkeypatch):
    monkeypatch.setattr(paths, "hf_cache_dir", lambda: home / "no-hf-cache")
    release = []

    def slow_download(size):
        while not release:
            time.sleep(0.01)

    manager = ModelDownloadManager(downloader=slow_download)
    first = manager.start("base")
    second = manager.start("base")  # no second thread while one runs
    assert first["state"] == "downloading"
    assert second["state"] == "downloading"
    thread = manager._thread
    release.append(True)
    thread.join(timeout=5)


def test_model_endpoints_use_the_shared_manager(home, monkeypatch):
    monkeypatch.setattr(paths, "hf_cache_dir", lambda: home / "no-hf-cache")

    def fake_download(size):
        _fake_model(paths.models_dir(), size)

    monkeypatch.setattr(setup_api, "download_manager", ModelDownloadManager(fake_download))
    assert client.get("/setup/model/status").json()["state"] == "missing"

    response = client.post("/setup/model/download", json={"model_size": "base"})
    assert response.status_code == 200
    setup_api.download_manager._thread.join(timeout=5)
    assert client.get("/setup/model/status").json()["state"] == "ready"


def test_ready_when_model_already_in_hf_cache(home, monkeypatch):
    hf = home / "hf-cache"
    monkeypatch.setattr(paths, "hf_cache_dir", lambda: hf)
    _fake_model(hf, "base")
    status = client.get("/setup/model/status").json()
    assert status["state"] == "ready"
    assert status["location"] == "hf_cache"
    assert status["bytes_downloaded"] == 0  # nothing of ours on disk


# --- wizard hardware endpoints (mic, voices, preview) ---------------------


def test_mic_check_reports_levels_without_persisting_audio(monkeypatch):
    import struct

    from app.voice import record

    frames = struct.pack("<4h", 0, 1000, -1000, 500)
    monkeypatch.setattr(
        record,
        "sample_mic_level",
        lambda seconds=1.5: {
            "seconds": seconds,
            **dict(zip(("rms", "peak"), record._int16_rms_peak(frames))),
            "device": "Fake Mic",
            "heard_anything": True,
        },
    )
    body = client.post("/setup/mic-check", json={"seconds": 1.0}).json()
    assert body["ok"] is True
    assert body["device"] == "Fake Mic"
    assert body["peak"] == 1000
    assert body["heard_anything"] is True


def test_mic_check_maps_missing_deps_to_503(monkeypatch):
    from app.voice import record

    def no_deps(seconds=1.5):
        raise RuntimeError("sounddevice is not installed")

    monkeypatch.setattr(record, "sample_mic_level", no_deps)
    response = client.post("/setup/mic-check", json={})
    assert response.status_code == 503
    assert "sounddevice" in response.json()["detail"]


def test_int16_rms_peak_pure_python():
    import struct

    from app.voice.record import _int16_rms_peak

    rms, peak = _int16_rms_peak(struct.pack("<3h", 0, 3, -4))
    assert peak == 4
    assert round(rms, 3) == round((25 / 3) ** 0.5, 3)
    assert _int16_rms_peak(b"") == (0.0, 0)


def test_tts_voices_endpoint(monkeypatch):
    from app.voice import speak

    monkeypatch.setattr(
        speak,
        "list_say_voices",
        lambda: [{"name": "Samantha", "lang": "en_US", "sample": "Hello!"}],
    )
    body = client.get("/setup/tts-voices").json()
    assert body["voices"][0]["name"] == "Samantha"
    assert "current" in body


def test_tts_preview_speaks_with_explicit_voice(monkeypatch):
    from app.voice import speak

    spoken = {}

    def fake_speak_once(text, *, voice="", rate_wpm=0):
        spoken.update({"text": text, "voice": voice, "rate_wpm": rate_wpm})
        return True

    monkeypatch.setattr(speak, "speak_once", fake_speak_once)
    body = client.post(
        "/setup/tts-preview",
        json={"voice": "Samantha", "rate_wpm": 160, "text": "x" * 500},
    ).json()
    assert body["spoke"] is True
    assert spoken["voice"] == "Samantha"
    assert len(spoken["text"]) == 200  # capped


def test_say_voice_line_parsing():
    from app.voice.speak import list_say_voices

    # Structure-only check that the parser is wired; real output depends
    # on the machine. On macOS this returns a list of dicts.
    voices = list_say_voices()
    assert isinstance(voices, list)
    for voice in voices[:3]:
        assert set(voice) == {"name", "lang", "sample"}


# --- the onboarding wizard page --------------------------------------------


def test_setup_wizard_page_serves_the_full_flow():
    page = client.get("/setup/ui")
    assert page.status_code == 200
    html = page.text
    # Every wizard step the mission defines, in one self-contained page.
    for step in [
        "step-welcome", "step-name", "step-contacts", "step-lexicon",
        "step-voice", "step-consent", "step-mic", "step-model", "step-done",
    ]:
        assert step in html
    # It drives the engine's own setup endpoints, same-origin.
    for endpoint in [
        "/setup/config", "/setup/mic-check", "/setup/tts-voices",
        "/setup/tts-preview", "/setup/model/download", "/setup/model/status",
    ]:
        assert endpoint in html


def test_setup_wizard_page_is_self_contained_and_honest():
    html = client.get("/setup/ui").text
    # No external resources: nothing fetched from the network to render.
    assert "https://" not in html
    assert "http://" not in html
    assert "<link" not in html.lower()
    # Plain-language privacy posture, verbatim commitments.
    assert "no send path" in html
    assert "deleted the moment" in html  # audio never kept
    # The learning flywheel is opt-IN: checkbox present, never pre-checked.
    assert 'id="repair_consent"' in html
    assert "checked" not in html.split('id="repair_consent"')[1].split(">")[0]
    # Medical boundary stated up front.
    assert "never gives medical advice" in html
    # Pilot recording protocol pointer for the family.
    assert "pilot-recording-protocol" in html


def test_setup_wizard_writes_config_the_engine_accepts(home, restore_settings):
    """The exact settings payload the wizard sends must be writable."""

    wizard_payload = {
        "settings": {
            "patient_name": "Dad",
            "parker_family_contacts": "Sarah, Michael",
            "personal_lexicon": "physio, bridge night",
            "parker_tts_voice": "Samantha",
            "repair_event_capture_consented": False,
            "onboarding_completed": True,
        }
    }
    response = client.post("/setup/config", json=wizard_payload)
    assert response.status_code == 200
    assert client.get("/setup/status").json()["needs_onboarding"] is False
