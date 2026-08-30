"""The engine's setup surface for the desktop shell (and curious humans).

The shell polls ``GET /setup/status`` on first run to decide whether to
show the onboarding wizard, writes wizard answers through
``POST /setup/config`` (allowlisted keys only — secrets are refused),
and drives the whisper-model download with
``POST /setup/model/download`` + ``GET /setup/model/status``.

Everything here is localhost-only engine surface, same trust posture as
the review page: no send paths, no credentials, no medical logic.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app import paths
from app import __version__
from app.config import settings
from app.conversation.addressing import normalized_address_mode, resolved_wake_name
from app.parker import family_config
from app.voice.transcribe import DEFAULT_ASR_MODEL

logger = logging.getLogger("parker.setup")

router = APIRouter()

# Model sizes the wizard may request — the eval-chosen default plus the
# cheaper tier for slow machines (benchmark/reports/audio_real_eval_*).
DOWNLOADABLE_MODELS = ("tiny", "base", "small")


def _download_whisper_model(model_size: str) -> None:
    """Fetch the CT2 whisper weights into PARKER_HOME/models (blocking)."""

    from faster_whisper import download_model

    paths.models_dir().mkdir(parents=True, exist_ok=True)
    download_model(model_size, cache_dir=str(paths.models_dir()))


def _dir_bytes(root) -> int:
    try:
        return sum(f.stat().st_size for f in root.rglob("*") if f.is_file())
    except OSError:  # pragma: no cover — racing a download is fine
        return 0


class ModelDownloadManager:
    """One background download at a time, status readable at any moment."""

    def __init__(self, downloader: Optional[Callable[[str], None]] = None) -> None:
        self._downloader = downloader or _download_whisper_model
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._model_size = DEFAULT_ASR_MODEL
        self._error: str = ""

    def start(self, model_size: str) -> dict[str, Any]:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self.status()
            self._model_size = model_size
            self._error = ""
            self._thread = threading.Thread(
                target=self._run, args=(model_size,), name="parker-model-download", daemon=True
            )
            self._thread.start()
        return self.status()

    def _run(self, model_size: str) -> None:
        try:
            self._downloader(model_size)
        except Exception as exc:  # noqa: BLE001 — surfaced via status, never crashes the engine
            logger.warning("model download failed: %s", exc)
            self._error = str(exc)

    def status(self, model_size: Optional[str] = None) -> dict[str, Any]:
        size = model_size or self._model_size
        downloading = (
            self._thread is not None and self._thread.is_alive() and size == self._model_size
        )
        location = paths.whisper_model_location(size)
        if downloading:
            state = "downloading"
        elif location != "missing":
            state = "ready"
        elif self._error and size == self._model_size:
            state = "error"
        else:
            state = "missing"
        return {
            "model_size": size,
            "state": state,
            "location": location,
            "bytes_downloaded": _dir_bytes(paths.models_dir()) if location != "hf_cache" else 0,
            "error": self._error if state == "error" else "",
        }


download_manager = ModelDownloadManager()


class FirstSessionManager:
    """In-memory handoff between the setup page and the desktop shell.

    FastAPI never spawns the talk process. The page requests a start, the
    Tauri shell observes it, and only the shell may acknowledge observable
    process/window state. A relaunch resets this ephemeral handoff without
    touching persisted family settings.
    """

    _RESULT_STATES = {"starting", "listening", "error"}

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._request_id = 0
        self._state = "idle"
        self._message = "Setup is saved. Parker is not listening yet."

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._status_locked()

    def _status_locked(self) -> dict[str, Any]:
        return {
            "request_id": self._request_id,
            "state": self._state,
            "message": self._message,
            "listening": self._state == "listening",
            "can_retry": self._state in {"idle", "error"},
        }

    def request(self, request_id: int = 0) -> dict[str, Any]:
        with self._lock:
            if request_id:
                if request_id < self._request_id:
                    raise ValueError("stale first-session request")
                if request_id == self._request_id:
                    return self._status_locked()
            if self._state in {"requested", "starting", "cancel_requested", "listening"}:
                return self._status_locked()
            self._request_id = request_id or self._request_id + 1
            self._state = "requested"
            self._message = "Waiting for Parker.app to answer."
            return self._status_locked()

    def acknowledge(self, request_id: int, state: str, message: str) -> dict[str, Any]:
        clean_message = message.strip()[:500]
        with self._lock:
            if request_id != self._request_id or request_id <= 0:
                raise ValueError("stale or unknown first-session request")
            if state not in self._RESULT_STATES:
                raise ValueError("first-session state must be starting, listening, or error")
            allowed = {
                "starting": {"requested"},
                "listening": {"starting"},
                "error": {"requested", "starting", "cancel_requested", "listening"},
            }
            if self._state not in allowed[state]:
                raise ValueError(
                    f"cannot change first-session state from {self._state} to {state}"
                )
            self._state = state
            self._message = clean_message or {
                "starting": "Starting Parker locally.",
                "listening": "Parker is listening. The Dad Screen is open.",
                "error": "Parker did not start. Nothing is listening.",
            }[state]
            return self._status_locked()

    def cancel(self, request_id: int) -> dict[str, Any]:
        with self._lock:
            if request_id > self._request_id and self._state in {"idle", "error"}:
                self._request_id = request_id
                self._state = "error"
                self._message = "The start request was cancelled before startup. Nothing started."
                return self._status_locked()
            if request_id != self._request_id or request_id <= 0:
                raise ValueError("stale or unknown first-session request")
            if self._state == "cancel_requested":
                return self._status_locked()
            if self._state not in {"requested", "starting"}:
                raise ValueError(f"cannot cancel first-session state {self._state}")
            if self._state == "requested":
                self._state = "error"
                self._message = "The start request timed out or was cancelled. Nothing started."
            else:
                self._state = "cancel_requested"
                self._message = "Stopping startup. Waiting for Parker.app to confirm cleanup."
            return self._status_locked()


first_session_manager = FirstSessionManager()


@router.get("/ui", response_class=HTMLResponse, include_in_schema=False)
def setup_wizard_page() -> str:
    """The onboarding wizard — the desktop shell's first-run window."""

    from app.parker.setup_ui import SETUP_PAGE_HTML

    return SETUP_PAGE_HTML


@router.get("/status")
def setup_status() -> dict[str, Any]:
    """Everything the shell needs to decide 'wizard or straight to tray'."""

    config = family_config.read_family_config()
    return {
        "needs_onboarding": family_config.needs_onboarding(),
        "onboarding_completed": config.get("onboarding_completed") is True,
        "parker_home": str(paths.parker_home()),
        "config_path": str(paths.config_path()),
        "config_exists": paths.config_path().exists(),
        "patient_name": settings.patient_name,
        "addressing_configured": (
            "parker_address_mode" in config and "parker_wake_name" in config
        ),
        "address_mode": normalized_address_mode(settings.parker_address_mode),
        "wake_name": resolved_wake_name(settings.parker_wake_name),
        "version": __version__,
        "model": download_manager.status(),
    }


class ConfigUpdate(BaseModel):
    settings: dict[str, Any]


@router.post("/config")
def setup_config(update: ConfigUpdate) -> dict[str, Any]:
    """Write allowlisted family settings to config.json + the live engine."""

    try:
        written = family_config.write_family_config(update.settings)
    except family_config.ConfigWriteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"could not write config file: {exc}") from exc
    return {"written": written, "config_path": str(paths.config_path())}


@router.get("/first-session/status")
def setup_first_session_status() -> dict[str, Any]:
    """Ephemeral shell handoff state; never implies process state by itself."""

    return first_session_manager.status()


class FirstSessionStart(BaseModel):
    request_id: int = Field(default=0, ge=0, le=9_007_199_254_740_991)


@router.post("/first-session/start")
def setup_first_session_start(start: FirstSessionStart) -> dict[str, Any]:
    """Request the existing desktop talk sidecar and Dad Screen.

    The desktop shell performs and acknowledges the process/window mutation.
    """

    config = family_config.read_family_config()
    if not (
        config.get("onboarding_completed") is True
        and "parker_address_mode" in config
        and "parker_wake_name" in config
    ):
        raise HTTPException(
            status_code=409,
            detail="finish setup and explicitly choose the address mode and wake name first",
        )
    if download_manager.status()["state"] != "ready":
        raise HTTPException(
            status_code=409,
            detail="the local speech model is not ready; download it, then try again",
        )
    try:
        return first_session_manager.request(start.request_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


class FirstSessionResult(BaseModel):
    request_id: int
    state: str
    message: str = ""


class FirstSessionCancel(BaseModel):
    request_id: int = Field(gt=0, le=9_007_199_254_740_991)


@router.post("/first-session/result")
def setup_first_session_result(result: FirstSessionResult) -> dict[str, Any]:
    """Desktop-shell acknowledgement of observed process/window state."""

    try:
        return first_session_manager.acknowledge(
            result.request_id, result.state, result.message
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/first-session/cancel")
def setup_first_session_cancel(cancel: FirstSessionCancel) -> dict[str, Any]:
    """Request shell cleanup; only an unclaimed request ends immediately."""

    try:
        return first_session_manager.cancel(cancel.request_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


class MicCheckRequest(BaseModel):
    seconds: float = 1.5


@router.post("/mic-check")
def setup_mic_check(request: MicCheckRequest) -> dict[str, Any]:
    """Open the default input for a moment and report the level.

    This is the wizard's TCC moment: the first real input-stream open,
    so the macOS microphone permission prompt appears here — in context,
    next to a level meter — attributed to the app that spawned the
    engine. Frames are discarded in memory; nothing is written.
    """

    from app.voice.record import sample_mic_level

    try:
        return {"ok": True, **sample_mic_level(request.seconds)}
    except RuntimeError as exc:  # voice deps missing
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — PortAudio raises its own hierarchy
        raise HTTPException(
            status_code=503,
            detail=f"could not open the microphone: {exc}",
        ) from exc


@router.get("/tts-voices")
def setup_tts_voices() -> dict[str, Any]:
    """Installed `say` voices for the wizard's voice picker."""

    from app.voice.speak import list_say_voices

    return {"voices": list_say_voices(), "current": settings.parker_tts_voice}


class TtsPreviewRequest(BaseModel):
    voice: str = ""
    rate_wpm: int = 0
    text: str = "Hello — I'm Parker. This is how I'll sound."


@router.post("/tts-preview")
def setup_tts_preview(request: TtsPreviewRequest) -> dict[str, Any]:
    """Speak one preview line with an explicit voice (not the saved one)."""

    from app.voice.speak import speak_once

    spoke = speak_once(
        request.text[:200], voice=request.voice, rate_wpm=max(0, request.rate_wpm)
    )
    return {"spoke": spoke}


class ModelDownloadRequest(BaseModel):
    model_size: str = DEFAULT_ASR_MODEL


@router.post("/model/download")
def setup_model_download(request: ModelDownloadRequest) -> dict[str, Any]:
    if request.model_size not in DOWNLOADABLE_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"model_size must be one of {', '.join(DOWNLOADABLE_MODELS)}",
        )
    return download_manager.start(request.model_size)


@router.get("/model/status")
def setup_model_status(model_size: Optional[str] = None) -> dict[str, Any]:
    return download_manager.status(model_size)
