"""PARKER_HOME resolution — every state path flows through app.paths.

The rules under test: an explicit PARKER_HOME always wins; a repo
checkout keeps repo-local paths (nothing existing breaks); the desktop
app default is ~/Library/Application Support/Parker; whisper weights
prefer PARKER_HOME/models but an existing HF cache copy is never
re-downloaded.
"""

from pathlib import Path

from app import paths


def test_env_var_wins_over_everything(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "custom-home"))
    assert paths.parker_home() == tmp_path / "custom-home"
    assert paths.db_path() == tmp_path / "custom-home" / "parker.db"
    assert paths.config_path().name == "config.json"


def test_env_var_expands_user(monkeypatch):
    monkeypatch.setenv(paths.ENV_HOME, "~/parker-home-test")
    assert paths.parker_home() == Path.home() / "parker-home-test"


def test_dev_checkout_defaults_to_backend_dir(monkeypatch):
    """This test suite runs from the repo checkout — the dev rule applies."""

    monkeypatch.delenv(paths.ENV_HOME, raising=False)
    home = paths.parker_home()
    assert home.name == "backend"
    assert (home / "tests").is_dir()
    assert (home.parent / "Makefile").exists()
    # The dev default keeps today's exact artifact locations.
    assert paths.db_path() == home / "parker.db"
    assert paths.digests_dir() == home / "digests"


def test_app_default_when_not_a_checkout(monkeypatch):
    monkeypatch.delenv(paths.ENV_HOME, raising=False)
    monkeypatch.setattr(paths, "_dev_root", lambda: None)
    monkeypatch.setattr(paths.sys, "platform", "darwin")
    assert paths.parker_home() == Path.home() / "Library" / "Application Support" / "Parker"


def test_app_default_on_linux(monkeypatch):
    monkeypatch.delenv(paths.ENV_HOME, raising=False)
    monkeypatch.setattr(paths, "_dev_root", lambda: None)
    monkeypatch.setattr(paths.sys, "platform", "linux")
    assert paths.parker_home() == Path.home() / ".parker"


def test_frozen_bundle_is_never_dev_mode(monkeypatch):
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    assert paths._dev_root() is None


def test_default_database_url_is_absolute(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path))
    url = paths.default_database_url()
    assert url == f"sqlite:///{tmp_path}/parker.db"
    assert Path(url.removeprefix("sqlite:///")).is_absolute()


def test_ensure_parker_home_creates_lazily(monkeypatch, tmp_path):
    target = tmp_path / "made-on-demand"
    monkeypatch.setenv(paths.ENV_HOME, str(target))
    assert not target.exists()  # import/resolution never creates directories
    assert paths.ensure_parker_home() == target
    assert target.is_dir()


# --- whisper model location ---------------------------------------------


def _fake_model(root: Path, size: str) -> None:
    snap = root / f"models--Systran--faster-whisper-{size}" / "snapshots" / "rev1"
    snap.mkdir(parents=True)
    (snap / "model.bin").write_bytes(b"weights")


def test_whisper_model_present_requires_model_bin(tmp_path):
    assert not paths.whisper_model_present(tmp_path, "base")
    _fake_model(tmp_path, "base")
    assert paths.whisper_model_present(tmp_path, "base")


def test_whisper_half_download_does_not_count(tmp_path):
    snap = tmp_path / "models--Systran--faster-whisper-base" / "snapshots" / "rev1"
    snap.mkdir(parents=True)
    (snap / "model.bin").symlink_to(snap / "missing-blob")  # dangling
    assert not paths.whisper_model_present(tmp_path, "base")


def test_location_prefers_parker_models_then_hf_cache(monkeypatch, tmp_path):
    parker_models = tmp_path / "parker-models"
    hf_cache = tmp_path / "hf-cache"
    monkeypatch.setattr(paths, "models_dir", lambda: parker_models)
    monkeypatch.setattr(paths, "hf_cache_dir", lambda: hf_cache)

    assert paths.whisper_model_location("base") == "missing"
    assert paths.whisper_download_root("base") == parker_models  # download here

    _fake_model(hf_cache, "base")
    assert paths.whisper_model_location("base") == "hf_cache"
    assert paths.whisper_download_root("base") is None  # keep the HF default

    _fake_model(parker_models, "base")
    assert paths.whisper_model_location("base") == "parker_models"
    assert paths.whisper_download_root("base") == parker_models
