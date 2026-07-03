"""Every piece of Parker state resolves through this module.

One rule for the whole engine: state lives under ``PARKER_HOME``.

Resolution order (first match wins):

1. ``PARKER_HOME`` environment variable — the desktop shell sets this for
   the bundled sidecar; tests set it for hermetic temp homes.
2. Repo checkout detected (and not running frozen/bundled): ``backend/``
   — every existing dev flow (``make run``, ``make demo``, ``make
   talk-loop``, ``make reset-db``) keeps byte-identical paths:
   ``backend/parker.db``, ``backend/digests/``.
3. The app default: ``~/Library/Application Support/Parker`` on macOS,
   ``~/.parker`` elsewhere (keeps Linux CI honest).

Nothing in here creates directories at import time; callers use the
``ensure_*`` helpers when they are about to write.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ENV_HOME = "PARKER_HOME"


def _dev_root() -> Path | None:
    """The backend/ dir when running from a repo checkout, else None.

    A PyInstaller bundle (``sys.frozen``) is never dev mode, even if it
    somehow sits next to a Makefile.
    """

    if getattr(sys, "frozen", False):
        return None
    try:
        backend = Path(__file__).resolve().parents[1]
    except (OSError, IndexError):  # pragma: no cover — defensive
        return None
    repo = backend.parent
    if (repo / "Makefile").exists() and (backend / "tests").is_dir():
        return backend
    return None


def _platform_default_home() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Parker"
    return Path.home() / ".parker"


def parker_home() -> Path:
    env = os.environ.get(ENV_HOME, "").strip()
    if env:
        return Path(env).expanduser()
    dev = _dev_root()
    if dev is not None:
        return dev
    return _platform_default_home()


def ensure_parker_home() -> Path:
    home = parker_home()
    home.mkdir(parents=True, exist_ok=True)
    return home


def config_path() -> Path:
    return parker_home() / "config.json"


def db_path() -> Path:
    return parker_home() / "parker.db"


def default_database_url() -> str:
    # Absolute sqlite URL: three slashes for the scheme + the absolute path.
    return f"sqlite:///{db_path()}"


def models_dir() -> Path:
    return parker_home() / "models"


def logs_dir() -> Path:
    return parker_home() / "logs"


def digests_dir() -> Path:
    return parker_home() / "digests"


# --- Whisper model location -------------------------------------------------
#
# faster-whisper stores weights in Hugging Face hub layout:
# <root>/models--Systran--faster-whisper-<size>/snapshots/<rev>/model.bin
# The app downloads to PARKER_HOME/models; dev machines that already have
# the weights in the default HF cache keep using them — never re-download.

_WHISPER_REPO_TEMPLATE = "models--Systran--faster-whisper-{size}"


def hf_cache_dir() -> Path:
    try:
        from huggingface_hub.constants import HF_HUB_CACHE

        return Path(HF_HUB_CACHE)
    except ImportError:  # voice deps not installed
        return Path.home() / ".cache" / "huggingface" / "hub"


def whisper_model_present(root: Path, model_size: str) -> bool:
    repo_dir = root / _WHISPER_REPO_TEMPLATE.format(size=model_size)
    snapshots = repo_dir / "snapshots"
    if not snapshots.is_dir():
        return False
    # model.bin is a symlink into blobs/; exists() follows it, so a
    # half-downloaded snapshot (dangling link) does not count as present.
    return any((snap / "model.bin").exists() for snap in snapshots.iterdir())


def whisper_model_location(model_size: str) -> str:
    """Where the weights for ``model_size`` live: parker_models | hf_cache | missing."""

    if whisper_model_present(models_dir(), model_size):
        return "parker_models"
    if whisper_model_present(hf_cache_dir(), model_size):
        return "hf_cache"
    return "missing"


def whisper_download_root(model_size: str) -> Path | None:
    """The ``download_root`` to hand faster-whisper, or None for the HF default.

    PARKER_HOME/models wins when the model is already there; an existing HF
    cache copy is used as-is (None keeps faster-whisper on its default
    cache); a genuinely missing model downloads into PARKER_HOME/models.
    """

    location = whisper_model_location(model_size)
    if location == "hf_cache":
        return None
    return models_dir()
