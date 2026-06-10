"""Talk to Parker: microphone → local transcription → text-loop routing.

``make talk`` records a few seconds from the default microphone,
transcribes the recording on this machine, and routes each utterance
through the same ``TextSession`` rules as ``make demo`` and
``make demo-voice`` — capture, repair choices, refusals,
human-approval routing.

Privacy invariant (pinned by tests): the recording lives in a
temporary file only for the seconds it takes to transcribe and is
deleted unconditionally afterwards, success or failure. Transcripts
are the only artifact.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.demo.replay import replay_transcript
from app.voice.record import Recorder, load_local_recorder
from app.voice.transcribe import Transcriber, transcribe_audio

TALK_CALL_SID = "DEMO-TALK"
DEFAULT_SECONDS = 6.0


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
    with tempfile.TemporaryDirectory(prefix="parker-talk-") as tmpdir:
        recording = Path(tmpdir) / "utterance.wav"
        try:
            record(recording, seconds)
            lines = transcribe_audio(recording, transcriber=transcriber)
        finally:
            recording.unlink(missing_ok=True)
    if not lines:
        return []
    return replay_transcript(db, script=lines, call_sid=call_sid)


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
