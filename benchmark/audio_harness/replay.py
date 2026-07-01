"""Route transcript lines through the real TextSession, isolated per run.

Each replay gets a fresh in-memory SQLite database so clips can never
contaminate each other's captures. ``simulate_repair`` additionally acts
as a cooperative user: when Parker offers repair choices and one of them
matches the known target intent, it selects that choice — this is how the
harness measures recovery *with* the repair protocol without a human in
the loop.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from . import BACKEND_ROOT  # noqa: F401 — ensures backend is importable

from app.conversation.textloop import TextSession  # noqa: E402
from app.db.database import Base  # noqa: E402
from app.db.models import CallLog, CapturedIntent  # noqa: E402
import app.escalation.models  # noqa: F401, E402 — register tables
import app.evening.session  # noqa: F401, E402
import app.exercises.session  # noqa: F401, E402
import app.memory.models  # noqa: F401, E402

from .score import choice_matches  # noqa: E402

REFUSAL_KINDS = {"refused", "emergency_redirect", "needs_human_approval"}


@dataclass
class Outcome:
    kinds: list[str] = field(default_factory=list)
    captured: list[dict[str, Any]] = field(default_factory=list)
    offered_choices: list[dict[str, Any]] = field(default_factory=list)
    repair_selections: int = 0

    @property
    def effect(self) -> str:
        if self.captured:
            return "captured"
        if any(kind in REFUSAL_KINDS for kind in self.kinds):
            return "refused"
        if "choices" in self.kinds:
            return "choices"
        return "noop"


@contextmanager
def _memory_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = factory()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _collect(db: Session, session: TextSession, lines: list[str], *, targets: list[dict[str, Any]] | None) -> Outcome:
    outcome = Outcome()
    queue = list(lines)
    while queue:
        line = queue.pop(0)
        response = session.handle(line)
        outcome.kinds.append(response["kind"])
        if response["kind"] == "choices":
            choices = response.get("choices") or []
            outcome.offered_choices.extend(choices)
            if targets:
                match = next(
                    (c for c in choices for t in targets if choice_matches(c, t)), None
                )
                if match is not None:
                    outcome.repair_selections += 1
                    queue.insert(0, str(match["position"]))
    for row in db.query(CapturedIntent).all():
        outcome.captured.append(
            {
                "requested_action": row.requested_action,
                "subject": row.subject,
                "recipient": row.recipient,
                "intent_text": row.intent_text,
            }
        )
    return outcome


def route_lines(lines: list[str], *, targets: list[dict[str, Any]] | None = None, call_sid: str = "AUDIO-HARNESS") -> Outcome:
    """Replay lines in a fresh DB. With ``targets``, act on matching repair choices."""

    with _memory_session() as db:
        call = CallLog(call_sid=call_sid, call_type="text_loop")
        db.add(call)
        db.commit()
        db.refresh(call)
        session = TextSession(db, call.id)
        return _collect(db, session, lines, targets=targets)
