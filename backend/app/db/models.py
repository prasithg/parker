"""SQLAlchemy models for ParkinsClaw."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class CallLog(Base):
    __tablename__ = "call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_sid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    call_type: Mapped[str] = mapped_column(String(32))  # check_in, med_reminder, evening_chat
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    patient_mood: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    dose_logs: Mapped[list["DoseLog"]] = relationship(back_populates="call_log")
    mood_entries: Mapped[list["MoodEntry"]] = relationship(back_populates="call_log")
    escalations: Mapped[list["Escalation"]] = relationship("Escalation", back_populates="call_log")


class Medication(Base):
    __tablename__ = "medications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    dosage: Mapped[str] = mapped_column(String(64))
    schedule_times: Mapped[str] = mapped_column(Text)  # JSON array of HH:MM strings
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class DoseLog(Base):
    __tablename__ = "dose_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_log_id: Mapped[int] = mapped_column(Integer, ForeignKey("call_logs.id"))
    medication_id: Mapped[int] = mapped_column(Integer, ForeignKey("medications.id"))
    scheduled_time: Mapped[str] = mapped_column(String(8))  # HH:MM
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    patient_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    call_log: Mapped["CallLog"] = relationship(back_populates="dose_logs")
    verifications: Mapped[list["DoseVerification"]] = relationship(back_populates="dose")


class DoseVerification(Base):
    __tablename__ = "dose_verifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dose_id: Mapped[int] = mapped_column(Integer, ForeignKey("dose_logs.id"), index=True)
    verification_type: Mapped[str] = mapped_column(
        Enum(
            "photo",
            "text",
            "caregiver_attested",
            name="dose_verification_type",
            native_enum=False,
        ),
        index=True,
    )
    image_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    text_attestation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    status: Mapped[str] = mapped_column(
        Enum(
            "pending",
            "verified",
            "missed",
            name="dose_verification_status",
            native_enum=False,
        ),
        default="pending",
        index=True,
    )

    dose: Mapped["DoseLog"] = relationship(back_populates="verifications")


class MoodEntry(Base):
    __tablename__ = "mood_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_log_id: Mapped[int] = mapped_column(Integer, ForeignKey("call_logs.id"))
    mood: Mapped[str] = mapped_column(String(32))  # good, okay, low, etc.
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    call_log: Mapped["CallLog"] = relationship(back_populates="mood_entries")
