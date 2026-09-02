"""FastAPI routes for the Patient Curiosity Loop harness.

Deliberately outside the dashboard-auth seam, like ``/parker/screen`` and
``/parker/practice``: this is the patient-facing surface, it exposes nothing
beyond what Parker says aloud in the room, and the person it serves should
never face a login. Every side effect still flows through the same
capture → resolve → stage → confirm pipeline as every other entry point.
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
from typing import Any, Literal, Optional

import logging

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.parker import realtime as realtime_lane
from app.parker.companion_power import PowerRefused, authority
from app.parker.companion_state import get_companion_settings, set_companion_settings
from app.parker.companion_ui import COMPANION_PAGE_HTML
from app.parker.converse import ConverseError, ConverseStore
from app.parker.converse_ui import CONVERSE_PAGE_HTML

logger = logging.getLogger("parker.converse")

router = APIRouter()

# One store per server process; tests swap it for one built on the test DB
# (same pattern as setup_api.first_session_manager).
converse_store = ConverseStore()


class TurnRequest(BaseModel):
    turn_id: int = Field(ge=0, le=10_000)
    # Exactly one of audio_base64 / text; the store enforces the pairing so
    # the rule lives beside the rest of the turn contract.
    audio_base64: Optional[str] = Field(default=None, min_length=1, max_length=8_000_000)
    audio_mime: Literal["audio/wav"] = "audio/wav"
    text: Optional[str] = Field(default=None, min_length=1, max_length=2_000)
    manual_finish: bool = True


class ClientReceiptRequest(BaseModel):
    turn_id: int | None = Field(default=None, ge=0, le=10_000)
    start_to_listening_ms: float | None = Field(default=None, ge=0, le=600_000)
    done_to_response_ms: float | None = Field(default=None, ge=0, le=600_000)
    response_to_first_audio_ms: float | None = Field(default=None, ge=0, le=600_000)
    done_to_first_audio_ms: float | None = Field(default=None, ge=0, le=600_000)
    stop_to_silence_ms: float | None = Field(default=None, ge=0, le=600_000)
    capture_seconds: float | None = Field(default=None, ge=0, le=600)
    outcome: str | None = Field(default=None, max_length=32)
    # Bounded semantic presence transitions from the page (what Reachy
    # showed, when, why). The store allowlists/truncates every entry; this
    # model only has to let the list through — its absence silently
    # dropped the whole beacon lane (escape found 2026-09-01).
    expression: list[dict[str, Any]] | None = Field(default=None, max_length=300)
    expression_dropped: int | None = Field(default=None, ge=0, le=1_000_000)


@router.get("/converse", response_class=HTMLResponse, include_in_schema=False)
def converse_page() -> str:
    """The companion: the virtual Reachy embodiment — power, CC, nothing else.

    Chairman direction 2026-09-01 (docs/plans/2026-09-01-companion-take2.md):
    this is a simulation of the Reachy Mini in the living room. The
    button/typing harness lives at /parker/converse/lab.
    """

    return COMPANION_PAGE_HTML


@router.get("/converse/lab", response_class=HTMLResponse, include_in_schema=False)
def converse_lab_page() -> str:
    """The developer/accessibility harness: Start, Done, Stop, typing."""

    return CONVERSE_PAGE_HTML


class CompanionSettingsRequest(BaseModel):
    power_on: bool | None = None  # refused here — power goes through /power
    cc_on: bool | None = None


class CompanionPowerRequest(BaseModel):
    on: bool
    client_id: str = Field(default="", max_length=64)


@router.get("/converse/companion/settings")
def companion_settings(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Persisted power/CC state (off must survive restarts) plus the live
    authority snapshot: who owns power right now and how many companion
    audio sockets are actually open."""

    settings = get_companion_settings(db)
    live = authority.snapshot()
    settings["gen"] = live["gen"]
    settings["owner_client"] = live["owner_client"]
    settings["live"] = live["live"]
    return settings


@router.post("/converse/companion/settings")
def update_companion_settings(
    payload: CompanionSettingsRequest, db: Session = Depends(get_db)
) -> dict[str, Any]:
    if payload.power_on is not None:
        # Power is not a setting a page may write behind the authority's
        # back — that is exactly how a stale tab kept listening.
        raise HTTPException(
            status_code=400, detail="Power goes through /converse/companion/power."
        )
    return set_companion_settings(db, cc_on=payload.cc_on)


@router.post("/converse/companion/power")
async def companion_power(
    payload: CompanionPowerRequest, db: Session = Depends(get_db)
) -> dict[str, Any]:
    """The one way power changes (docs/plans/2026-09-01-foundation-closure-overnight.md).

    ``on`` claims power for this page: the reply carries the owner token
    and generation every companion socket must present; 409 while another
    screen is actually listening; 503 when the durable write fails (and
    nothing is on). ``off`` turns Parker off for EVERY screen: the
    in-memory flip lands first, every wake/realtime socket receives a
    ``revoked`` frame and closes, then the flag persists — ``saved`` is
    false when that write failed, so the page can say so and retry.
    """

    from starlette.concurrency import run_in_threadpool

    def persist(on: bool) -> None:
        set_companion_settings(db, power_on=on)

    if payload.on:
        try:
            # The durable write runs under the authority lock; keep both off
            # the event loop so a SQLite busy wait never stalls a pump.
            granted = await run_in_threadpool(
                authority.claim, persist, client_id=payload.client_id
            )
        except PowerRefused as refused:
            raise HTTPException(
                status_code=refused.status_code,
                detail={"reason": refused.reason, "text": refused.detail},
            )
        for close in granted.pop("displaced"):
            await _revoke(close, "superseded")
        return granted
    released = await run_in_threadpool(authority.release, persist)
    for close in released.pop("revoked"):
        await _revoke(close, "power_off")
    return released


async def _revoke(close, reason: str) -> None:
    try:
        await asyncio.wait_for(close(reason), timeout=2.0)
    except Exception:  # noqa: BLE001 — a wedged socket must not block the switch
        logger.debug("revoking a companion socket failed", exc_info=True)


def _socket_credentials(websocket: WebSocket) -> tuple[str, str]:
    params = websocket.query_params
    return str(params.get("owner", ""))[:80], str(params.get("gen", ""))[:12]


async def _refuse(websocket: WebSocket, reason: str) -> None:
    text = (
        "Parker is off. Nothing is listening."
        if reason == "power_off"
        else "Parker is on another screen now."
    )
    await websocket.send_json({"type": "revoked", "reason": reason, "text": text})
    await websocket.close()


def _closer(websocket: WebSocket):
    async def close(reason: str) -> None:
        try:
            await websocket.send_json({"type": "revoked", "reason": reason})
        except Exception:  # noqa: BLE001 — already gone is fine
            pass
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass

    return close


# Presence assets for the Converse page: the expression state module, the
# Reachy renderer, and vendored Three.js. Same-origin only — the patient
# surface never fetches runtime code from a CDN. Works identically from
# the repo and the PyInstaller sidecar (the spec ships this directory as
# package data next to the frozen module).
_STATIC_ROOT = (Path(__file__).parent / "static").resolve()
_STATIC_MEDIA_TYPES = {".js": "text/javascript", ".md": "text/markdown", "": "text/plain"}


@router.get("/converse/static/{asset_path:path}", include_in_schema=False)
def converse_static(asset_path: str) -> FileResponse:
    candidate = (_STATIC_ROOT / asset_path).resolve()
    if not candidate.is_relative_to(_STATIC_ROOT) or not candidate.is_file():
        raise HTTPException(status_code=404, detail="No such asset.")
    return FileResponse(
        candidate,
        media_type=_STATIC_MEDIA_TYPES.get(candidate.suffix, "application/octet-stream"),
        headers={"Cache-Control": "no-cache"},
    )


# After a wake fires, the lane keeps transcribing for this long so the
# rest of a same-breath request ("Hey Parker, can you help me") reaches
# the live line as text while that line is still connecting.
WAKE_TAIL_SECONDS = 3.0


@router.websocket("/converse/wake")
async def converse_wake(websocket: WebSocket) -> None:
    """Local dormant wake listening: mic PCM in, one wake frame out.

    Localhost-only audio — the transcriber is the same warmed local model
    the push-button lane uses; nothing here touches the network. The page
    streams 16 kHz mono s16le frames while dormant; after a wake it keeps
    the lane open briefly for ``tail`` frames (the words after the wake
    phrase) and closes it once the live line is up
    (docs/plans/2026-09-01-wake-word.md).

    Power is checked here, not trusted from the page: the socket must
    present the owner token and generation the power claim issued, or it
    is answered with ``revoked`` and closed before any audio is read.
    """

    import base64
    import binascii

    from starlette.concurrency import run_in_threadpool

    from app.parker import wake as wake_module
    from app.parker.converse import write_receipt

    await websocket.accept()
    owner, gen = _socket_credentials(websocket)
    refusal = authority.authorize(owner, gen)
    if refusal is not None:
        await _refuse(websocket, refusal)
        return
    transcriber = converse_store.transcriber()
    if transcriber is None:
        await websocket.send_json(
            {
                "type": "unavailable",
                "text": (
                    "Wake listening needs the local voice model "
                    "(make voice-deps)."
                ),
            }
        )
        await websocket.close()
        return
    from app.config import settings as app_settings

    detector = wake_module.WakeDetector(
        transcriber, relative_gate=app_settings.parker_wake_relative_gate
    )
    opened = time.monotonic()
    woke_at: float | None = None
    sid, _superseded = authority.register(token=owner, kind="wake", close=_closer(websocket))
    if sid is None:
        # Power moved while the model warmed: off, or a new owner.
        await _refuse(websocket, authority.authorize(owner, gen) or "not_owner")
        return
    try:
        while True:
            message = await websocket.receive_json()
            if not isinstance(message, dict):
                continue
            kind = message.get("type")
            if kind == "audio":
                try:
                    pcm = base64.b64decode(
                        str(message.get("data", "")).encode("ascii"), validate=True
                    )
                except (ValueError, binascii.Error, UnicodeEncodeError):
                    continue  # junk frames never end dormancy
                if woke_at is not None:
                    # The tail lane: what he says right after the wake.
                    if time.monotonic() - woke_at > WAKE_TAIL_SECONDS:
                        continue
                    heard = await run_in_threadpool(detector.hear, pcm)
                    if heard and heard["heard"]:
                        try:
                            await websocket.send_json(
                                {"type": "tail", "text": heard["heard"][:200]}
                            )
                        except RuntimeError:
                            return  # revoked mid-tail
                    continue
                hit = await run_in_threadpool(detector.feed, pcm)
                if hit:
                    woke_at = time.monotonic()
                    logger.info(
                        "wake detected matched=%r infer_ms=%d rms=%d",
                        hit["matched"],
                        hit["infer_ms"],
                        hit["rms"],
                    )
                    try:
                        write_receipt(
                            {
                                "recorded_by": "server",
                                "kind": "wake",
                                "matched": hit["matched"],
                                "infer_ms": hit["infer_ms"],
                                "rms": hit["rms"],
                                "dormant_s": int(time.monotonic() - opened),
                            }
                        )
                    except Exception:  # noqa: BLE001 — receipts never break waking
                        pass
                    try:
                        await websocket.send_json({"type": "wake", **hit})
                    except RuntimeError:
                        return  # revoked mid-inference: the socket is already closed
            elif kind == "end":
                return
    except WebSocketDisconnect:
        pass
    finally:
        authority.unregister(sid)


@router.post("/converse/sessions")
def create_converse_session() -> dict[str, Any]:
    """Start one conversation session and warm the shared local model."""

    created = converse_store.create_session()
    created["realtime_available"] = realtime_lane.realtime_available()
    return created


@router.websocket("/converse/realtime")
async def converse_realtime(websocket: WebSocket) -> None:
    """The live full-duplex lane: browser <-> Parker policy <-> gpt-realtime.

    Parker stays the boundary in the middle — guards, the action pipeline,
    and the screen mirror run server-side (app/parker/realtime.py). Power
    is enforced here too: only the page that owns the current power
    generation may open the line, and power-off revokes it mid-call.
    """

    await websocket.accept()
    owner, gen = _socket_credentials(websocket)
    refusal = authority.authorize(owner, gen)
    if refusal is not None:
        await _refuse(websocket, refusal)
        return
    if not realtime_lane.realtime_available():
        await websocket.send_json(
            {
                "type": "unavailable",
                "text": (
                    "Live conversation needs the family to add an OpenAI key "
                    "first. The patient loop still works."
                ),
            }
        )
        await websocket.close()
        return
    if not realtime_lane.try_acquire_bridge_slot():
        # Each bridge holds a billed upstream socket; this is one household.
        await websocket.send_json(
            {"type": "unavailable", "text": "A live conversation is already running."}
        )
        await websocket.close()
        return
    sid, superseded = authority.register(
        token=owner, kind="realtime", close=_closer(websocket)
    )
    if sid is None:
        realtime_lane.release_bridge_slot()
        await _refuse(websocket, authority.authorize(owner, gen) or "not_owner")
        return
    bridge = realtime_lane.RealtimeBridge(websocket.send_json, websocket.receive_json)
    try:
        for close in superseded:
            # One owner, one line: the page's own reconnect replaces its
            # dead socket; the fenced page ignores frames from the old one.
            await _revoke(close, "superseded")
        await bridge.run()
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 — never leak internals to the patient page
        logger.exception("realtime bridge failed")
        try:
            await websocket.send_json(
                {"type": "notice", "text": "The live line dropped — say \u201cHey Parker\u201d to try again."}
            )
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass
    finally:
        authority.unregister(sid)
        realtime_lane.release_bridge_slot()


@router.post("/converse/sessions/{session_id}/turns")
def run_converse_turn(session_id: str, payload: TurnRequest) -> dict[str, Any]:
    """One turn: audio (base64 WAV) or text in, routed reply + timings out."""

    try:
        return converse_store.run_turn(
            session_id,
            turn_id=payload.turn_id,
            audio_base64=payload.audio_base64,
            text=payload.text,
        )
    except ConverseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.post("/converse/sessions/{session_id}/turns/stream")
def run_converse_turn_stream(session_id: str, payload: TurnRequest) -> StreamingResponse:
    """One turn as newline-delimited JSON events.

    ``{"event": "heard", ...}`` once the transcript exists, then
    ``{"event": "speech", "text": sentence}`` per guarded sentence as the
    answer generates (TTS can start after the first one), then
    ``{"event": "final", ...}`` with the complete turn result — or
    ``{"event": "error", "status", "detail"}``. The page speaks streamed
    sentences the moment they arrive; a stopped turn simply ends.
    """

    events: "queue.Queue[dict[str, Any] | None]" = queue.Queue()

    def work() -> None:
        try:
            result = converse_store.run_turn(
                session_id,
                turn_id=payload.turn_id,
                audio_base64=payload.audio_base64,
                text=payload.text,
                emit=events.put,
            )
            events.put({"event": "final", **result})
        except ConverseError as exc:
            events.put({"event": "error", "status": exc.status_code, "detail": exc.detail})
        except Exception:  # noqa: BLE001 — never leak internals to the patient page
            events.put({"event": "error", "status": 500, "detail": "Parker hit a snag."})
        finally:
            events.put(None)

    threading.Thread(target=work, daemon=True).start()

    def generate():
        while True:
            item = events.get()
            if item is None:
                break
            yield json.dumps(item) + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@router.post("/converse/sessions/{session_id}/stop")
def stop_converse(session_id: str) -> dict[str, Any]:
    """Immediate stop: invalidate the generation; late results are discarded."""

    try:
        return converse_store.stop(session_id)
    except ConverseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.post("/converse/sessions/{session_id}/end")
def end_converse(session_id: str) -> dict[str, Any]:
    """Explicit clean exit; sessions also expire on their own."""

    try:
        return converse_store.end_session(session_id)
    except ConverseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.get("/converse/sessions/{session_id}/state")
def converse_state(session_id: str) -> dict[str, Any]:
    """Current session state for reconnects; mirrors the last turn only."""

    try:
        return converse_store.state(session_id)
    except ConverseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.post("/converse/sessions/{session_id}/receipts")
def record_converse_receipt(session_id: str, payload: ClientReceiptRequest) -> dict[str, Any]:
    """Client-measured latency marks; aggregate-only, stays on this machine."""

    try:
        return converse_store.record_client_receipt(
            session_id, payload.model_dump(exclude_none=True)
        )
    except ConverseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
