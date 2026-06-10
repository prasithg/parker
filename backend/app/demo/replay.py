"""Replay a synthetic effortful-speech transcript through the text loop.

Feeds a scripted conversation — disfluent phrasing, a repair-choice
selection, an unsafe request, a purchase request — through the real
``TextSession`` routing, printing the dialogue and leaving captured
intents in the local DB. This is the transcript-to-repair demo seam: an
ASR transcript can later replace the script line-for-line.

Synthetic only. The refusal and human-approval lines exercise the safety
guards; nothing external happens.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.conversation.textloop import TextSession
from app.db.models import CallLog

REPLAY_CALL_SID = "DEMO-REPLAY"

# (speaker_line, note shown in the printed dialogue)
DEMO_SCRIPT: list[str] = [
    "Remind me to water the tomato plants this evening",
    "Tell Sarah the physio visit went really well today",
    "Call... the... you know... the one with the garden...",
    "1",  # picks the reminder interpretation from the repair choices
    "Should I take half my pills tomorrow?",  # refused, redirected
    "Order that walker with the card on file",  # routed to human approval
    "What's the weather looking like this weekend?",  # answer stub
]


def replay_transcript(
    db: Session,
    script: list[str] | None = None,
    call_sid: str = REPLAY_CALL_SID,
) -> list[dict[str, Any]]:
    """Run each scripted line through a TextSession; return the exchanges."""

    call = db.query(CallLog).filter(CallLog.call_sid == call_sid).first()
    if call is None:
        call = CallLog(call_sid=call_sid, call_type="text_loop")
        db.add(call)
        db.commit()
        db.refresh(call)
    session = TextSession(db, call.id)
    exchanges: list[dict[str, Any]] = []
    for line in script or DEMO_SCRIPT:
        response = session.handle(line)
        exchanges.append({"you": line, "parker": response["speech"], "kind": response["kind"]})
    return exchanges


def main() -> None:  # pragma: no cover — CLI entry point
    from app.db.database import SessionLocal, create_tables
    from app.parker.pipeline import resolve_captured_intents, stage_resolved_actions

    create_tables()
    db = SessionLocal()
    print("Replaying synthetic effortful-speech transcript through the text loop:\n")
    for exchange in replay_transcript(db):
        print(f"  you>    {exchange['you']}")
        print(f"  parker> {exchange['parker']}\n")
    resolved = resolve_captured_intents(db)
    staged = stage_resolved_actions(db)
    print(f"Tick: resolved {len(resolved)}, staged {len(staged)} — review at /parker/review/ui")
    db.close()


if __name__ == "__main__":  # pragma: no cover
    main()
