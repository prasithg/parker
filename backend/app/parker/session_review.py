"""The human-testing flywheel: a finished live session, reviewable.

A live realtime session used to evaporate — per-turn transcripts, worker
injections, and latencies lived only in bridge memory and INFO logs,
leaving a one-line CallLog summary and one topic memory. A human tester
cannot judge what they cannot see, so the bridge now journals each
session into `realtime_session_events` (best-effort, retried, never able
to break the call), and `/parker/sessions/ui` shows the finished session
back: what Parker heard, said, injected, and staged, with ack and
inject latencies, plus what the NEXT session's context card now carries.

The judgment loop closes with `realtime_session_feedback`: one tap files
"that felt wrong because…" against a specific event. Local rows only —
nothing here has a send path, and the surface sits behind the same
opt-in dashboard auth as the caregiver review page.

Design constraints honored (docs/personas/ravi-scenarios.md):
- the browser frame vocabulary is untouched — the review page finds the
  latest session by recency, so no new live frames exist for the
  scenario deck to trip over;
- events land in their own feature-owned table (like ScreenState), so
  the deck's exact-count pins on shared tables stay true;
- every write is wrapped in the bridge's retry helper and swallows
  failure — journaling must never end a conversation.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Callable, Optional

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Session

from app.db.database import Base

logger = logging.getLogger("parker.session_review")

# Event kinds the bridge journals. `turn` is one spoken exchange;
# `guard_trip` records what the medical guard cancelled mid-word;
# `lookup_ack` is the instant answer to look_that_up; `injection` is a
# worker result landing mid-conversation; `proposal` is a propose_action
# outcome (the StagedAction rows themselves live in the normal pipeline);
# `expression` is one browser-reported semantic presence transition
# (from/to phase + overlays + reason), bounded and allowlisted by the
# bridge, so review can see what Parker visibly presented.
EVENT_KINDS = ("turn", "guard_trip", "lookup_ack", "injection", "proposal", "expression")

MAX_FEEDBACK_NOTE_CHARS = 2000


class RealtimeSessionEvent(Base):
    __tablename__ = "realtime_session_events"

    id = Column(Integer, primary_key=True)
    call_log_id = Column(Integer, ForeignKey("call_logs.id"), nullable=False, index=True)
    seq = Column(Integer, nullable=False)
    kind = Column(String(24), nullable=False)
    heard = Column(Text, nullable=False, default="")
    said = Column(Text, nullable=False, default="")
    detail = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class RealtimeSessionFeedback(Base):
    __tablename__ = "realtime_session_feedback"

    id = Column(Integer, primary_key=True)
    event_id = Column(
        Integer, ForeignKey("realtime_session_events.id"), nullable=False, index=True
    )
    call_log_id = Column(Integer, ForeignKey("call_logs.id"), nullable=False, index=True)
    note = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ---------------------------------------------------------------------------
# The bridge-side writer (sync; runs in the bridge's threadpool, wrapped in
# realtime's retry helper — each attempt opens its own session, so it is
# idempotent-enough: a duplicate (call, seq) row can only appear if a commit
# landed and then the same closure re-ran, which the retry helper never does
# after a successful return).
# ---------------------------------------------------------------------------


def record_event_sync(
    make_db: Callable[[], Any],
    call_sid: str,
    seq: int,
    kind: str,
    heard: str = "",
    said: str = "",
    detail: Optional[dict[str, Any]] = None,
) -> None:
    """Journal one session event against the session's call log."""

    from app.db.models import CallLog

    db = make_db()
    try:
        call = db.query(CallLog).filter(CallLog.call_sid == call_sid).first()
        if call is None:
            call = CallLog(call_sid=call_sid, call_type="realtime")
            db.add(call)
            db.flush()
        # Idempotent under retry: the verify SELECT below can itself fail
        # transiently AFTER a successful commit, and the retry wrapper
        # then re-runs this whole closure — the row must not double.
        existing = (
            db.query(RealtimeSessionEvent)
            .filter(
                RealtimeSessionEvent.call_log_id == call.id,
                RealtimeSessionEvent.seq == seq,
            )
            .first()
        )
        if existing is not None:
            return
        db.add(
            RealtimeSessionEvent(
                call_log_id=call.id,
                seq=seq,
                kind=kind,
                heard=heard or "",
                said=said or "",
                detail=json.dumps(detail or {}),
            )
        )
        db.commit()
        # Verify-after-commit: on a shared connection (the test harness's
        # StaticPool) a concurrent session's rollback can silently discard
        # this insert between statement and commit — no exception raised.
        # Turning the silent loss into an error makes the retry wrapper
        # actually retry it; on per-session connections this is just one
        # cheap indexed read.
        written = (
            db.query(RealtimeSessionEvent)
            .filter(
                RealtimeSessionEvent.call_log_id == call.id,
                RealtimeSessionEvent.seq == seq,
            )
            .first()
        )
        if written is None:
            raise RuntimeError("realtime journal write was rolled back")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Feed builders for the review surface (read-only over the DB)
# ---------------------------------------------------------------------------


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _parse_detail(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def build_sessions_feed(db: Session, limit: int = 20) -> dict[str, Any]:
    """Recent live sessions, newest first — the tester taps the top one."""

    from app.db.models import CallLog

    calls = (
        db.query(CallLog)
        .filter(CallLog.call_type == "realtime")
        .order_by(CallLog.id.desc())
        .limit(max(1, min(limit, 100)))
        .all()
    )
    sessions = []
    for call in calls:
        turn_count = (
            db.query(RealtimeSessionEvent)
            .filter(
                RealtimeSessionEvent.call_log_id == call.id,
                RealtimeSessionEvent.kind == "turn",
            )
            .count()
        )
        feedback_count = (
            db.query(RealtimeSessionFeedback)
            .filter(RealtimeSessionFeedback.call_log_id == call.id)
            .count()
        )
        sessions.append(
            {
                "call_sid": call.call_sid,
                "started_at": _iso(call.started_at),
                "ended_at": _iso(call.ended_at),
                "duration_seconds": call.duration_seconds,
                "summary": call.summary or "",
                "turn_count": turn_count,
                "feedback_count": feedback_count,
                "live": call.ended_at is None,
            }
        )
    return {"sessions": sessions}


def _staged_actions_for(db: Session, call_log_id: int) -> list[dict[str, Any]]:
    from app.db.models import CapturedIntent, ResolutionResult, StagedAction

    rows = (
        db.query(StagedAction)
        .join(StagedAction.resolution_result)
        .join(ResolutionResult.captured_intent)
        .filter(CapturedIntent.call_log_id == call_log_id)
        .order_by(StagedAction.id)
        .all()
    )
    return [
        {
            "id": row.id,
            "action_type": row.resolution_result.action_type,
            "summary": row.resolution_result.summary or "",
            "status": row.status,
            "created_at": _iso(row.created_at),
        }
        for row in rows
    ]


def build_next_card_preview(db: Session) -> dict[str, Any]:
    """What the NEXT session's context card would carry, computed now.

    Runs the real card builder over the DB-backed sources only — the
    live gateway probe is deliberately skipped so a review-page request
    can never block on the family agent's network (its timeout is sized
    for a background worker, not an HTTP handler). Honest caveats carried
    to the page: ambient gateway lines are absent here, and the
    due-medication/streak lines depend on the clock, so tomorrow's actual
    card can differ from this preview.
    """

    from sqlalchemy.orm import sessionmaker

    from app.parker import realtime_workers

    factory = sessionmaker(bind=db.get_bind())
    db_sources = tuple(
        (name, source)
        for name, source in realtime_workers.CONTEXT_SOURCES
        if name != "gateway"
    )
    result = realtime_workers.run_context_worker(lambda: factory(), sources=db_sources)
    lines = [line for line in (result.speech or "").splitlines() if line.strip()]
    return {"lines": lines, "error": result.error or ""}


def build_session_detail(db: Session, call_sid: str) -> Optional[dict[str, Any]]:
    """One finished (or live) session, everything the tester needs to judge it."""

    from app.db.models import CallLog
    from app.memory.models import ConversationMemory

    call = (
        db.query(CallLog)
        .filter(CallLog.call_sid == call_sid, CallLog.call_type == "realtime")
        .first()
    )
    if call is None:
        return None

    events = (
        db.query(RealtimeSessionEvent)
        .filter(RealtimeSessionEvent.call_log_id == call.id)
        .order_by(RealtimeSessionEvent.seq)
        .all()
    )
    feedback_rows = (
        db.query(RealtimeSessionFeedback)
        .filter(RealtimeSessionFeedback.call_log_id == call.id)
        .order_by(RealtimeSessionFeedback.id)
        .all()
    )
    feedback_by_event: dict[int, list[dict[str, Any]]] = {}
    for row in feedback_rows:
        feedback_by_event.setdefault(row.event_id, []).append(
            {"id": row.id, "note": row.note, "created_at": _iso(row.created_at)}
        )

    minted = (
        db.query(ConversationMemory)
        .filter(
            ConversationMemory.call_log_id == call.id,
            ConversationMemory.source == "realtime",
        )
        .first()
    )

    return {
        "call_sid": call.call_sid,
        "started_at": _iso(call.started_at),
        "ended_at": _iso(call.ended_at),
        "duration_seconds": call.duration_seconds,
        "summary": call.summary or "",
        "live": call.ended_at is None,
        "events": [
            {
                "id": event.id,
                "seq": event.seq,
                "kind": event.kind,
                "heard": event.heard,
                "said": event.said,
                "detail": _parse_detail(event.detail),
                "created_at": _iso(event.created_at),
                "feedback": feedback_by_event.get(event.id, []),
            }
            for event in events
        ],
        "staged_actions": _staged_actions_for(db, call.id),
        "minted_memory": minted.content if minted else "",
        "next_card": build_next_card_preview(db),
    }


def file_feedback(
    db: Session, call_sid: str, event_id: int, note: str
) -> Optional[dict[str, Any]]:
    """File "that felt wrong because…" against one event of one session.

    Returns None when the session or event does not exist, or when the
    event belongs to a different session — the caller turns that into a
    404 rather than filing feedback against the wrong conversation.
    """

    from app.db.models import CallLog

    call = (
        db.query(CallLog)
        .filter(CallLog.call_sid == call_sid, CallLog.call_type == "realtime")
        .first()
    )
    if call is None:
        return None
    event = (
        db.query(RealtimeSessionEvent)
        .filter(
            RealtimeSessionEvent.id == event_id,
            RealtimeSessionEvent.call_log_id == call.id,
        )
        .first()
    )
    if event is None:
        return None
    row = RealtimeSessionFeedback(
        event_id=event.id,
        call_log_id=call.id,
        note=(note or "").strip()[:MAX_FEEDBACK_NOTE_CHARS],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "id": row.id,
        "event_id": row.event_id,
        "note": row.note,
        "created_at": _iso(row.created_at),
    }
