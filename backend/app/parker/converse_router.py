"""FastAPI routes for the Patient Curiosity Loop harness.

Deliberately outside the dashboard-auth seam, like ``/parker/screen`` and
``/parker/practice``: this is the patient-facing surface, it exposes nothing
beyond what Parker says aloud in the room, and the person it serves should
never face a login. Every side effect still flows through the same
capture → resolve → stage → confirm pipeline as every other entry point.
"""

from __future__ import annotations

import json
import queue
import threading
from typing import Any, Literal, Optional

import logging

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.parker import realtime as realtime_lane
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


@router.get("/converse", response_class=HTMLResponse, include_in_schema=False)
def converse_page() -> str:
    """The Patient Curiosity Loop page: Start, take your time, Done, Stop."""

    return CONVERSE_PAGE_HTML


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
    and the screen mirror run server-side (app/parker/realtime.py).
    """

    await websocket.accept()
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
    bridge = realtime_lane.RealtimeBridge(websocket.send_json, websocket.receive_json)
    try:
        await bridge.run()
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 — never leak internals to the patient page
        logger.exception("realtime bridge failed")
        try:
            await websocket.send_json(
                {"type": "notice", "text": "The live line dropped — tap Live conversation to reconnect."}
            )
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass


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
