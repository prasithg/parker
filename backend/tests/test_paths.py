"""PARKER_HOME resolution — every state path flows through app.paths.

The rules under test: an explicit PARKER_HOME always wins; a repo
checkout keeps repo-local paths (nothing existing breaks); the desktop
app default is ~/Library/Application Support/Parker; whisper weights
prefer PARKER_HOME/models but an existing HF cache copy is never
re-downloaded.
"""

from pathlib import Path

import pytest

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


# --- offline cached snapshot selection ----------------------------------
#
# faster-whisper with a bare model name asks the model hub for metadata on
# process start, even when the weights are cached. A *complete* cached
# snapshot (weights + config + tokenizer + vocabulary, under the revision
# that refs/main names) can be handed to it as a directory instead; anything
# less keeps the normal download/repair path so a half install never turns
# into a broken offline load.

_REQUIRED = ("model.bin", "config.json", "tokenizer.json")


def _snapshot(root: Path, size: str, revision: str = "rev1", *, files=_REQUIRED + ("vocabulary.json",), ref=None) -> Path:
    repo = root / f"models--Systran--faster-whisper-{size}"
    snap = repo / "snapshots" / revision
    snap.mkdir(parents=True, exist_ok=True)
    for name in files:
        (snap / name).write_text("synthetic")
    (repo / "refs").mkdir(exist_ok=True)
    (repo / "refs" / "main").write_text(revision if ref is None else ref)
    return snap


@pytest.fixture
def caches(monkeypatch, tmp_path):
    parker_models = tmp_path / "parker-models"
    hf_cache = tmp_path / "hf-cache"
    monkeypatch.setattr(paths, "models_dir", lambda: parker_models)
    monkeypatch.setattr(paths, "hf_cache_dir", lambda: hf_cache)
    return parker_models, hf_cache


def test_cached_snapshot_none_when_nothing_is_installed(caches):
    assert paths.cached_whisper_snapshot("base") is None


@pytest.mark.parametrize("vocabulary", ["vocabulary.json", "vocabulary.txt"])
def test_cached_snapshot_accepts_either_vocabulary_format(caches, vocabulary):
    # Systran's faster-whisper-base ships vocabulary.txt, other sizes ship
    # vocabulary.json; both are the complete tokenizer vocabulary.
    parker_models, _ = caches
    snap = _snapshot(parker_models, "base", files=_REQUIRED + (vocabulary,))
    assert paths.cached_whisper_snapshot("base") == snap


@pytest.mark.parametrize("missing", ["model.bin", "config.json", "tokenizer.json", "vocabulary"])
def test_cached_snapshot_requires_every_file(caches, missing):
    parker_models, _ = caches
    files = tuple(name for name in _REQUIRED + ("vocabulary.json",) if not name.startswith(missing))
    _snapshot(parker_models, "base", files=files)
    assert paths.cached_whisper_snapshot("base") is None


@pytest.mark.parametrize("empty", _REQUIRED + ("vocabulary.json",))
def test_cached_snapshot_empty_files_are_incomplete(caches, empty):
    parker_models, hf_cache = caches
    broken = _snapshot(parker_models, "base")
    (broken / empty).write_bytes(b"")
    assert paths.cached_whisper_snapshot("base") is None
    complete = _snapshot(hf_cache, "base")
    assert paths.cached_whisper_snapshot("base") == complete


def test_cached_snapshot_dangling_symlink_is_incomplete(caches):
    # HF layout: snapshot files are symlinks into blobs/; a half download
    # leaves a dangling model.bin link that must not count as complete.
    parker_models, _ = caches
    snap = _snapshot(parker_models, "base")
    (snap / "model.bin").unlink()
    (snap / "model.bin").symlink_to(snap / "missing-blob")
    assert paths.cached_whisper_snapshot("base") is None


@pytest.mark.parametrize("ref", ["", "   \n", ".", "..", "rev1/", "../snapshots/rev1", "snapshots/rev1", "/rev1"])
def test_cached_snapshot_rejects_invalid_refs(caches, ref):
    # The complete snapshot exists at rev1, and several of these refs would
    # even resolve to it through path tricks — none may be honoured.
    parker_models, _ = caches
    _snapshot(parker_models, "base", ref=ref)
    assert paths.cached_whisper_snapshot("base") is None


def test_cached_snapshot_without_refs_main_is_not_selected(caches):
    parker_models, _ = caches
    snap = _snapshot(parker_models, "base")
    (snap.parent.parent / "refs" / "main").unlink()
    assert paths.cached_whisper_snapshot("base") is None


def test_cached_snapshot_never_picks_an_unreferenced_revision(caches):
    # refs/main names a revision that is not on disk while another complete
    # snapshot is: the default revision is the contract, not "whatever is there".
    parker_models, _ = caches
    _snapshot(parker_models, "base", "rev1", ref="rev2")
    assert paths.cached_whisper_snapshot("base") is None


def test_partial_parker_cache_does_not_hide_a_complete_hf_cache(caches):
    parker_models, hf_cache = caches
    _snapshot(parker_models, "base", files=("model.bin",))
    complete = _snapshot(hf_cache, "base")
    assert paths.cached_whisper_snapshot("base") == complete
    # Existing download-root semantics stay untouched: model.bin in
    # PARKER_HOME/models still makes that the repair/download root.
    assert paths.whisper_model_location("base") == "parker_models"
    assert paths.whisper_download_root("base") == parker_models


def test_invalid_parker_ref_does_not_hide_a_complete_hf_cache(caches):
    parker_models, hf_cache = caches
    _snapshot(parker_models, "base", ref="../snapshots/rev1")
    complete = _snapshot(hf_cache, "base")
    assert paths.cached_whisper_snapshot("base") == complete


def test_complete_parker_cache_wins_over_complete_hf_cache(caches):
    parker_models, hf_cache = caches
    parker = _snapshot(parker_models, "base", "parker-rev")
    _snapshot(hf_cache, "base", "hf-rev")
    assert paths.cached_whisper_snapshot("base") == parker


def test_cached_snapshot_is_per_model_size(caches):
    parker_models, _ = caches
    _snapshot(parker_models, "base")
    assert paths.cached_whisper_snapshot("small") is None
