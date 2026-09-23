"""Server side of the Patient Curiosity Loop (docs/plans/2026-08-29-*).

One browser session = one ``ConverseSession``: a dedicated DB session, one
persistent ``TextSession`` (so repair state and brain history carry across
turns), a generation counter for Stop, and a turn lock that serializes
processing. The store shares one warmed local transcriber across sessions —
the model loads once per server process, at session creation, never inside a
turn.

Contracts pinned by tests:

- Temporary audio lives for exactly one transcription and is deleted on
  success, failure, and cancellation alike; transcripts are the only
  artifact (same privacy contract as the talk loop and Voice Practice).
- Stop bumps the session generation. A turn that finishes under a stale
  generation is discarded: its result never reaches the browser or the
  live screen row, and the session's transient prompts (pending choices /
  yes-no offer) are dismissed so the next turn cannot inherit them. Staged
  actions keep the defer semantics — visible on the review page, never
  silently acted on.
- Touch is addressing: turns route with ``addressed_to_parker=True`` and
  source ``touch_start`` — wake gating stays a talk-loop concern.
- Turn responses carry per-stage timings (decode/asr/route/provider) so
  latency is an observable contract, and every turn appends an
  aggregate-only receipt line locally (never speech content beyond what
  the screen already shows — receipts hold stage timings and kinds only).
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import secrets
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from app.brain.adapter import BrainAdapter, BrainContext, BrainReply, Message
from app.conversation.textloop import TextSession, UtteranceContext, _build_model_client
from app.db.models import CallLog
from app.parker.screen import publish_screen_state
from app.voice.transcribe import Transcriber, transcribe_audio

logger = logging.getLogger("parker.converse")

SESSION_TTL_SECONDS = 30 * 60
MAX_SESSIONS = 6
# 16 kHz mono 16-bit WAV: three minutes of patient capture plus header room.
MAX_CONVERSE_AUDIO_BYTES = 16_000 * 2 * 180 + 4096

SILENCE_SPEECH = "I didn't catch anything that time — take your time and try again."
STOPPED_SPEECH = "Stopped."

TOUCH_CONTEXT = UtteranceContext(addressed_to_parker=True, source="touch_start")


class ConverseError(RuntimeError):
    """A request-level failure with an HTTP status the router maps directly."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class TimedBrain:
    """Wraps the session brain: per-turn provider latency + sentence streaming.

    Purely observational on the reply itself — same contract, no retries.
    The store reads and resets ``last_elapsed_ms`` around each turn so the
    timing a turn reports is its own. When the store sets ``on_sentence``
    for a streaming turn and the inner brain supports ``respond_stream``,
    sentences flow to the callback as they generate; the returned reply is
    still the authoritative, guard-screened one.
    """

    def __init__(self, inner: BrainAdapter) -> None:
        self._inner = inner
        self.last_elapsed_ms: float = 0.0
        self.on_sentence: Callable[[str], None] | None = None

    def respond(
        self, history: list[Message], utterance: str, context: BrainContext
    ) -> BrainReply:
        started = time.monotonic()
        try:
            callback = self.on_sentence
            if callback is not None and hasattr(self._inner, "respond_stream"):
                return self._inner.respond_stream(history, utterance, context, callback)
            return self._inner.respond(history, utterance, context)
        finally:
            self.last_elapsed_ms += (time.monotonic() - started) * 1000.0


def _build_default_brain() -> TimedBrain | None:
    """The configured brain wrapped for timing, or None for the honest stub."""

    from app.brain.build import build_brain_adapter

    inner = build_brain_adapter()
    return None if inner is None else TimedBrain(inner)


def _decode_audio(encoded: str) -> bytes:
    try:
        content = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError, binascii.Error):
        raise ConverseError(422, "audio_base64 is not valid base64")
    if not content:
        raise ConverseError(422, "audio must not be empty")
    if len(content) > MAX_CONVERSE_AUDIO_BYTES:
        raise ConverseError(
            413, f"audio exceeds {MAX_CONVERSE_AUDIO_BYTES} bytes"
        )
    return content


def _strip_choices(choices: Optional[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Position and label only — capture internals never leave the server."""

    return [
        {"position": choice["position"], "label": choice["label"]}
        for choice in (choices or [])
        if isinstance(choice, dict) and "position" in choice and "label" in choice
    ]


class ConverseSession:
    """One browser conversation: state the store guards with two locks."""

    def __init__(
        self, session_id: str, db: Any, text_session: TextSession, brain: TimedBrain | None
    ):
        self.id = session_id
        self.db = db
        self.text_session = text_session
        self.brain = brain
        self.generation = 0
        self.turn_lock = threading.Lock()  # serializes turn processing
        self.state_lock = threading.Lock()  # guards generation + last result
        self.last_result: dict[str, Any] | None = None
        self.last_active = time.monotonic()
        self.created_at = datetime.utcnow()


class ConverseStore:
    """Owns every live converse session and the one warmed transcriber."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Any] | None = None,
        transcriber_loader: Callable[[], Transcriber] | None = None,
        brain_builder: Callable[[], TimedBrain] | None = None,
        model_client_builder: Callable[[], Any] | None = None,
        receipt_writer: Callable[[dict[str, Any]], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._transcriber_loader = transcriber_loader
        self._brain_builder = brain_builder or _build_default_brain
        self._model_client_builder = model_client_builder or _build_model_client
        self._receipt_writer = receipt_writer or write_receipt
        self._clock = clock or time.monotonic
        self._sessions: dict[str, ConverseSession] = {}
        self._store_lock = threading.Lock()
        self._transcriber: Transcriber | None = None
        self._transcriber_lock = threading.Lock()
        self._transcriber_error: str | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def create_session(self) -> dict[str, Any]:
        self._sweep_expired()
        with self._store_lock:
            if len(self._sessions) >= MAX_SESSIONS:
                # Evict the most idle session — but never one mid-turn: its
                # DB session is in use on another thread.
                for candidate in sorted(self._sessions.values(), key=lambda s: s.last_active):
                    if candidate.turn_lock.acquire(blocking=False):
                        try:
                            self._drop(candidate.id)
                        finally:
                            candidate.turn_lock.release()
                        break

        asr_ready = self._warm_transcriber()
        session_id = secrets.token_urlsafe(16)
        db = self._make_db()
        call = CallLog(call_sid=f"CONVERSE-{session_id[:24]}", call_type="converse")
        db.add(call)
        db.commit()
        db.refresh(call)
        brain = self._brain_builder()
        text_session = TextSession(
            db,
            call.id,
            model_client=self._model_client_builder(),
            brain=brain,
            outcome_source="curiosity_loop",
        )
        session = ConverseSession(session_id, db, text_session, brain)
        session.last_active = self._clock()  # the store's clock, tests included
        with self._store_lock:
            self._sessions[session_id] = session
        return {
            "session_id": session_id,
            "asr_ready": asr_ready,
            "asr_hint": self._transcriber_error,
        }

    def end_session(self, session_id: str) -> dict[str, Any]:
        session = self._require(session_id)
        with session.state_lock:
            session.generation += 1
        # Wait for any in-flight turn before closing its DB session.
        with session.turn_lock:
            with self._store_lock:
                self._drop(session_id)
        return {"ended": True}

    def _make_db(self) -> Any:
        if self._session_factory is not None:
            return self._session_factory()
        from app.db.database import SessionLocal, create_tables

        create_tables()
        return SessionLocal()

    def transcriber(self):
        """The warmed shared local transcriber, or None when unavailable.

        The wake lane (app/parker/wake.py) runs on the SAME model the
        push-button lane transcribes with — one load, one cache.
        """

        self._warm_transcriber()
        return self._transcriber

    def _warm_transcriber(self) -> bool:
        """Load the shared local model exactly once; remember an unavailable state."""

        with self._transcriber_lock:
            if self._transcriber is not None:
                return True
            loader = self._transcriber_loader
            if loader is None:
                from app.config import settings
                from app.voice.transcribe import load_local_transcriber

                def loader():  # the family's thread cap applies to the shared model
                    return load_local_transcriber(cpu_threads=settings.parker_asr_cpu_threads)
            try:
                self._transcriber = loader()
                self._transcriber_error = None
                return True
            except Exception as exc:  # noqa: BLE001 — any load failure is "unavailable", never a dead socket
                # ImportError arrives as RuntimeError(VOICE_DEPS_HINT); the
                # realistic first-run failures (weights not cached while
                # offline, hub unreachable, a half-downloaded snapshot) are
                # huggingface_hub's LocalEntryNotFoundError — a
                # FileNotFoundError — and a bad model size is a ValueError.
                # Not sticky: the next call retries the load, human-paced
                # (the page powers off on "unavailable").
                logger.warning("local transcriber failed to load: %s", exc, exc_info=True)
                self._transcriber_error = str(exc) or type(exc).__name__
                return False

    def _sweep_expired(self) -> None:
        now = self._clock()
        with self._store_lock:
            expired = [
                session
                for session in self._sessions.values()
                if (now - session.last_active) > SESSION_TTL_SECONDS
            ]
            for session in expired:
                if session.turn_lock.acquire(blocking=False):
                    try:
                        self._drop(session.id)
                    finally:
                        session.turn_lock.release()

    def _drop(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return
        try:
            session.db.close()
        except Exception:  # noqa: BLE001 — closing must never break the store
            logger.debug("closing converse session db failed", exc_info=True)

    def _require(self, session_id: str) -> ConverseSession:
        with self._store_lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise ConverseError(404, "Unknown or expired conversation session")
        return session

    # ------------------------------------------------------------------
    # Turns
    # ------------------------------------------------------------------

    def run_turn(
        self,
        session_id: str,
        *,
        turn_id: int,
        audio_base64: str | None = None,
        text: str | None = None,
        emit: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Run one turn; ``emit`` (optional) receives progressive events.

        Events, in order: ``{"event": "heard", ...}`` once the transcript
        exists, then zero or more ``{"event": "speech", "text": sentence}``
        as the brain's answer generates — each sentence pre-screened by the
        same medical-boundary detector the final guard uses, capped at the
        TTS trim length, and dropped once the generation goes stale. The
        complete guard-screened result is still the return value.
        """

        session = self._require(session_id)
        if (audio_base64 is None) == (text is None):
            raise ConverseError(422, "Provide exactly one of audio_base64 or text")

        audio_bytes = _decode_audio(audio_base64) if audio_base64 is not None else None
        with session.state_lock:
            generation = session.generation
            session.last_active = self._clock()

        with session.turn_lock:
            # end_session may have raced the decode above: it drops and
            # closes the session under this same lock, so a turn that gets
            # the lock afterwards must not touch the closed DB session.
            with self._store_lock:
                if self._sessions.get(session_id) is not session:
                    raise ConverseError(404, "Unknown or expired conversation session")
            result = self._process_turn(
                session,
                generation=generation,
                turn_id=turn_id,
                audio_bytes=audio_bytes,
                text=text,
                emit=emit,
            )
        self._write_turn_receipt(session, result)
        return result

    def _streaming_sentence_guard(
        self,
        session: ConverseSession,
        generation: int,
        emit: Callable[[dict[str, Any]], None],
    ) -> Callable[[str], None]:
        """Per-sentence gate for streamed speech.

        Applies the same medical-boundary detector as the final guard to
        the ACCUMULATED text (so a violation assembled across sentences
        still trips), enforces the TTS trim cap, and stops emitting the
        moment the generation goes stale. The final whole-reply guard still
        runs afterwards and remains authoritative.
        """

        from app.brain.guard import speech_violates_medical_boundary

        state = {"accumulated": "", "spoken": "", "count": 0, "blocked": False}

        def on_sentence(sentence: str) -> None:
            if state["blocked"] or state["count"] >= 3:
                return
            with session.state_lock:
                if session.generation != generation:
                    state["blocked"] = True
                    return
            state["accumulated"] = f"{state['accumulated']} {sentence}".strip()
            if speech_violates_medical_boundary(state["accumulated"]):
                state["blocked"] = True  # the final guard speaks the redirect
                return
            # Mirror trim_for_speech exactly (3 sentences AND 360 chars), so
            # what streams is always a prefix of the final screened speech —
            # the page speaks the final's remainder ("Want more detail?"),
            # never characters the trim withheld.
            would_speak = f"{state['spoken']} {sentence}".strip()
            if state["spoken"] and len(would_speak) > 360:
                state["blocked"] = True
                return
            state["spoken"] = would_speak
            state["count"] += 1
            try:
                emit({"event": "speech", "text": sentence})
            except Exception:  # noqa: BLE001 — a dead pipe must not kill the turn
                state["blocked"] = True

        return on_sentence

    def _process_turn(
        self,
        session: ConverseSession,
        *,
        generation: int,
        turn_id: int,
        audio_bytes: bytes | None,
        text: str | None,
        emit: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        timings: dict[str, float] = {"decode": 0.0, "asr": 0.0, "route": 0.0, "provider": 0.0}
        started = time.monotonic()

        if audio_bytes is not None:
            lines = self._transcribe(audio_bytes, timings)
        else:
            lines = [text.strip()] if text and text.strip() else []

        if self._is_stale(session, generation):
            return self._stopped_result(session, turn_id, timings, started)

        if emit is not None and lines:
            try:
                emit({"event": "heard", "heard": " ".join(lines)})
            except Exception:  # noqa: BLE001
                emit = None

        if not lines:
            with session.state_lock:
                if session.generation != generation:
                    # Stop raced a silent window; stopped means stopped —
                    # even a gentle retry line must not land afterwards.
                    return self._stopped_result(session, turn_id, timings, started)
                result = {
                    "turn_id": turn_id,
                    "state": "silence",
                    "kind": "silence",
                    "heard": "",
                    "speech": SILENCE_SPEECH,
                    "choices": [],
                    "sources": [],
                    "awaiting": "",
                    "timings_ms": self._final_timings(timings, started),
                }
                session.last_result = result
                session.last_active = self._clock()
                return result

        route_started = time.monotonic()
        if session.brain is not None:
            session.brain.last_elapsed_ms = 0.0
            if emit is not None:
                session.brain.on_sentence = self._streaming_sentence_guard(
                    session, generation, emit
                )
        exchanges: list[dict[str, Any]] = []
        try:
            for line in lines:
                response = session.text_session.handle(line, context=TOUCH_CONTEXT)
                exchanges.append({"you": line, **response})
        finally:
            if session.brain is not None:
                session.brain.on_sentence = None

        from app.parker.pipeline import resolve_captured_intents, stage_resolved_actions

        resolve_captured_intents(session.db, call_log_id=session.text_session.call_log_id)
        stage_resolved_actions(session.db, call_log_id=session.text_session.call_log_id)
        offer = session.text_session.offer_pending_confirmation()
        if offer is not None:
            exchanges.append({"you": "", **offer})
        timings["route"] = (time.monotonic() - route_started) * 1000.0
        timings["provider"] = session.brain.last_elapsed_ms if session.brain else 0.0
        timings["route"] = max(0.0, timings["route"] - timings["provider"])

        with session.state_lock:
            if session.generation != generation:
                # Stop won the race: the person asked for silence before this
                # result existed. Discard it entirely.
                session.text_session.dismiss_transient_state()
                self._publish_stopped_screen(session)
                return self._stopped_result(session, turn_id, timings, started)
            result = self._build_result(session, turn_id, exchanges, timings, started)
            session.last_result = result
            session.last_active = self._clock()
            return result

    def _transcribe(self, audio_bytes: bytes, timings: dict[str, float]) -> list[str]:
        if self._transcriber is None and not self._warm_transcriber():
            raise ConverseError(
                503, self._transcriber_error or "Local transcription is not available"
            )
        decode_started = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(prefix="parker-converse-") as tmpdir:
                audio_path = Path(tmpdir) / "utterance.wav"
                descriptor = os.open(audio_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(audio_bytes)
                timings["decode"] = (time.monotonic() - decode_started) * 1000.0
                asr_started = time.monotonic()
                lines = transcribe_audio(audio_path, transcriber=self._transcriber)
                timings["asr"] = (time.monotonic() - asr_started) * 1000.0
                return lines
        except ConverseError:
            raise
        except RuntimeError as exc:
            raise ConverseError(503, str(exc))
        except (OSError, ValueError) as exc:
            raise ConverseError(422, "Parker could not transcribe that audio locally") from exc

    def _is_stale(self, session: ConverseSession, generation: int) -> bool:
        with session.state_lock:
            return session.generation != generation

    def _build_result(
        self,
        session: ConverseSession,
        turn_id: int,
        exchanges: list[dict[str, Any]],
        timings: dict[str, float],
        started: float,
    ) -> dict[str, Any]:
        heard = " ".join(e["you"] for e in exchanges if e.get("you"))
        speakable = [
            e for e in exchanges if e.get("speech") and e.get("kind") != "ambient_noop"
        ]
        # One turn, one question. Whisper merges pause-free speech into one
        # window, and each line routes separately — so a repair question
        # from line one can be superseded by line two a millisecond later.
        # A prompt that is not the final exchange is dead: speaking it would
        # stack two questions in one breath while only the last one's
        # buttons exist (found by the adversarial verifier).
        prompt_kinds = {
            "choices",
            "clarify",
            "retry",
            "confirmation_repair",
            "confirmation_mismatch",
            "confirm_offer",
        }
        if speakable:
            speakable = [
                e for e in speakable[:-1] if e.get("kind") not in prompt_kinds
            ] + [speakable[-1]]
        # A capture immediately followed by its own confirmation offer says
        # the subject twice ("Okay — I'll bring up X… Ready when you are:
        # a reminder about X…"). One turn, one readback: the offer alone
        # carries the confirmation contract.
        if (
            len(speakable) >= 2
            and speakable[-1].get("kind") == "confirm_offer"
            and speakable[-2].get("kind") in {"captured", "revised"}
        ):
            speakable = speakable[:-2] + [speakable[-1]]
        spoken = [e.get("speech", "") for e in speakable]
        last = exchanges[-1] if exchanges else {}
        sources: list[dict[str, str]] = []
        for exchange in exchanges:
            for source in exchange.get("sources", []) or []:
                if isinstance(source, dict):
                    sources.append(
                        {
                            "label": str(source.get("label", "")),
                            "url": str(source.get("url", "")),
                            "fresh_as_of": str(source.get("fresh_as_of", "")),
                        }
                    )
        if session.text_session.has_pending_choices:
            awaiting = "choices"
        elif session.text_session.has_pending_confirmation:
            awaiting = "yes_no"
        else:
            awaiting = ""
        # The rendered choices must be the LIVE pending set — which is not
        # necessarily on the final exchange when a later line of the same
        # window was a control word or silence-shaped noop.
        if awaiting == "choices":
            choice_source = next(
                (e for e in reversed(exchanges) if e.get("choices")), {}
            )
            choices = _strip_choices(choice_source.get("choices"))
        else:
            choices = []
        return {
            "turn_id": turn_id,
            "state": "answer",
            "kind": str(last.get("kind", "")),
            "heard": heard,
            "speech": " ".join(spoken).strip(),
            "choices": choices,
            "sources": sources,
            "awaiting": awaiting,
            "timings_ms": self._final_timings(timings, started),
        }

    def _stopped_result(
        self,
        session: ConverseSession,
        turn_id: int,
        timings: dict[str, float],
        started: float,
    ) -> dict[str, Any]:
        return {
            "turn_id": turn_id,
            "state": "stopped",
            "kind": "stopped",
            "heard": "",
            "speech": "",
            "choices": [],
            "sources": [],
            "awaiting": "",
            "timings_ms": self._final_timings(timings, started),
        }

    @staticmethod
    def _final_timings(timings: dict[str, float], started: float) -> dict[str, int]:
        totals = dict(timings)
        totals["total_after_done"] = (time.monotonic() - started) * 1000.0
        return {key: int(round(value)) for key, value in totals.items()}

    # ------------------------------------------------------------------
    # Stop / state
    # ------------------------------------------------------------------

    def stop(self, session_id: str) -> dict[str, Any]:
        session = self._require(session_id)
        with session.state_lock:
            session.generation += 1
            generation = session.generation
            session.last_result = None
            session.last_active = self._clock()
        # Touch the TextSession and its DB session only when no turn owns
        # them: a SQLAlchemy session is single-threaded by contract. With a
        # turn in flight, its own stale path dismisses transient state and
        # overwrites the screen the moment it finishes — the generation bump
        # above already guarantees its result is discarded.
        if session.turn_lock.acquire(blocking=False):
            try:
                session.text_session.dismiss_transient_state()
                self._publish_stopped_screen(session)
            finally:
                session.turn_lock.release()
        return {"stopped": True, "generation": generation}

    def _publish_stopped_screen(self, session: ConverseSession) -> None:
        """Overwrite the live screen row so a raced turn's frame cannot linger."""

        try:
            publish_screen_state(
                session.db,
                heard="",
                speech=STOPPED_SPEECH,
                kind="cancelled",
                choices=None,
                awaiting="",
            )
        except Exception:  # noqa: BLE001 — the mirror must never break Stop
            session.db.rollback()
            logger.debug("stopped-screen publish skipped", exc_info=True)

    def state(self, session_id: str) -> dict[str, Any]:
        session = self._require(session_id)
        with session.state_lock:
            return {
                "session_id": session.id,
                "generation": session.generation,
                "last_result": session.last_result,
                "asr_ready": self._transcriber is not None,
            }

    def _remember(self, session: ConverseSession, result: dict[str, Any]) -> None:
        with session.state_lock:
            session.last_result = result
            session.last_active = self._clock()

    # ------------------------------------------------------------------
    # Receipts
    # ------------------------------------------------------------------

    def _write_turn_receipt(self, session: ConverseSession, result: dict[str, Any]) -> None:
        try:
            self._receipt_writer(
                {
                    "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                    "recorded_by": "server",
                    "session": session.id[:8],
                    "turn_id": result.get("turn_id"),
                    "state": result.get("state"),
                    "kind": result.get("kind"),
                    "timings_ms": result.get("timings_ms", {}),
                }
            )
        except Exception:  # noqa: BLE001 — receipts must never break a turn
            logger.debug("turn receipt skipped", exc_info=True)

    def record_client_receipt(self, session_id: str, marks: dict[str, Any]) -> dict[str, Any]:
        session = self._require(session_id)
        allowed = {
            key: value
            for key, value in marks.items()
            if key
            in {
                "turn_id",
                "start_to_listening_ms",
                "done_to_response_ms",
                "response_to_first_audio_ms",
                "done_to_first_audio_ms",
                "stop_to_silence_ms",
                "capture_seconds",
                "outcome",
                "expression_dropped",
            }
            and isinstance(value, (int, float, str))
        }
        # Bounded semantic presence transitions (what Reachy showed, when,
        # and why) — the review trail for the Start/Done lane and the tail
        # of a live session after its socket closed. Untrusted client
        # input: allowlisted fields, typed, truncated, and capped.
        transitions = marks.get("expression")
        if isinstance(transitions, list):
            cleaned = []
            for item in transitions[:300]:
                if not isinstance(item, dict):
                    continue
                entry: dict[str, Any] = {}
                for field in ("from", "to", "action", "guard", "attention", "reason", "work"):
                    value = item.get(field)
                    if isinstance(value, str):
                        entry[field] = value[:32]
                for field in ("at_ms", "gen"):
                    value = item.get(field)
                    if isinstance(value, (int, float)):
                        entry[field] = int(value)
                if entry:
                    cleaned.append(entry)
            if cleaned:
                allowed["expression"] = cleaned
        try:
            self._receipt_writer(
                {
                    "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                    "recorded_by": "client",
                    "session": session.id[:8],
                    **allowed,
                }
            )
        except Exception:  # noqa: BLE001
            logger.debug("client receipt skipped", exc_info=True)
        return {"recorded": True}


def write_receipt(entry: dict[str, Any]) -> None:
    """Append one aggregate-only latency receipt line locally (JSONL)."""

    from app import paths

    receipts = paths.receipts_dir()
    receipts.mkdir(parents=True, exist_ok=True)
    with (receipts / "converse_latency.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
