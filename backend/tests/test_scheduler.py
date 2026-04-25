"""Tests for call scheduler logic."""

import json
from datetime import datetime, time

from app.db.models import Medication
from app.calls.scheduler import (
    build_call_schedule,
    compute_call_schedule,
    _is_quiet_hours,
    is_quiet_hours,
    should_retry,
)


class TestIsQuietHours:
    def test_before_8am_is_quiet(self):
        assert is_quiet_hours(time(7, 30)) is True

    def test_after_9pm_is_quiet(self):
        assert is_quiet_hours(time(21, 30)) is True

    def test_midday_is_not_quiet(self):
        assert is_quiet_hours(time(12, 0)) is False

    def test_boundary_8am_not_quiet(self):
        assert is_quiet_hours(time(8, 0)) is False

    def test_boundary_9pm_not_quiet(self):
        assert is_quiet_hours(time(21, 0)) is False

    def test_private_helper_accepts_datetime(self):
        assert _is_quiet_hours(datetime(2026, 4, 25, 7, 59)) is True
        assert _is_quiet_hours(datetime(2026, 4, 25, 8, 0)) is False


class TestShouldRetry:
    def test_first_attempt_should_retry(self):
        assert should_retry(attempt=0) is True

    def test_second_attempt_should_retry(self):
        assert should_retry(attempt=1) is True

    def test_third_attempt_should_not_retry(self):
        assert should_retry(attempt=2) is False


class TestBuildCallSchedule:
    def test_includes_morning_checkin(self):
        med_times = ["08:00", "14:00"]
        schedule = build_call_schedule(med_times)
        types = [s["call_type"] for s in schedule]
        assert "check_in" in types

    def test_includes_evening_chat(self):
        schedule = build_call_schedule([])
        types = [s["call_type"] for s in schedule]
        assert "evening_chat" in types

    def test_includes_med_reminders(self):
        schedule = build_call_schedule(["08:00", "14:00"])
        med_reminders = [s for s in schedule if s["call_type"] == "med_reminder"]
        assert len(med_reminders) == 2

    def test_schedule_times_are_valid(self):
        schedule = build_call_schedule(["09:00"])
        for entry in schedule:
            h, m = entry["time"].split(":")
            assert 0 <= int(h) <= 23
            assert 0 <= int(m) <= 59


class TestComputeCallSchedule:
    def test_compute_includes_deduped_med_reminders(self, db):
        db.add_all(
            [
                Medication(
                    name="Levodopa",
                    dosage="100mg",
                    schedule_times=json.dumps(["08:00", "14:00"]),
                    active=True,
                ),
                Medication(
                    name="Sinemet",
                    dosage="25/100",
                    schedule_times=json.dumps(["08:00"]),
                    active=True,
                ),
            ]
        )
        db.commit()

        schedule = compute_call_schedule(db)
        med_reminders = [s for s in schedule if s["call_type"] == "med_reminder"]
        assert len(med_reminders) == 2
        first = next(s for s in med_reminders if s["time"] == "07:45")
        assert len(first["medication_ids"]) == 2
        assert {"check_in", "evening_chat"} <= {s["call_type"] for s in schedule}
