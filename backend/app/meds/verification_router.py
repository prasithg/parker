"""FastAPI routes for dose verification."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import DoseLog
from app.meds.verification import (
    create_dose_verification,
    list_dose_verifications,
    serialize_verification,
)

router = APIRouter()


class DoseVerificationRequest(BaseModel):
    dose_id: int | None = None
    verification_type: Literal["photo", "text", "caregiver_attested"]
    image_path: str | None = None
    text_attestation: str | None = None
    timestamp: datetime | None = None
    status: Literal["pending", "verified", "missed"] = "verified"


@router.post("/calls/{call_id}/verify-dose")
def verify_dose_for_call(
    call_id: int,
    request: DoseVerificationRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Create a verification for a dose associated with a call."""

    dose = _resolve_call_dose(db, call_id, request.dose_id)
    verification = create_dose_verification(
        db,
        dose_id=dose.id,
        verification_type=request.verification_type,
        image_path=request.image_path,
        text_attestation=request.text_attestation,
        status=request.status,
        timestamp=request.timestamp,
    )
    if verification is None:
        raise HTTPException(status_code=404, detail="Dose not found")
    return serialize_verification(verification)


@router.post("/doses/{dose_id}/verifications")
def create_verification_for_dose(
    dose_id: int,
    request: DoseVerificationRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Create a verification directly for a dose."""

    verification = create_dose_verification(
        db,
        dose_id=dose_id,
        verification_type=request.verification_type,
        image_path=request.image_path,
        text_attestation=request.text_attestation,
        status=request.status,
        timestamp=request.timestamp,
    )
    if verification is None:
        raise HTTPException(status_code=404, detail="Dose not found")
    return serialize_verification(verification)


@router.get("/doses/{dose_id}/verifications")
def get_verifications_for_dose(
    dose_id: int,
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """List verifications for a dose."""

    verifications = list_dose_verifications(db, dose_id)
    if verifications is None:
        raise HTTPException(status_code=404, detail="Dose not found")
    return [serialize_verification(item) for item in verifications]


def _resolve_call_dose(db: Session, call_id: int, dose_id: int | None) -> DoseLog:
    query = db.query(DoseLog).filter(DoseLog.call_log_id == call_id)
    if dose_id is not None:
        dose = query.filter(DoseLog.id == dose_id).first()
        if dose is None:
            raise HTTPException(status_code=404, detail="Dose not found for call")
        return dose

    doses = query.order_by(DoseLog.id.asc()).limit(2).all()
    if not doses:
        raise HTTPException(status_code=404, detail="Dose not found for call")
    if len(doses) > 1:
        raise HTTPException(status_code=400, detail="dose_id is required when a call has multiple doses")
    return doses[0]
