"""Local caregiver handoff cards for user-confirmed read-only research queries.

A card is created only after the user first repairs an n-best informational
query and then explicitly confirms that Parker may leave the selected query for
family review. The row is local, contains no URL/credential/payment field, and
has no browser, send, purchase, submission, or account-change capability.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db.database import Base

SOURCE_KIND = "local_asr_nbest_repair"
PROVENANCE_STATUS = "user_confirmed_interpretation_no_external_source_fetched"
RISK_LABEL = "read_only_research_no_external_action"
SUPPORTED_REPAIR_FAMILIES = frozenset({"weather_place", "person_entity"})


class LocalResearchHandoff(Base):
    """A local, caregiver-visible research request with terminal controls."""

    __tablename__ = "local_research_handoffs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_log_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("call_logs.id"), nullable=True, index=True
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


def create_local_research_handoff(
    db: Session,
    *,
    call_log_id: int | None,
    query: str,
    selected_interpretation: str,
    repair_family: str,
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
    card = LocalResearchHandoff(
        call_log_id=call_log_id,
        query=normalized_query,
        selected_interpretation=normalized_selection[:256],
        repair_family=repair_family,
        source_kind=SOURCE_KIND,
        provenance_status=PROVENANCE_STATUS,
        risk_label=RISK_LABEL,
        status="ready",
        created_at=_coerce_datetime(now) or datetime.utcnow(),
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
    """Mark a ready card complete; terminal rows are never rewritten."""

    card = db.get(LocalResearchHandoff, handoff_id)
    if card is None:
        return None
    if card.status == "ready":
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
    }


def _coerce_datetime(value: datetime | str | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)
