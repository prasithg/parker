"""The realtime full-duplex lane: gpt-realtime as a Parker brain.

The browser holds one websocket to Parker; Parker holds one websocket to
the OpenAI Realtime API and stays the policy boundary in between — the
server-relay topology means guards, transcripts, the action pipeline, and
the screen mirror all run here, never in the page.

What Parker keeps in this lane (docs/brain-adapters.md):

- **Speech is the model's; actions are Parker's.** The only tool the
  realtime session gets is ``propose_action``. A call lands here, is
  validated against the same proposal guard, captured and staged through
  the same pipeline — and the model is told to say it's waiting for
  confirmation on screen. Nothing executes from this lane in v0.
- **The post-hoc guard.** The model hears audio directly (that is the
  point of the lane — and the family's explicit data choice, 2026-08-30),
  so the deterministic guards cannot run pre-model here. Instead the
  assistant's own transcript is screened as it streams: a medical-boundary
  violation cancels the response mid-word, flushes the browser's playback,
  and speaks the standard redirect.
- **The screen mirror and outcome trail stay on.** User transcripts and
  spoken replies land on the live Dad screen row like every other lane.
- **Stop and barge-in are immediate.** Browser Stop cancels the response
  and flushes playback; the server VAD's ``speech_started`` flushes
  playback so Dad can talk over Parker naturally.

No key configured → the endpoint answers with an honest policy message
and closes. Tests drive the bridge with a fake upstream; nothing here
touches the network in the suite.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
from typing import Any, Awaitable, Callable, Optional

from starlette.concurrency import run_in_threadpool

from app.brain.claude import PROPOSE_ACTION_TOOL, _system_prompt
from app.brain.guard import MEDICAL_BOUNDARY_REDIRECT, speech_violates_medical_boundary

logger = logging.getLogger("parker.realtime")

OPENAI_REALTIME_URL = "wss://api.openai.com/v1/realtime?model={model}"

_REALTIME_ADDENDUM = """

You are in live spoken conversation. Keep replies to one or two short
sentences unless asked for more; it is fine to be interrupted — stop and
listen. When you call propose_action, tell {patient_name} the action is
written on the screen waiting for their confirmation — never that it is
done. If anything sounds urgent, say to call emergency services or get a
family member right away."""


def realtime_available() -> bool:
    from app.config import settings

    return bool(settings.openai_api_key) and settings.parker_realtime_enabled


def build_session_update() -> dict[str, Any]:
    """The session.update Parker sends on connect (GA realtime shape)."""

    from app.brain.claude import build_brain_context
    from app.config import settings

    context = build_brain_context()
    instructions = _system_prompt(context) + _REALTIME_ADDENDUM.format(
        patient_name=context.patient_name
    )
    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "model": settings.openai_realtime_model,
            "output_modalities": ["audio"],
            "instructions": instructions,
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    # Semantic VAD reads whether a thought is finished, not
                    # just silence — the patient-speech end-pointing the
                    # energy VAD never managed. Low eagerness waits longest.
                    "turn_detection": {"type": "semantic_vad", "eagerness": "low"},
                    "transcription": {"model": "gpt-4o-mini-transcribe"},
                },
                "output": {
                    "format": {"type": "audio/pcm"},
                    "voice": settings.openai_realtime_voice,
                },
            },
            "tools": [
                {
                    "type": "function",
                    "name": PROPOSE_ACTION_TOOL["name"],
                    "description": PROPOSE_ACTION_TOOL["description"],
                    "parameters": PROPOSE_ACTION_TOOL["input_schema"],
                }
            ],
            "tool_choice": "auto",
        },
    }


async def connect_openai() -> Any:
    """Open the upstream websocket. Injectable for tests."""

    import websockets

    from app.config import settings

    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        # Stable per-household pseudonymous id (never the raw key/user data).
        "OpenAI-Safety-Identifier": hashlib.sha256(
            f"parker:{settings.patient_name}".encode("utf-8")
        ).hexdigest()[:32],
    }
    return await websockets.connect(
        OPENAI_REALTIME_URL.format(model=settings.openai_realtime_model),
        additional_headers=headers,
        max_size=1 << 24,
    )


# Test seam: the suite points this at an in-memory engine so bridge side
# effects never touch the real local database. None -> app SessionLocal.
_db_session_factory: Optional[Callable[[], Any]] = None


def _make_db() -> Any:
    if _db_session_factory is not None:
        return _db_session_factory()
    from app.db.database import SessionLocal, create_tables

    create_tables()
    return SessionLocal()


def _record_exchange_sync(heard: str, speech: str) -> None:
    """Mirror one realtime exchange to the live screen row (best-effort)."""

    from app.parker.screen import publish_screen_state

    try:
        db = _make_db()
        try:
            publish_screen_state(
                db, heard=heard, speech=speech, kind="answer", choices=None, awaiting=""
            )
        finally:
            db.close()
    except Exception:  # noqa: BLE001 — the mirror must never break the call
        logger.debug("realtime screen mirror skipped", exc_info=True)


def _stage_proposal_sync(arguments: dict[str, Any]) -> dict[str, Any]:
    """Capture + stage one proposed action through the normal pipeline."""

    from app.brain.adapter import PROPOSABLE_ACTION_TYPES
    from app.conversation.tools import execute_tool
    from app.db.models import CallLog
    from app.parker.pipeline import resolve_captured_intents, stage_resolved_actions

    action_type = str(arguments.get("action_type", ""))
    if action_type not in PROPOSABLE_ACTION_TYPES:
        return {"status": "rejected", "detail": "That action type is not allowed."}
    subject = str(arguments.get("subject", "")).strip()
    intent_text = str(arguments.get("intent_text", "")).strip()
    if not subject or not intent_text:
        return {"status": "rejected", "detail": "The proposal was incomplete."}

    requested = {"reminder": "remind", "family_message": "message", "exercise_start": "exercise"}.get(
        action_type, action_type
    )
    db = _make_db()
    try:
        call = db.query(CallLog).filter(CallLog.call_sid == "REALTIME-LIVE").first()
        if call is None:
            call = CallLog(call_sid="REALTIME-LIVE", call_type="realtime")
            db.add(call)
            db.commit()
            db.refresh(call)
        payload: dict[str, Any] = {
            "intent_text": intent_text,
            "requested_action": requested,
            "subject": subject,
        }
        recipient = arguments.get("recipient")
        if recipient:
            payload["recipient"] = str(recipient)
        result = execute_tool(db, call.id, "capture_intent", payload)
        if result.get("status") != "captured":
            return {"status": "rejected", "detail": "Parker could not save that."}
        resolve_captured_intents(db, call_log_id=call.id)
        stage_resolved_actions(db, call_log_id=call.id)
        return {
            "status": "staged",
            "detail": (
                "Staged and shown on the screen for confirmation. Nothing runs "
                "until it is confirmed there."
            ),
        }
    finally:
        db.close()


class RealtimeBridge:
    """One live conversation: browser ws <-> Parker policy <-> OpenAI ws."""

    def __init__(
        self,
        browser_send: Callable[[dict[str, Any]], Awaitable[None]],
        browser_receive: Callable[[], Awaitable[dict[str, Any]]],
        *,
        upstream_connect: Optional[Callable[[], Awaitable[Any]]] = None,
    ) -> None:
        self._browser_send = browser_send
        self._browser_receive = browser_receive
        # Resolved at run time through the module so tests can monkeypatch
        # connect_openai without touching every construction site.
        self._upstream_connect = upstream_connect
        self._upstream: Any = None
        # Per-response transcript accumulation for the post-hoc guard.
        self._assistant_transcript = ""
        self._guard_tripped = False
        self._user_transcript = ""

    async def run(self) -> None:
        connect = self._upstream_connect or globals()["connect_openai"]
        self._upstream = await connect()
        try:
            await self._upstream.send(json.dumps(build_session_update()))
            browser_task = asyncio.create_task(self._pump_browser())
            upstream_task = asyncio.create_task(self._pump_upstream())
            done, pending = await asyncio.wait(
                {browser_task, upstream_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            for task in done:
                exc = task.exception()
                if exc is not None and not isinstance(exc, asyncio.CancelledError):
                    raise exc
        finally:
            close = getattr(self._upstream, "close", None)
            if close is not None:
                try:
                    await close()
                except Exception:  # noqa: BLE001
                    pass

    # ------------------------------------------------------------------
    # Browser -> upstream
    # ------------------------------------------------------------------

    async def _pump_browser(self) -> None:
        while True:
            message = await self._browser_receive()
            kind = message.get("type")
            if kind == "audio":
                encoded = str(message.get("data", ""))
                try:
                    base64.b64decode(encoded.encode("ascii"), validate=True)
                except (ValueError, binascii.Error, UnicodeEncodeError):
                    continue  # never forward junk upstream
                await self._upstream.send(
                    json.dumps({"type": "input_audio_buffer.append", "audio": encoded})
                )
            elif kind == "stop":
                await self._upstream.send(json.dumps({"type": "response.cancel"}))
                await self._browser_send({"type": "clear"})
            elif kind == "end":
                return

    # ------------------------------------------------------------------
    # Upstream -> browser (with the post-hoc guard)
    # ------------------------------------------------------------------

    async def _pump_upstream(self) -> None:
        while True:
            raw = await self._upstream.recv()
            try:
                event = json.loads(raw)
            except (TypeError, ValueError):
                continue
            await self._handle_upstream_event(event)

    async def _handle_upstream_event(self, event: dict[str, Any]) -> None:
        etype = str(event.get("type", ""))

        if etype.endswith("output_audio.delta") or etype == "response.audio.delta":
            if not self._guard_tripped:
                await self._browser_send({"type": "audio", "data": event.get("delta", "")})
        elif etype.endswith("output_audio_transcript.delta") or etype == "response.audio_transcript.delta":
            delta = str(event.get("delta", ""))
            self._assistant_transcript += delta
            if not self._guard_tripped and speech_violates_medical_boundary(
                self._assistant_transcript
            ):
                # The model crossed the line mid-sentence: cancel it, flush
                # what the browser hasn't played, and speak the redirect.
                self._guard_tripped = True
                await self._upstream.send(json.dumps({"type": "response.cancel"}))
                await self._browser_send({"type": "clear"})
                await self._browser_send(
                    {"type": "guard_redirect", "text": MEDICAL_BOUNDARY_REDIRECT}
                )
            elif not self._guard_tripped:
                await self._browser_send({"type": "assistant_transcript_delta", "text": delta})
        elif etype.endswith("input_audio_transcription.completed"):
            transcript = str(event.get("transcript", "")).strip()
            self._user_transcript = transcript
            if transcript:
                await self._browser_send({"type": "user_transcript", "text": transcript})
        elif etype == "input_audio_buffer.speech_started":
            # Barge-in: he started talking — whatever is queued goes silent.
            await self._browser_send({"type": "clear"})
        elif etype == "response.done":
            await self._on_response_done(event)
        elif etype == "error":
            detail = (event.get("error") or {}).get("message", "")
            logger.warning("realtime upstream error: %s", detail)
            await self._browser_send(
                {"type": "notice", "text": "Parker's live line hiccuped — keep talking."}
            )

    async def _on_response_done(self, event: dict[str, Any]) -> None:
        response = event.get("response") or {}
        speech = (
            self._assistant_transcript
            if not self._guard_tripped
            else MEDICAL_BOUNDARY_REDIRECT
        )
        if self._user_transcript or speech:
            await run_in_threadpool(_record_exchange_sync, self._user_transcript, speech)
        self._assistant_transcript = ""
        self._guard_tripped = False
        self._user_transcript = ""

        for item in response.get("output") or []:
            if not isinstance(item, dict) or item.get("type") != "function_call":
                continue
            if item.get("name") != "propose_action":
                continue
            try:
                arguments = json.loads(item.get("arguments") or "{}")
            except (TypeError, ValueError):
                arguments = {}
            outcome = await run_in_threadpool(_stage_proposal_sync, arguments)
            await self._upstream.send(
                json.dumps(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call_output",
                            "call_id": item.get("call_id", ""),
                            "output": json.dumps(outcome),
                        },
                    }
                )
            )
            await self._upstream.send(json.dumps({"type": "response.create"}))
            if outcome.get("status") == "staged":
                await self._browser_send(
                    {"type": "proposal_staged", "label": str(arguments.get("label", ""))}
                )
