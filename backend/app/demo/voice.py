"""Voice demo: local transcription → text-loop replay.

``make demo-voice AUDIO=path.wav`` transcribes the audio file entirely on
this machine and feeds the transcript lines through the same
``TextSession`` routing as ``make demo``'s scripted replay — repair
choices, refusals, and human-approval guards all apply unchanged. The
audio file is only read, never copied or stored; transcripts are the
only artifact that persists.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.demo.replay import replay_transcript
from app.voice.transcribe import Transcriber, transcribe_audio

VOICE_CALL_SID = "DEMO-VOICE"


def run_voice_demo(
    db: Session,
    audio_path: str | Path,
    *,
    transcriber: Optional[Transcriber] = None,
    call_sid: str = VOICE_CALL_SID,
) -> list[dict[str, Any]]:
    """Transcribe audio locally and replay the lines through a TextSession."""

    lines = transcribe_audio(audio_path, transcriber=transcriber)
    if not lines:
        return []
    return replay_transcript(db, script=lines, call_sid=call_sid)


def main() -> None:  # pragma: no cover — CLI entry point
    from app.db.database import SessionLocal, create_tables
    from app.parker.pipeline import resolve_captured_intents, stage_resolved_actions

    if len(sys.argv) != 2:
        print("usage: python -m app.demo.voice <audio-file>  (or: make demo-voice AUDIO=path.wav)")
        raise SystemExit(2)
    audio = Path(sys.argv[1])
    create_tables()
    db = SessionLocal()
    print(f"Transcribing {audio.name} locally (no cloud APIs; audio is read, not stored)…\n")
    exchanges = run_voice_demo(db, audio)
    if not exchanges:
        print("No speech recognized in the audio file.")
    for exchange in exchanges:
        print(f"  you>    {exchange['you']}")
        print(f"  parker> {exchange['parker']}\n")
    resolved = resolve_captured_intents(db)
    staged = stage_resolved_actions(db)
    print(f"Tick: resolved {len(resolved)}, staged {len(staged)} — review at /parker/review/ui")
    db.close()


if __name__ == "__main__":  # pragma: no cover
    main()
