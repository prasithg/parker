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


class CapturedIntent(Base):
    __tablename__ = "captured_intents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_log_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("call_logs.id"), nullable=True)
    intent_text: Mapped[str] = mapped_column(Text)
    requested_action: Mapped[str] = mapped_column(String(64), default="remind", index=True)
    subject: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    # Family/caregiver contact name for message intents; resolved against
    # configured contacts at execution time, never a raw phone number.
    recipient: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    status: Mapped[str] = mapped_column(
        Enum("pending", "resolved", "rejected", name="captured_intent_status", native_enum=False),
        default="pending",
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    call_log: Mapped[Optional["CallLog"]] = relationship()
    resolutions: Mapped[list["ResolutionResult"]] = relationship(back_populates="captured_intent")


class ResolutionResult(Base):
    __tablename__ = "resolution_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    captured_intent_id: Mapped[int] = mapped_column(Integer, ForeignKey("captured_intents.id"), index=True)
    status: Mapped[str] = mapped_column(
        Enum("resolved", "rejected", "staged", name="resolution_result_status", native_enum=False),
        default="resolved",
        index=True,
    )
    action_type: Mapped[str] = mapped_column(String(64), index=True)
    reversible: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    summary: Mapped[str] = mapped_column(Text)
    execute_after: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    captured_intent: Mapped["CapturedIntent"] = relationship(back_populates="resolutions")
    staged_actions: Mapped[list["StagedAction"]] = relationship(back_populates="resolution_result")


class StagedAction(Base):
    __tablename__ = "staged_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    resolution_result_id: Mapped[int] = mapped_column(Integer, ForeignKey("resolution_results.id"), index=True)
    status: Mapped[str] = mapped_column(
        Enum(
            "staged",
            "confirmed",
            "blocked",
            "executed",
            "cancelled",
            name="staged_action_status",
            native_enum=False,
        ),
        default="staged",
        index=True,
    )
    action_type: Mapped[str] = mapped_column(String(64), index=True)
    action_payload: Mapped[str] = mapped_column(Text, default="{}")
    reversible: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    execute_after: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    last_resurfaced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    resurface_count: Mapped[int] = mapped_column(Integer, default=0)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    confirmed_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    execution_result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Set when a non-response escalation candidate was raised for this action
    # (at most one per staged action).
    escalation_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("escalations.id"), nullable=True
    )

    resolution_result: Mapped["ResolutionResult"] = relationship(back_populates="staged_actions")
    outbox_messages: Mapped[list["OutboxMessage"]] = relationship(back_populates="staged_action")


class OutboxMessage(Base):
    """A confirmed family message queued locally.

    v0 has no send path: executing a confirmed family_message action creates
    a row with status ``queued_local`` and stops there. Real delivery would
    require a future, explicitly approved sender that flips status to
    ``sent`` — that code intentionally does not exist yet. Rows are
    cancellable, which is what makes the v0 execution artifact reversible.
    """

    __tablename__ = "outbox_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    staged_action_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("staged_actions.id"), index=True
    )
    recipient: Mapped[str] = mapped_column(String(128))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        Enum(
            "queued_local",
            "cancelled",
            "sent",
            name="outbox_message_status",
            native_enum=False,
        ),
        default="queued_local",
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    staged_action: Mapped["StagedAction"] = relationship(back_populates="outbox_messages")
