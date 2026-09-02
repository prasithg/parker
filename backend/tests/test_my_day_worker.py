"""The my_day worker is date-grounded: his LOCAL day, from what is due, not
from what he set recently (P0.3, fresh review of the my_day slice).

The review repro: an old reminder due today was dropped and a fresh one
due next month was spoken, because selection was `created_at >= now-2d`
and `execute_after` was never read. Notes were narrated as present-tense
family plans no matter how old they were. Every test injects the clock
(`now=`) and the home timezone so the pins are independent of the
machine's date and zone; storage is naive UTC, as in production.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import CallLog, CapturedIntent, Medication, ResolutionResult, StagedAction
from app.memory.store import save_memory
from app.parker import realtime_workers
from app.parker.realtime_workers import MY_DAY_LIMIT_LINE, run_my_day_worker

TZ = timezone(timedelta(hours=-4), "EDT")
NOW = datetime(2026, 9, 2, 15, 0, tzinfo=TZ)  # a Wednesday afternoon


@pytest.fixture(autouse=True)
def _home_timezone(monkeypatch):
    # realtime_workers imports home_timezone function-locally from rollup.
    monkeypatch.setattr("app.parker.rollup.home_timezone", lambda: TZ)


def _stored(moment: datetime) -> datetime:
    """Aware instant → the naive-UTC form the DateTime columns hold."""

    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def _local(year, month, day, hour, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=TZ)


def _reminder(db, subject, *, status, created, due=None):
    """One reminder as the pipeline stores it: intent → resolution → staged action."""

    call = db.query(CallLog).first()
    if call is None:
        call = CallLog(call_sid="P03", call_type="converse")
        db.add(call)
        db.flush()
    intent = CapturedIntent(
        call_log_id=call.id,
        intent_text=f"remind me about {subject}",
        requested_action="remind",
        subject=subject,
        status="resolved",
    )
    db.add(intent)
    db.flush()
    resolution = ResolutionResult(
        captured_intent_id=intent.id, status="staged", action_type="reminder", reversible=True, summary="x"
    )
    db.add(resolution)
    db.flush()
    action = StagedAction(
        resolution_result_id=resolution.id,
        status=status,
        action_type="reminder",
        action_payload=json.dumps({"subject": subject}),
        reversible=True,
        created_at=_stored(created),
        execute_after=_stored(due) if due is not None else None,
    )
    db.add(action)
    db.commit()
    return action


def _note(db, content, *, created, memory_type="event", source="call"):
    row = save_memory(db, content, memory_type, source=source)
    row.created_at = _stored(created)
    db.commit()
    return row


def _lines(speech: str) -> list[str]:
    return speech.splitlines()


def _line_with(lines, *needles) -> str:
    matches = [line for line in lines if all(needle in line for needle in needles)]
    assert len(matches) == 1, (needles, lines)
    return matches[0]


def test_review_repro_old_reminder_due_today_beats_new_reminder_due_next_month(db):
    """The reviewer's repro: set ten days ago, due this afternoon → spoken;
    set just now, due next month → not his day."""

    _reminder(db, "the dentist", status="staged", created=NOW - timedelta(days=10), due=_local(2026, 9, 2, 16))
    _reminder(db, "new reminder due next month", status="staged", created=NOW, due=NOW + timedelta(days=30))

    result = run_my_day_worker(lambda: db, now=NOW)

    assert result.error == ""
    lines = _lines(result.speech)
    assert lines[0] == "Right now it is Wednesday, 2 September 2026, 3:00 PM EDT."
    dentist = _line_with(lines, "the dentist")
    assert dentist.startswith("A reminder (waiting for his yes): the dentist")
    assert dentist.endswith("— today at 4 PM.")
    assert not any("next month" in line for line in lines)
    assert lines[-1] == MY_DAY_LIMIT_LINE


def test_local_day_window_at_2330_local_splits_today_from_tomorrow_across_the_utc_date(db):
    """At 23:30 local (UTC-4) both reminders are stored on the SAME UTC date
    (2026-09-03); only an aware local-day window tells today from tomorrow."""

    late = _local(2026, 9, 2, 23, 30)
    _reminder(db, "lock the back door", status="staged", created=late - timedelta(days=5), due=_local(2026, 9, 2, 23, 45))
    _reminder(db, "put the bins out", status="staged", created=late - timedelta(days=5), due=_local(2026, 9, 3, 0, 30))

    lines = _lines(run_my_day_worker(lambda: db, now=late).speech)

    door = _line_with(lines, "lock the back door")
    bins = _line_with(lines, "put the bins out")
    assert door.endswith("— today at 11:45 PM.")
    assert bins.endswith("— tomorrow at 12:30 AM.")
    assert lines.index(door) < lines.index(bins)


def test_overdue_open_reminder_is_named_still_open_with_its_due_day(db):
    """A reminder whose time passed without a yes is still his — say so,
    with its day — after today's, tomorrow's and undated ones."""

    _reminder(db, "call the physio", status="staged", created=NOW - timedelta(days=5), due=_local(2026, 8, 30, 10))
    _reminder(db, "ring the pharmacy", status="confirmed", created=NOW - timedelta(days=1), due=_local(2026, 9, 2, 9))
    _reminder(db, "the dentist", status="staged", created=NOW - timedelta(days=10), due=_local(2026, 9, 2, 16))
    _reminder(db, "water the plants", status="executed", created=NOW - timedelta(hours=3))

    lines = _lines(run_my_day_worker(lambda: db, now=NOW).speech)

    physio = _line_with(lines, "call the physio")
    assert "(waiting for his yes)" in physio
    assert physio.endswith("— still open, was due on 30 August at 10 AM.")
    pharmacy = _line_with(lines, "ring the pharmacy")
    assert "(set)" in pharmacy
    assert pharmacy.endswith("— still open, was due earlier today at 9 AM.")
    dentist = _line_with(lines, "the dentist")
    plants = _line_with(lines, "water the plants")
    # today, then undated, then the open ones — and the fresher open one first
    assert lines.index(dentist) < lines.index(plants) < lines.index(pharmacy) < lines.index(physio)


def test_executed_reminder_whose_time_has_passed_is_not_his_day(db):
    """Delivered at his yes, due time gone: that is the digest's done list,
    not today."""

    _reminder(db, "take the bins out", status="executed", created=NOW - timedelta(days=1), due=_local(2026, 9, 1, 16))

    lines = _lines(run_my_day_worker(lambda: db, now=NOW).speech)

    assert not any("take the bins out" in line for line in lines)
    assert any(line.startswith("Nothing is on record for him today") for line in lines)


def test_undated_reminder_he_set_by_voice_is_kept_and_labelled_no_time(db):
    """The realtime lane stores no due time at all (propose_action has no
    `when`): a reminder he set by voice today stays, honestly labelled;
    the two-day recency rule for undated rows is unchanged."""

    _reminder(db, "water the plants", status="executed", created=NOW - timedelta(hours=3))
    _reminder(db, "an old undated thing", status="executed", created=NOW - timedelta(days=3))

    lines = _lines(run_my_day_worker(lambda: db, now=NOW).speech)

    plants = _line_with(lines, "water the plants")
    assert plants == "A reminder (set): water the plants — no time on record."
    assert not any("old undated thing" in line for line in lines)


def test_stale_tomorrow_note_is_dated_within_a_week_and_dropped_beyond_it(db):
    """'tomorrow' in a note counts from the day it was written. Within a
    week: dated, with the uncertainty spelled out. Older: omitted. Parker's
    own realtime session summaries are never 'a note the family left'."""

    stale = "Sarah moved the neurologist appointment to tomorrow at two."
    _note(db, stale, created=NOW - timedelta(days=20))
    monday = _note(db, stale, created=NOW - timedelta(days=2))
    _note(db, "He talked about the tennis on tonight.", created=NOW - timedelta(hours=1), memory_type="topic", source="realtime")
    _note(db, "Priya is coming to visit on Saturday.", created=NOW - timedelta(hours=2), memory_type="event", source="manual")
    assert monday.created_at.date() == datetime(2026, 8, 31).date()  # Monday, stored UTC

    lines = _lines(run_my_day_worker(lambda: db, now=NOW).speech)

    neurologist = [line for line in lines if "neurologist" in line]
    assert neurologist == [
        "A note the family left on Monday: " + stale
        + " — written on Monday, so its “tomorrow” counts from then; the date is uncertain."
    ]
    assert not any("tennis" in line for line in lines)
    assert _line_with(lines, "Priya") == "A note the family left today: Priya is coming to visit on Saturday."


def test_medicine_lines_stay_dose_free_and_a_dosed_reminder_is_dropped(db):
    """Regression pin: names and times only, and a reminder that carries a
    dose is dropped by the spoken guard, never dated and spoken."""

    db.add(Medication(name="Sinemet", dosage="25-100 mg", schedule_times='["08:00"]', active=True))
    db.commit()
    _reminder(db, "take two Sinemet 25-100 mg", status="staged", created=NOW - timedelta(hours=1), due=_local(2026, 9, 2, 16))

    lines = _lines(run_my_day_worker(lambda: db, now=NOW).speech)

    assert "His Sinemet is scheduled at 8 AM." in lines
    assert "25-100" not in " ".join(lines) and "mg" not in " ".join(lines)
    assert lines[-1] == MY_DAY_LIMIT_LINE


def test_one_failing_source_is_named_never_nothing_on_record(db, monkeypatch):
    """Unit twin of the websocket honesty pins: a failing source is named,
    a store that will not open is an error — never 'nothing on record'."""

    def broken(db, now_local):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(realtime_workers, "_my_day_reminder_lines", broken)
    result = run_my_day_worker(lambda: db, now=NOW)
    assert result.error == ""
    assert "could not read his reminders" in result.speech
    assert "Nothing is on record" not in result.speech
    assert _lines(result.speech)[-1] == MY_DAY_LIMIT_LINE

    def no_store():
        raise RuntimeError("database is locked")

    failed = run_my_day_worker(no_store, now=NOW)
    assert failed.error == "could not read his notes"
    assert failed.speech == ""
