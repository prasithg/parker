"""Family handoff digest: a local, unsent daily summary.

The digest is the family's rearview mirror under the capability trust
model: released messages, executed reminders, exercise and evening
sessions appear as *what happened* — events the family stays aware of,
not items waiting for their approval. The per-message gates that do
exist (queued off-allowlist messages, non-response candidates, failed
skill executions) appear under *needs a look*.

Hard boundaries, pinned by tests:

- Local artifact only. This module has no send path and imports nothing
  that could create one; ``make digest`` writes a markdown file next to
  the local database and prints it. Nothing leaves the machine.
- Events, never advice. The digest reports what Parker did and what is
  waiting ("reminder done: call the pharmacy"), and never generates
  medical recommendations of any kind.
- No credentials or secrets — content comes only from the local Parker
  rows the review page already shows.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import OutboxMessage, StagedAction
from app.escalation.candidates import CANDIDATE_REASON_PREFIX
from app.escalation.engine import get_open_escalations
from app.evening.session import LocalEveningSession
from app.exercises.session import LocalExerciseSession

DIGEST_WINDOW_HOURS = 24
DIGEST_ITEM_CAP = 20  # per section; a digest is a skim, not an export
BODY_PREVIEW_CHARS = 140

# Executed action types covered by their own dedicated digest sections.
_DEDICATED_EXECUTED_TYPES = ("reminder", "family_message", "exercise_start")


def _payload(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _preview(text: str | None) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= BODY_PREVIEW_CHARS:
        return cleaned
    return cleaned[: BODY_PREVIEW_CHARS - 1].rstrip() + "…"


def _when(value: Optional[datetime]) -> Optional[str]:
    return value.strftime("%Y-%m-%d %H:%M") if value else None


def build_digest(
    db: Session,
    *,
    now: Optional[datetime] = None,
    window_hours: int = DIGEST_WINDOW_HOURS,
) -> dict[str, Any]:
    """Assemble the structured digest from local rows.

    "What happened" sections are windowed on their event timestamp;
    "needs a look" sections list what is open *now* regardless of age —
    an old queued message still needs a decision.
    """

    current = now or datetime.utcnow()
    cutoff = current - timedelta(hours=window_hours)

    reminders_done = [
        {
            "subject": _payload(a.action_payload).get("subject"),
            "executed_at": _when(a.executed_at),
            "confirmed_by": a.confirmed_by,
        }
        for a in db.query(StagedAction)
        .filter(StagedAction.status == "executed")
        .filter(StagedAction.action_type == "reminder")
        .filter(StagedAction.executed_at >= cutoff)
        .order_by(StagedAction.executed_at.desc(), StagedAction.id.desc())
        .limit(DIGEST_ITEM_CAP)
        .all()
    ]

    other_done = [
        {
            "action_type": a.action_type,
            "subject": _payload(a.action_payload).get("subject"),
            "executed_at": _when(a.executed_at),
            "result": _preview(a.execution_result),
        }
        for a in db.query(StagedAction)
        .filter(StagedAction.status == "executed")
        .filter(StagedAction.action_type.notin_(_DEDICATED_EXECUTED_TYPES))
        .filter(StagedAction.executed_at >= cutoff)
        .order_by(StagedAction.executed_at.desc(), StagedAction.id.desc())
        .limit(DIGEST_ITEM_CAP)
        .all()
    ]

    def _outbox_rows(status: str, stamp_column) -> list[OutboxMessage]:
        return (
            db.query(OutboxMessage)
            .filter(OutboxMessage.status == status)
            .filter(stamp_column >= cutoff)
            .order_by(stamp_column.desc(), OutboxMessage.id.desc())
            .limit(DIGEST_ITEM_CAP)
            .all()
        )

    messages_released = [
        {
            "recipient": m.recipient,
            "body": _preview(m.body),
            "released_at": _when(m.released_at),
            "released_by": m.released_by,
        }
        for m in _outbox_rows("released_local", OutboxMessage.released_at)
    ]
    messages_approved = [
        {
            "recipient": m.recipient,
            "body": _preview(m.body),
            "approved_at": _when(m.approved_at),
            "approved_by": m.approved_by,
        }
        for m in _outbox_rows("approved_local", OutboxMessage.approved_at)
    ]

    exercise_sessions = [
        {
            "subject": s.subject,
            "status": s.status,
            "started_at": _when(s.started_at),
        }
        for s in db.query(LocalExerciseSession)
        .filter(LocalExerciseSession.started_at >= cutoff)
        .order_by(LocalExerciseSession.started_at.desc(), LocalExerciseSession.id.desc())
        .limit(DIGEST_ITEM_CAP)
        .all()
    ]

    evening_sessions = [
        {
            "evening_date": s.evening_date,
            "status": s.status,
            "started_at": _when(s.started_at),
        }
        for s in db.query(LocalEveningSession)
        .filter(LocalEveningSession.started_at >= cutoff)
        .order_by(LocalEveningSession.started_at.desc(), LocalEveningSession.id.desc())
        .limit(DIGEST_ITEM_CAP)
        .all()
    ]

    changed_mind = [
        {
            "what": _describe_action(a),
            "cancelled_at": _when(a.cancelled_at),
            "cancelled_by": a.cancelled_by,
        }
        for a in db.query(StagedAction)
        .filter(StagedAction.status == "cancelled")
        .filter(StagedAction.cancelled_at >= cutoff)
        .order_by(StagedAction.cancelled_at.desc(), StagedAction.id.desc())
        .limit(DIGEST_ITEM_CAP)
        .all()
    ] + [
        {
            "what": f"message to {m.recipient}",
            "cancelled_at": _when(m.cancelled_at),
            "cancelled_by": None,
        }
        for m in db.query(OutboxMessage)
        .filter(OutboxMessage.status == "cancelled")
        .filter(OutboxMessage.cancelled_at >= cutoff)
        .order_by(OutboxMessage.cancelled_at.desc(), OutboxMessage.id.desc())
        .limit(DIGEST_ITEM_CAP)
        .all()
    ]

    outbox_queued = [
        {
            "recipient": m.recipient,
            "body": _preview(m.body),
            "queued_at": _when(m.created_at),
        }
        for m in db.query(OutboxMessage)
        .filter(OutboxMessage.status == "queued_local")
        .order_by(OutboxMessage.created_at.desc(), OutboxMessage.id.desc())
        .limit(DIGEST_ITEM_CAP)
        .all()
    ]

    escalation_candidates = [
        {"reason": e.reason, "created_at": _when(e.created_at)}
        for e in get_open_escalations(db)
        if e.reason.startswith(CANDIDATE_REASON_PREFIX)
    ][:DIGEST_ITEM_CAP]

    failed_actions = [
        {
            "action_type": a.action_type,
            "subject": _payload(a.action_payload).get("subject"),
            "result": _preview(a.execution_result),
        }
        for a in db.query(StagedAction)
        .filter(StagedAction.status == "failed")
        .order_by(StagedAction.id.desc())
        .limit(DIGEST_ITEM_CAP)
        .all()
    ]

    waiting_for_confirmation = [
        {
            "what": _describe_action(a),
            "status": a.status,
            "resurface_count": a.resurface_count,
        }
        for a in db.query(StagedAction)
        .filter(StagedAction.status.in_(["staged", "confirmed"]))
        .order_by(StagedAction.execute_after, StagedAction.created_at, StagedAction.id)
        .limit(DIGEST_ITEM_CAP)
        .all()
    ]

    what_happened = {
        "reminders_done": reminders_done,
        "messages_released": messages_released,
        "messages_approved": messages_approved,
        "exercise_sessions": exercise_sessions,
        "evening_sessions": evening_sessions,
        "other_done": other_done,
        "changed_mind": changed_mind,
    }
    needs_review = {
        "outbox_queued": outbox_queued,
        "escalation_candidates": escalation_candidates,
        "failed_actions": failed_actions,
        "waiting_for_confirmation": waiting_for_confirmation,
    }
    return {
        "date": current.strftime("%Y-%m-%d"),
        "generated_at": current.strftime("%Y-%m-%d %H:%M"),
        "window_hours": window_hours,
        "patient_name": settings.patient_name,
        "what_happened": what_happened,
        "needs_review": needs_review,
        "stayed_local": {
            "happened_count": sum(len(items) for items in what_happened.values()),
            "open_count": sum(len(items) for items in needs_review.values()),
        },
    }


def _describe_action(action: StagedAction) -> str:
    payload = _payload(action.action_payload)
    subject = payload.get("subject") or action.action_type
    if action.action_type == "family_message":
        recipient = payload.get("recipient") or "family"
        return f"message to {recipient} — “{_preview(payload.get('intent_text'))}”"
    if action.action_type == "reminder":
        return f"reminder “{subject}”"
    if action.action_type == "exercise_start":
        return f"exercise “{subject}”"
    return f"{action.action_type} “{subject}”"


def render_digest_markdown(digest: dict[str, Any]) -> str:
    """Render the structured digest as a family-readable markdown artifact."""

    happened = digest["what_happened"]
    review = digest["needs_review"]
    name = digest["patient_name"]

    lines: list[str] = [
        f"# Parker family digest — {digest['date']}",
        "",
        f"For {name}'s family. Generated on this machine at "
        f"{digest['generated_at']} UTC from Parker's local records "
        f"(last {digest['window_hours']} hours, plus anything still open).",
        "Nothing in this digest was sent anywhere — Parker v0 has no send path.",
        "This is the rearview mirror: awareness for the family, not an approval queue.",
        "",
        "## What happened",
        "",
    ]

    happened_lines: list[str] = []
    for item in happened["reminders_done"]:
        happened_lines.append(
            f"- {item['executed_at']} — Reminder done: “{item['subject']}” "
            f"(confirmed by {item['confirmed_by'] or '—'})."
        )
    for item in happened["messages_released"]:
        happened_lines.append(
            f"- {item['released_at']} — Message to {item['recipient']}: “{item['body']}” "
            f"— released to family contacts on {name}'s own confirmation "
            "(capability policy; still on this machine)."
        )
    for item in happened["messages_approved"]:
        happened_lines.append(
            f"- {item['approved_at']} — Message to {item['recipient']}: “{item['body']}” "
            f"— approved by {item['approved_by'] or 'caregiver'} (still on this machine)."
        )
    for item in happened["exercise_sessions"]:
        happened_lines.append(
            f"- {item['started_at']} — Exercise “{item['subject']}” — {item['status']}."
        )
    for item in happened["evening_sessions"]:
        happened_lines.append(
            f"- {item['started_at']} — Evening check-in ({item['evening_date']}) "
            f"— {item['status']}."
        )
    for item in happened["other_done"]:
        happened_lines.append(
            f"- {item['executed_at']} — {item['action_type']} “{item['subject']}” done"
            + (f": {item['result']}." if item["result"] else ".")
        )
    for item in happened["changed_mind"]:
        happened_lines.append(
            f"- {item['cancelled_at']} — Changed their mind: {item['what']} cancelled"
            + (f" by {item['cancelled_by']}." if item["cancelled_by"] else ".")
        )
    lines.extend(happened_lines or ["A quiet day — nothing recorded in this window."])

    lines.extend(["", "## Needs a look", ""])
    review_lines: list[str] = []
    for item in review["outbox_queued"]:
        review_lines.append(
            f"- Message to {item['recipient']} waiting for caregiver approval "
            f"(recipient not on the family-contacts allowlist): “{item['body']}” "
            f"(queued {item['queued_at']})."
        )
    for item in review["escalation_candidates"]:
        review_lines.append(
            f"- {item['reason']} Review only — no notification was dispatched."
        )
    for item in review["failed_actions"]:
        review_lines.append(
            f"- Failed and not retried: {item['action_type']} “{item['subject']}”"
            + (f" — {item['result']}." if item["result"] else ".")
        )
    for item in review["waiting_for_confirmation"]:
        resurfaced = (
            f" (brought up {item['resurface_count']}x so far)"
            if item["resurface_count"]
            else ""
        )
        review_lines.append(f"- Waiting for a confirmation: {item['what']}{resurfaced}.")
    lines.extend(review_lines or ["Nothing needs a decision right now."])

    lines.extend(
        [
            "",
            "## What stayed local",
            "",
            f"Everything above — {digest['stayed_local']['happened_count']} recorded "
            f"event(s) and {digest['stayed_local']['open_count']} open item(s) — lives "
            "in the local Parker database on this machine. Released and approved "
            "messages alike have no send transport in v0; this digest itself is a "
            "local file that goes nowhere unless the family shares it by hand.",
            "",
        ]
    )
    return "\n".join(lines)


DIGEST_PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Parker — family digest</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; margin: 2rem auto; max-width: 880px; padding: 0 1rem; color: #1a1a2e; }}
  pre {{ white-space: pre-wrap; font: inherit; line-height: 1.5; }}
  .note {{ background: #f4f7f4; border-radius: 8px; padding: .6rem 1rem; font-size: .85rem; }}
</style>
</head>
<body>
<p class="note">Local artifact — regenerated on each visit, never sent anywhere.
<a href="/parker/review/ui">Back to caregiver review</a></p>
<pre>{content}</pre>
</body>
</html>
"""


def render_digest_page(db: Session, *, now: Optional[datetime] = None) -> str:
    """The review-UI-linked HTML view of the current digest."""

    text = render_digest_markdown(build_digest(db, now=now))
    return DIGEST_PAGE_TEMPLATE.format(content=html.escape(text))


def default_digest_dir() -> Path:
    # PARKER_HOME/digests — backend/digests/ in a dev checkout (gitignored),
    # ~/Library/Application Support/Parker/digests in the desktop app.
    from app import paths

    return paths.digests_dir()


def write_digest_file(
    text: str, *, date: str, directory: Optional[Path] = None
) -> Path:
    target_dir = directory or default_digest_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"parker-digest-{date}.md"
    path.write_text(text, encoding="utf-8")
    return path


def main() -> None:  # pragma: no cover — CLI entry point (`make digest`)
    from app.db.database import SessionLocal, create_tables

    create_tables()
    db = SessionLocal()
    digest = build_digest(db)
    text = render_digest_markdown(digest)
    db.close()
    path = write_digest_file(text, date=digest["date"])
    print(text)
    print(f"Written to {path} — a local file; nothing was sent.")


if __name__ == "__main__":  # pragma: no cover
    main()
