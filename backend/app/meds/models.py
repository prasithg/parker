"""Pydantic schemas for medication and call summaries."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class MedicationConfig(BaseModel):
    """Configuration for a single medication."""

    name: str
    dosage: str
    schedule_times: list[str]
    active: bool = True
    instructions: str = ""


class AdherenceStats(BaseModel):
    """Medication adherence metrics."""

    medication_id: int
    medication_name: str
    total_scheduled: int
    total_confirmed: int
    adherence_rate: float = Field(ge=0.0, le=1.0)
    last_7_days_rate: float = Field(ge=0.0, le=1.0)


class MedicationSchedule(BaseModel):
    """Full schedule of all active medications."""

    medications: list[MedicationConfig] = Field(default_factory=list)

    def all_times(self) -> list[str]:
        """Return deduplicated sorted list of all scheduled times."""
        times = set()
        for med in self.medications:
            if med.active:
                times.update(med.schedule_times)
        return sorted(times)

    def meds_at_time(self, time_str: str) -> list[MedicationConfig]:
        """Return medications scheduled at a specific time."""
        return [m for m in self.medications if m.active and time_str in m.schedule_times]


class CallSummary(BaseModel):
    """Summary data for a completed call."""

    call_id: int
    started_at: datetime | None
    duration_seconds: int | None
    summary: str | None
    mood: str | None
    dose_logs: list[dict[str, Any]] = Field(default_factory=list)
