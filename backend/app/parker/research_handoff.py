"""Local caregiver handoff cards for user-confirmed read-only research queries.

A card is created only after the user first repairs an n-best informational
query and then explicitly confirms that Parker may leave the selected query for
family review. The row is local, contains no URL/credential/payment field, and
has no browser, send, purchase, submission, or account-change capability.

Query text has a bounded local retention window. Expiry or an authenticated
caregiver redaction clears the query, selected ASR interpretation, and any linked
consented repair-event text while preserving non-sensitive lifecycle,
provenance, and redaction audit fields.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, inspect, text
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db.database import Base

# Register the foreign-key target whenever this model is imported. Without this,
# isolated test/module loads can call Base.metadata.create_all before the repair
# event model has populated the shared metadata.
from app.conversation import repair_events as _repair_events  # noqa: F401, E402

SOURCE_KIND = "local_asr_nbest_repair"
PROVENANCE_STATUS = "user_confirmed_interpretation_no_external_source_fetched"
RISK_LABEL = "read_only_research_no_external_action"
SUPPORTED_REPAIR_FAMILIES = frozenset({"weather_place", "person_entity"})
DEFAULT_QUERY_RETENTION_DAYS = 30
REDACTED_TEXT = "[redacted]"
MANUAL_REDACTION_REASON = "manual_caregiver_redaction"
RETENTION_REDACTION_REASON = "retention_window_expired"
PRIVACY_SCHEMA_COLUMNS = {
    "repair_event_id": "INTEGER",
    "retention_expires_at": "DATETIME",
    "redacted_at": "DATETIME",
    "redacted_by": "VARCHAR(64)",
    "redaction_reason": "VARCHAR(64)",
}


class LocalResearchHandoff(Base):
    """A local, caregiver-visible research request with terminal controls."""

    __tablename__ = "local_research_handoffs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_log_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("call_logs.id"), nullable=True, index=True
    )
    repair_event_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("repair_events.id"), nullable=True, index=True
    )
    query: Mapped[str] = mapped_column(Text)
    selected_interpretation: Mapped[str] = mapped_column(String(256))
    repair_family: Mapped[str] = mapped_column(String(64), index=True)
    source_kind: Mapped[str] = mapped_column(String(64), default=SOURCE_KIND)
    provenance_status: Mapped[str] = mapped_column(String(128), default=PROVENANCE_STATUS)
    risk_label: Mapped[str] = mapped_column(String(128), default=RISK_LABEL)
    status: Mapped[str] = mapped_column(String(16), default="ready", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    retention_expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    redacted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    redacted_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    redaction_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)


def ensure_local_research_handoff_privacy_schema(bind) -> None:
    """Add/backfill the v0 privacy columns without deleting existing local data.

    Parker remains SQLite/create-all based, but ``create_all`` cannot alter the
    research-handoff table introduced by the previous stacked slice. This small,
    additive migration is intentionally scoped to nullable columns, followed by
    a deterministic retention backfill. It never resets or copies the database.
    """

    inspector = inspect(bind)
    if "local_research_handoffs" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("local_research_handoffs")}
    missing = [name for name in PRIVACY_SCHEMA_COLUMNS if name not in existing]
    if missing and bind.dialect.name != "sqlite":
        raise RuntimeError("research-handoff privacy migration currently supports SQLite only")
    with bind.begin() as connection:
        for name in missing:
            connection.execute(
                text(
                    f"ALTER TABLE local_research_handoffs "
                    f"ADD COLUMN {name} {PRIVACY_SCHEMA_COLUMNS[name]}"
                )
            )
        connection.execute(
            text(
                "UPDATE local_research_handoffs "
                "SET retention_expires_at = datetime(created_at, '+30 days') "
                "WHERE retention_expires_at IS NULL"
            )
        )
        if "repair_events" in inspect(connection).get_table_names():
            connection.execute(
                text(
                    "UPDATE local_research_handoffs "
                    "SET repair_event_id = ("
                    "SELECT MAX(repair_events.id) FROM repair_events "
                    "WHERE repair_events.call_log_id = local_research_handoffs.call_log_id "
                    "AND repair_events.selected_label = "
                    "local_research_handoffs.selected_interpretation"
                    ") WHERE repair_event_id IS NULL AND call_log_id IS NOT NULL"
                )
            )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_local_research_handoffs_retention_expires_at "
                "ON local_research_handoffs (retention_expires_at)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_local_research_handoffs_redacted_at "
                "ON local_research_handoffs (redacted_at)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_local_research_handoffs_repair_event_id "
                "ON local_research_handoffs (repair_event_id)"
            )
        )


def create_local_research_handoff(
    db: Session,
    *,
    call_log_id: int | None,
    query: str,
    selected_interpretation: str,
    repair_family: str,
    repair_event_id: int | None = None,
    now: datetime | str | None = None,
) -> LocalResearchHandoff:
    """Create one local card after explicit user confirmation.

    Only the two reviewed, read-only informational repair families may use this
    seam. This prevents it from becoming a generic action or web-agent escape
    hatch.
    """

    normalized_query = query.strip()
    normalized_selection = selected_interpretation.strip()
    if not normalized_query or not normalized_selection:
        raise ValueError("research handoff needs a query and selected interpretation")
    if repair_family not in SUPPORTED_REPAIR_FAMILIES:
        raise ValueError(f"unsupported research handoff repair family: {repair_family}")
    created_at = _coerce_datetime(now) or datetime.utcnow()
    card = LocalResearchHandoff(
        call_log_id=call_log_id,
        repair_event_id=repair_event_id,
        query=normalized_query,
        selected_interpretation=normalized_selection[:256],
        repair_family=repair_family,
        source_kind=SOURCE_KIND,
        provenance_status=PROVENANCE_STATUS,
        risk_label=RISK_LABEL,
        status="ready",
        created_at=created_at,
        retention_expires_at=created_at + timedelta(days=DEFAULT_QUERY_RETENTION_DAYS),
    )
    db.add(card)
    db.commit()
    db.refresh(card)
    return card


def list_recent_local_research_handoffs(
    db: Session, *, limit: int = 10
) -> list[LocalResearchHandoff]:
    """Return recent cards for the authenticated caregiver surface."""

    return (
        db.query(LocalResearchHandoff)
        .order_by(LocalResearchHandoff.created_at.desc(), LocalResearchHandoff.id.desc())
        .limit(limit)
        .all()
    )


def complete_local_research_handoff(
    db: Session,
    handoff_id: int,
    *,
    completed_by: str = "caregiver",
    now: datetime | str | None = None,
) -> LocalResearchHandoff | None:
    """Mark a ready, readable card complete; terminal/redacted rows stay unchanged."""

    card = db.get(LocalResearchHandoff, handoff_id)
    if card is None:
        return None
    if card.status == "ready" and card.redacted_at is None:
        card.status = "completed"
        card.completed_at = _coerce_datetime(now) or datetime.utcnow()
        card.completed_by = completed_by.strip() or "caregiver"
        db.commit()
        db.refresh(card)
    return card


def cancel_local_research_handoff(
    db: Session,
    handoff_id: int,
    *,
    cancelled_by: str = "caregiver",
    now: datetime | str | None = None,
) -> LocalResearchHandoff | None:
    """Cancel a ready card; terminal rows are never rewritten."""

    card = db.get(LocalResearchHandoff, handoff_id)
    if card is None:
        return None
    if card.status == "ready":
        card.status = "cancelled"
        card.cancelled_at = _coerce_datetime(now) or datetime.utcnow()
        card.cancelled_by = cancelled_by.strip() or "caregiver"
        db.commit()
        db.refresh(card)
    return card


def redact_local_research_handoff(
    db: Session,
    handoff_id: int,
    *,
    redacted_by: str = "caregiver",
    now: datetime | str | None = None,
) -> LocalResearchHandoff | None:
    """Remove sensitive query text while preserving non-sensitive lifecycle audit.

    Redaction is local, irreversible, and idempotent. It clears both the
    normalized query and the selected ASR interpretation so the alternate name
    or place cannot leak after the visible query is removed.
    """

    card = db.get(LocalResearchHandoff, handoff_id)
    if card is None:
        return None
    if card.redacted_at is None:
        _redact_card(
            db,
            card,
            redacted_by=redacted_by.strip() or "caregiver",
            reason=MANUAL_REDACTION_REASON,
            now=_coerce_datetime(now) or datetime.utcnow(),
        )
        db.commit()
        db.refresh(card)
    return card


def redact_expired_local_research_handoffs(
    db: Session,
    *,
    now: datetime | str | None = None,
) -> list[LocalResearchHandoff]:
    """Apply the default retention policy to any due, non-redacted cards."""

    moment = _coerce_datetime(now) or datetime.utcnow()
    cards = (
        db.query(LocalResearchHandoff)
        .filter(LocalResearchHandoff.redacted_at.is_(None))
        .filter(LocalResearchHandoff.retention_expires_at <= moment)
        .order_by(LocalResearchHandoff.retention_expires_at, LocalResearchHandoff.id)
        .all()
    )
    for card in cards:
        _redact_card(
            db,
            card,
            redacted_by="retention_policy",
            reason=RETENTION_REDACTION_REASON,
            now=moment,
        )
    if cards:
        db.commit()
        for card in cards:
            db.refresh(card)
    return cards


def run_research_handoff_retention(db_session_factory) -> int:
    """Run one bounded retention sweep with an owned database session."""

    db = db_session_factory()
    try:
        return len(redact_expired_local_research_handoffs(db))
    finally:
        db.close()


def serialize_local_research_handoff(card: LocalResearchHandoff) -> dict[str, object]:
    """Public caregiver-feed shape; intentionally has no URL or credential fields."""

    return {
        "id": card.id,
        "query": card.query,
        "selected_interpretation": card.selected_interpretation,
        "repair_family": card.repair_family,
        "source_kind": card.source_kind,
        "provenance_status": card.provenance_status,
        "risk_label": card.risk_label,
        "status": card.status,
        "created_at": card.created_at.isoformat() if card.created_at else None,
        "completed_at": card.completed_at.isoformat() if card.completed_at else None,
        "cancelled_at": card.cancelled_at.isoformat() if card.cancelled_at else None,
        "completed_by": card.completed_by,
        "cancelled_by": card.cancelled_by,
        "retention_expires_at": (
            card.retention_expires_at.isoformat() if card.retention_expires_at else None
        ),
        "query_redacted": card.redacted_at is not None,
        "redacted_at": card.redacted_at.isoformat() if card.redacted_at else None,
        "redacted_by": card.redacted_by,
        "redaction_reason": card.redaction_reason,
    }


def _redact_card(
    db: Session,
    card: LocalResearchHandoff,
    *,
    redacted_by: str,
    reason: str,
    now: datetime,
) -> None:
    card.query = REDACTED_TEXT
    card.selected_interpretation = REDACTED_TEXT
    card.redacted_at = now
    card.redacted_by = redacted_by
    card.redaction_reason = reason
    if card.repair_event_id is not None:
        from app.conversation.repair_events import RepairEvent

        event = db.get(RepairEvent, card.repair_event_id)
        if event is not None:
            event.utterance = REDACTED_TEXT
            event.alternates_json = "[]"
            event.offered_choices_json = "[]"
            event.selected_label = REDACTED_TEXT


def _coerce_datetime(value: datetime | str | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)
