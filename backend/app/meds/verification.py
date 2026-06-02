"""Dose verification workflow and missed-dose escalation processing."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import CallLog, DoseLog, DoseVerification
from app.escalation.engine import create_escalation

VERIFICATION_TYPES = {"photo", "text", "caregiver_attested"}
VERIFICATION_STATUSES = {"pending", "verified", "missed"}


def open_verification_window(
    db: Session,
    dose_id: int,
    window_opened_at: datetime | None = None,
) -> DoseVerification | None:
    """Create a pending verification marker for scheduler-driven expiry checks."""

    dose = db.get(DoseLog, dose_id)
    if dose is None or dose.confirmed:
        return None

    existing = (
        db.query(DoseVerification)
        .filter(DoseVerification.dose_id == dose_id)
        .filter(DoseVerification.status == "pending")
        .first()
    )
    if existing:
        return existing

    marker = DoseVerification(
        dose_id=dose_id,
        verification_type="text",
        status="pending",
        timestamp=window_opened_at or datetime.utcnow(),
    )
    db.add(marker)
    db.commit()
    db.refresh(marker)
    return marker


def create_dose_verification(
    db: Session,
    dose_id: int,
    verification_type: str,
    image_path: str | None = None,
    text_attestation: str | None = None,
    status: str = "verified",
    timestamp: datetime | None = None,
) -> DoseVerification | None:
    """Persist a verification and mark the dose confirmed when verified."""

    if verification_type not in VERIFICATION_TYPES:
        raise ValueError(f"Invalid verification_type: {verification_type}")
    if status not in VERIFICATION_STATUSES:
        raise ValueError(f"Invalid status: {status}")

    dose = db.get(DoseLog, dose_id)
    if dose is None:
        return None

    verification = DoseVerification(
        dose_id=dose_id,
        verification_type=verification_type,
        image_path=image_path,
        text_attestation=text_attestation,
        status=status,
        timestamp=timestamp or datetime.utcnow(),
    )
    db.add(verification)
    if status == "verified":
        dose.confirmed = True
        dose.confirmed_at = verification.timestamp
    db.commit()
    db.refresh(verification)
    return verification


def list_dose_verifications(db: Session, dose_id: int) -> list[DoseVerification] | None:
    """Return verifications for a dose, oldest first."""

    if db.get(DoseLog, dose_id) is None:
        return None
    return (
        db.query(DoseVerification)
        .filter(DoseVerification.dose_id == dose_id)
        .order_by(DoseVerification.timestamp.asc(), DoseVerification.id.asc())
        .all()
    )


def process_due_verification_windows(
    db: Session,
    now: datetime | None = None,
    window_minutes: int | None = None,
) -> list[Any]:
    """Escalate pending verification windows that expired without verification.

    This is intentionally test-friendly and scheduler-callable; it does not
    register or start any live cron by itself.
    """

    current = now or datetime.utcnow()
    minutes = window_minutes if window_minutes is not None else settings.dose_verification_window_minutes
    cutoff = current - timedelta(minutes=minutes)
    pending = (
        db.query(DoseVerification)
        .join(DoseLog, DoseVerification.dose_id == DoseLog.id)
        .join(CallLog, DoseLog.call_log_id == CallLog.id)
        .filter(DoseVerification.status == "pending")
        .filter(DoseVerification.timestamp <= cutoff)
        .filter(DoseLog.confirmed.is_(False))
        .filter(CallLog.call_type == "med_reminder")
        .all()
    )

    escalations = []
    for verification in pending:
        has_verified = (
            db.query(DoseVerification.id)
            .filter(DoseVerification.dose_id == verification.dose_id)
            .filter(DoseVerification.status == "verified")
            .first()
            is not None
        )
        if has_verified:
            verification.status = "verified"
            continue

        verification.status = "missed"
        verification.text_attestation = "Verification window expired without dose confirmation."
        db.commit()
        escalation = create_escalation(
            db,
            call_log_id=verification.dose.call_log_id,
            reason=f"Missed dose verification window for dose {verification.dose_id}",
            severity="missed-dose",
        )
        escalations.append(escalation)

    db.commit()
    return escalations


def serialize_verification(verification: DoseVerification) -> dict[str, Any]:
    """Serialize dose verification rows for API responses."""

    return {
        "id": verification.id,
        "dose_id": verification.dose_id,
        "verification_type": verification.verification_type,
        "image_path": verification.image_path,
        "text_attestation": verification.text_attestation,
        "timestamp": verification.timestamp.isoformat() if verification.timestamp else None,
        "status": verification.status,
    }
