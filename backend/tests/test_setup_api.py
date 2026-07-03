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
