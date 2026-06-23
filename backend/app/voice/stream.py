"""Twilio Media Streams to OpenAI Realtime WebSocket bridge."""

from __future__ import annotations

import asyncio
import audioop
import base64
import json
import logging
from datetime import datetime
from typing import Any, Awaitable, Callable

import websockets
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from websockets.exceptions import ConnectionClosed

from app.config import settings
from app.conversation.agent import ConversationAgent
from app.conversation.prompts import get_system_prompt
from app.conversation.tools import TOOL_DEFINITIONS, execute_tool
from app.db.database import SessionLocal
from app.db.models import CallLog

logger = logging.getLogger("parker.voice.stream")

TWILIO_SAMPLE_RATE = 8_000
OPENAI_SAMPLE_RATE = 24_000
SAMPLE_WIDTH_BYTES = 2
OPENAI_REALTIME_URL = "wss://api.openai.com/v1/realtime"

JsonObject = dict[str, Any]

_agent = ConversationAgent(SessionLocal)


class StreamState:
    """Mutable state shared by the bridge relay tasks."""

    def __init__(self) -> None:
        self.stream_sid: str | None = None
        self.call_sid: str | None = None
        self.call_log_id: int | None = None
        self.call_started_at: datetime = datetime.utcnow()
        self.input_rate_state: tuple[Any, ...] | None = None
        self.output_rate_state: tuple[Any, ...] | None = None


async def twilio_media_stream(websocket: WebSocket) -> None:
    """Handle a Twilio Media Streams WebSocket and bridge it to OpenAI Realtime."""

    await websocket.accept()
    if not settings.openai_api_key:
        logger.error("OpenAI API key is not configured; closing Twilio stream")
        await websocket.close(code=1011, reason="OpenAI API key not configured")
        return

    state = StreamState()
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "OpenAI-Beta": "realtime=v1",
    }
    realtime_url = f"{OPENAI_REALTIME_URL}?model={settings.openai_realtime_model}"

    try:
        async with websockets.connect(
            realtime_url,
            additional_headers=headers,
            ping_interval=20,
            ping_timeout=20,
            max_size=8 * 1024 * 1024,
        ) as openai_ws:
            await configure_realtime_session(openai_ws.send, state)
            relay_tasks = {
                asyncio.create_task(
                    relay_twilio_to_openai(websocket, openai_ws.send, state),
                    name="twilio-to-openai",
                ),
                asyncio.create_task(
                    relay_openai_to_twilio(openai_ws, websocket, state),
                    name="openai-to-twilio",
                ),
            }
            done, pending = await asyncio.wait(relay_tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()
    except WebSocketDisconnect:
        logger.info("Twilio stream disconnected")
    except ConnectionClosed:
        logger.info("OpenAI Realtime stream closed")
    except Exception:
        logger.exception("Voice stream bridge failed")
        try:
            await websocket.close(code=1011)
        except RuntimeError:
            pass
    finally:
        await finalize_call(state)


async def configure_realtime_session(send_json_text: Callable[[str], Awaitable[None]], state: StreamState) -> None:
    """Configure the OpenAI Realtime session for low-latency phone audio."""

    del state
    payload = {
        "type": "session.update",
        "session": {
            "modalities": ["audio", "text"],
            "instructions": get_system_prompt("check_in", settings.patient_name),
            "voice": settings.openai_realtime_voice,
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "input_audio_transcription": {"model": "gpt-4o-mini-transcribe"},
            "turn_detection": {
                "type": "server_vad",
                "threshold": 0.5,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 600,
            },
            "tools": realtime_tool_definitions(TOOL_DEFINITIONS),
            "tool_choice": "auto",
        },
    }
    await send_json_text(json.dumps(payload))


async def relay_twilio_to_openai(
    twilio_ws: WebSocket,
    openai_send: Callable[[str], Awaitable[None]],
    state: StreamState,
) -> None:
    """Read Twilio events and append converted audio to OpenAI's input buffer."""

    while True:
        try:
            message = await twilio_ws.receive_text()
        except WebSocketDisconnect:
            logger.info("Twilio WebSocket disconnected while receiving")
            return

        event = parse_twilio_event(message)
        event_type = event.get("event")

        if event_type == "start":
            await handle_twilio_start(event, openai_send, state)
        elif event_type == "media":
            payload = event.get("media", {}).get("payload")
            if not payload:
                continue
            pcm24_b64, state.input_rate_state = twilio_payload_to_openai_pcm24(
                payload,
                state.input_rate_state,
            )
            await openai_send(
                json.dumps({"type": "input_audio_buffer.append", "audio": pcm24_b64})
            )
        elif event_type == "stop":
            logger.info("Twilio stop event received for stream %s", state.stream_sid)
            await openai_send(json.dumps({"type": "input_audio_buffer.commit"}))
            return
        elif event_type == "connected":
            logger.debug("Twilio media stream connected")
        else:
            logger.debug("Ignoring Twilio event: %s", event_type)


async def relay_openai_to_twilio(openai_ws: Any, twilio_ws: WebSocket, state: StreamState) -> None:
    """Read OpenAI Realtime events and send converted audio/function results onward."""

    async for raw_message in openai_ws:
        try:
            event = json.loads(raw_message)
        except json.JSONDecodeError:
            logger.warning("Malformed OpenAI Realtime message: %r", raw_message[:200])
            continue

        event_type = event.get("type")
        if event_type in {"response.audio.delta", "response.output_audio.delta"}:
            delta = event.get("delta")
            if delta and state.stream_sid:
                twilio_b64, state.output_rate_state = openai_pcm24_to_twilio_payload(
                    delta,
                    state.output_rate_state,
                )
                await twilio_ws.send_json(twilio_media_message(state.stream_sid, twilio_b64))
        elif event_type in {"response.function_call_arguments.done", "response.output_item.done"}:
            await maybe_handle_function_call(event, openai_ws.send, state)
        elif event_type == "response.audio_transcript.done":
            logger.debug("Assistant transcript: %s", event.get("transcript"))
        elif event_type == "conversation.item.input_audio_transcription.completed":
            logger.debug("User transcript: %s", event.get("transcript"))
        elif event_type == "error":
            logger.error("OpenAI Realtime error: %s", event.get("error"))
        else:
            logger.debug("OpenAI event: %s", event_type)


async def handle_twilio_start(
    event: JsonObject,
    openai_send: Callable[[str], Awaitable[None]],
    state: StreamState,
) -> None:
    """Initialize call state when Twilio sends its start event."""

    start = event.get("start", {})
    state.stream_sid = start.get("streamSid") or event.get("streamSid")
    state.call_sid = start.get("callSid") or start.get("call_sid")
    custom = start.get("customParameters") or {}
    call_type = custom.get("call_type") or custom.get("callType") or "check_in"

    db = SessionLocal()
    try:
        call = get_or_create_call(db, state.call_sid, call_type)
        state.call_log_id = call.id
        messages = _agent.start_conversation(call.id, call.call_type, settings.patient_name)
        system_prompt = messages[0].get("content") or get_system_prompt(call.call_type, settings.patient_name)
        opening = messages[-1].get("content") or f"Hi {settings.patient_name}, how are you feeling today?"
    finally:
        db.close()

    logger.info("Started Twilio media stream call_sid=%s stream_sid=%s", state.call_sid, state.stream_sid)
    await openai_send(
        json.dumps(
            {
                "type": "session.update",
                "session": {"instructions": system_prompt},
            }
        )
    )
    await openai_send(
        json.dumps(
            {
                "type": "response.create",
                "response": {
                    "modalities": ["audio", "text"],
                    "instructions": opening,
                },
            }
        )
    )


async def maybe_handle_function_call(
    event: JsonObject,
    openai_send: Callable[[str], Awaitable[None]],
    state: StreamState,
) -> None:
    """Execute OpenAI Realtime function calls with ParkinsClaw's tool handlers."""

    call = extract_function_call(event)
    if not call or state.call_log_id is None:
        return

    name = call["name"]
    call_id = call["call_id"]
    try:
        arguments = json.loads(call.get("arguments") or "{}")
    except json.JSONDecodeError:
        arguments = {}

    db = SessionLocal()
    try:
        result = execute_tool(db, state.call_log_id, name, arguments)
    finally:
        db.close()

    await openai_send(
        json.dumps(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(result),
                },
            }
        )
    )
    await openai_send(json.dumps({"type": "response.create"}))


def extract_function_call(event: JsonObject) -> dict[str, str] | None:
    """Normalize supported OpenAI Realtime function-call event shapes."""

    event_type = event.get("type")
    if event_type == "response.function_call_arguments.done":
        name = event.get("name")
        call_id = event.get("call_id") or event.get("callId")
        if name and call_id:
            return {
                "name": str(name),
                "call_id": str(call_id),
                "arguments": str(event.get("arguments") or "{}"),
            }
    if event_type == "response.output_item.done":
        item = event.get("item") or {}
        if item.get("type") == "function_call" and item.get("name") and item.get("call_id"):
            return {
                "name": str(item["name"]),
                "call_id": str(item["call_id"]),
                "arguments": str(item.get("arguments") or "{}"),
            }
    return None


async def finalize_call(state: StreamState) -> None:
    """Mark the call ended and ask ConversationAgent to summarize if possible."""

    if state.call_log_id is None:
        return
    duration = int((datetime.utcnow() - state.call_started_at).total_seconds())
    db = SessionLocal()
    try:
        call = db.get(CallLog, state.call_log_id)
        if call:
            call.ended_at = datetime.utcnow()
            call.duration_seconds = duration
            db.commit()
    finally:
        db.close()
    try:
        _agent.end_conversation(state.call_log_id)
    except Exception:
        logger.exception("Failed to summarize realtime call %s", state.call_log_id)


def get_or_create_call(db: Session, call_sid: str | None, call_type: str) -> CallLog:
    """Fetch or create a CallLog for the active stream."""

    call = db.query(CallLog).filter(CallLog.call_sid == call_sid).first() if call_sid else None
    if call:
        return call
    call = CallLog(
        call_sid=call_sid or f"STREAM{datetime.utcnow().timestamp()}",
        call_type=call_type or "check_in",
    )
    db.add(call)
    db.commit()
    db.refresh(call)
    return call


def parse_twilio_event(message: str | bytes) -> JsonObject:
    """Parse a Twilio JSON WebSocket message."""

    if isinstance(message, bytes):
        message = message.decode("utf-8")
    try:
        value = json.loads(message)
    except json.JSONDecodeError as exc:
        raise ValueError("Malformed Twilio media message") from exc
    if not isinstance(value, dict):
        raise ValueError("Twilio media message must be a JSON object")
    return value


def twilio_media_message(stream_sid: str, payload_b64: str) -> JsonObject:
    """Build a Twilio media message carrying base64 G.711 mulaw audio."""

    return {
        "event": "media",
        "streamSid": stream_sid,
        "media": {"payload": payload_b64},
    }


def twilio_payload_to_openai_pcm24(
    payload_b64: str,
    rate_state: tuple[Any, ...] | None = None,
) -> tuple[str, tuple[Any, ...] | None]:
    """Convert Twilio base64 mulaw/8kHz audio to base64 PCM16/24kHz."""

    mulaw = base64.b64decode(payload_b64)
    pcm8 = audioop.ulaw2lin(mulaw, SAMPLE_WIDTH_BYTES)
    pcm24, new_state = audioop.ratecv(
        pcm8,
        SAMPLE_WIDTH_BYTES,
        1,
        TWILIO_SAMPLE_RATE,
        OPENAI_SAMPLE_RATE,
        rate_state,
    )
    return base64.b64encode(pcm24).decode("ascii"), new_state


def openai_pcm24_to_twilio_payload(
    pcm24_b64: str,
    rate_state: tuple[Any, ...] | None = None,
) -> tuple[str, tuple[Any, ...] | None]:
    """Convert OpenAI base64 PCM16/24kHz audio to Twilio base64 mulaw/8kHz."""

    pcm24 = base64.b64decode(pcm24_b64)
    pcm8, new_state = audioop.ratecv(
        pcm24,
        SAMPLE_WIDTH_BYTES,
        1,
        OPENAI_SAMPLE_RATE,
        TWILIO_SAMPLE_RATE,
        rate_state,
    )
    mulaw = audioop.lin2ulaw(pcm8, SAMPLE_WIDTH_BYTES)
    return base64.b64encode(mulaw).decode("ascii"), new_state


def realtime_tool_definitions(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Chat Completions tool definitions to Realtime function tools."""

    normalized: list[dict[str, Any]] = []
    for tool in tools:
        function = tool.get("function", tool)
        name = function.get("name")
        if not name:
            continue
        normalized.append(
            {
                "type": "function",
                "name": name,
                "description": function.get("description", ""),
                "parameters": function.get("parameters", {"type": "object", "properties": {}}),
            }
        )
    return normalized
