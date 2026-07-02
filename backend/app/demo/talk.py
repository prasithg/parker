"""Talk to Parker: microphone → local transcription → text-loop routing.

``make talk`` records one fixed window from the default microphone and
routes the transcript through the text loop (single-shot).

``make talk-loop`` runs a continuous listen→route cycle over one
persistent ``TextSession`` so repair-choice state carries across turns:
if Parker offers "1) reminder 2) message" in one window, saying "1"
in the next window selects it correctly.

Privacy invariant (pinned by tests): every recording lives in a
temporary file deleted unconditionally after transcription, success or
failure. Transcripts are the only artifact.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from sqlalchemy.orm import Session

from app.brain.claude import build_brain_adapter
from app.conversation.textloop import TextSession, _build_model_client
from app.db.models import CallLog
from app.demo.replay import replay_transcript
from app.voice.record import Recorder, load_local_recorder
from app.voice.transcribe import Transcriber, transcribe_audio

TALK_CALL_SID = "DEMO-TALK"
TALK_LOOP_CALL_SID = "DEMO-TALK-LOOP"
DEFAULT_SECONDS = 6.0


def _record_one(
    record: Recorder,
    transcriber: Optional[Transcriber],
    seconds: float,
) -> list[str]:
    """Record one window into a temp file, transcribe, delete. Returns lines."""
    with tempfile.TemporaryDirectory(prefix="parker-talk-") as tmpdir:
        recording = Path(tmpdir) / "utterance.wav"
        try:
            record(recording, seconds)
            return transcribe_audio(recording, transcriber=transcriber)
        finally:
            recording.unlink(missing_ok=True)


def run_talk(
    db: Session,
    *,
    seconds: float = DEFAULT_SECONDS,
    recorder: Optional[Recorder] = None,
    transcriber: Optional[Transcriber] = None,
    call_sid: str = TALK_CALL_SID,
) -> list[dict[str, Any]]:
    """Record one utterance, transcribe it locally, replay it; keep no audio."""

    record = recorder or load_local_recorder()
    lines = _record_one(record, transcriber, seconds)
    if not lines:
        return []
    return replay_transcript(db, script=lines, call_sid=call_sid)


def run_talk_loop(
    db: Session,
    *,
    seconds: float = DEFAULT_SECONDS,
    recorder: Optional[Recorder] = None,
    transcriber: Optional[Transcriber] = None,
    call_sid: str = TALK_LOOP_CALL_SID,
    max_turns: Optional[int] = None,
    on_turn_start: Optional[Callable[[int], None]] = None,
    on_exchange: Optional[Callable[[dict[str, Any]], None]] = None,
    on_silence: Optional[Callable[[], None]] = None,
) -> list[dict[str, Any]]:
    """Continuous listen→route loop over one persistent TextSession.

    One ``TextSession`` lives for the whole conversation so repair-choice
    state carries across recording windows. Each turn: record → transcribe
    → feed each utterance to the session → tick. Runs until
    ``KeyboardInterrupt`` (interactive use) or ``max_turns`` is reached
    (tests). Returns all exchanges from all turns.
    """
    from app.parker.pipeline import resolve_captured_intents, stage_resolved_actions

    record = recorder or load_local_recorder()

    call = db.query(CallLog).filter(CallLog.call_sid == call_sid).first()
    if call is None:
        call = CallLog(call_sid=call_sid, call_type="text_loop")
        db.add(call)
        db.commit()
        db.refresh(call)

    session = TextSession(
        db, call.id, model_client=_build_model_client(), brain=build_brain_adapter()
    )
    all_exchanges: list[dict[str, Any]] = []
    turn = 0

    try:
        while max_turns is None or turn < max_turns:
            if on_turn_start:
                on_turn_start(turn)

            lines = _record_one(record, transcriber, seconds)
            turn += 1

            if not lines:
                if on_silence:
                    on_silence()
                continue

            for line in lines:
                response = session.handle(line)
                exchange = {"you": line, "parker": response["speech"], "kind": response["kind"]}
                all_exchanges.append(exchange)
                if on_exchange:
                    on_exchange(exchange)

            resolve_captured_intents(db)
            stage_resolved_actions(db)

    except KeyboardInterrupt:
        pass

    return all_exchanges


def main() -> None:  # pragma: no cover — CLI entry point
    from app.db.database import SessionLocal, create_tables
    from app.parker.pipeline import resolve_captured_intents, stage_resolved_actions

    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SECONDS
    create_tables()
    db = SessionLocal()
    print(f"Listening for {seconds:g}s — speak now (audio is transcribed locally, then deleted)…")
    exchanges = run_talk(db, seconds=seconds)
    if not exchanges:
        print("Didn't catch anything that time — try again a little closer to the mic.")
    for exchange in exchanges:
        print(f"  you>    {exchange['you']}")
        print(f"  parker> {exchange['parker']}\n")
    resolved = resolve_captured_intents(db)
    staged = stage_resolved_actions(db)
    print(f"Tick: resolved {len(resolved)}, staged {len(staged)} — review at /parker/review/ui")
    db.close()


if __name__ == "__main__":  # pragma: no cover
    main()
