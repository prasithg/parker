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
from zoneinfo import ZoneInfo

import pytest

from app.conversation.tools import execute_tool
from app.db.models import CallLog, CapturedIntent, Medication, ResolutionResult, StagedAction
from app.memory.store import save_memory
from app.parker import realtime_workers
from app.parker.pipeline import capture_intent, resolve_captured_intents, stage_resolved_actions
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
    assert _line_with(lines, "Priya") == (
        "A note the family left today: Priya is coming to visit on Saturday."
        " — its plan date is not explicit, so the date is uncertain."
    )


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


# ---------------------------------------------------------------------------
# Fix round 2 (review of 1038242): P03-1 pending intents, P03-2 due-time
# zone, P03-3 the reminder cap is never silent.
# ---------------------------------------------------------------------------


def _call(db) -> CallLog:
    call = CallLog(call_sid="P03-fix2", call_type="converse")
    db.add(call)
    db.commit()
    return call


def test_dated_reminder_pending_in_the_pipeline_is_his_day_before_it_stages(db):
    """P03-1: through the real pipeline a dated reminder is a pending
    CapturedIntent until the resolve gate (due_at <= now) lets it stage —
    so before its time there is NO StagedAction. Captured ten days ago by
    the text-lane tool, due 4 PM today: at 3 PM it is his day; at 4:30 PM,
    once resolved and staged, it is still open — one line, never two."""

    call = _call(db)
    result = execute_tool(
        db,
        call.id,
        "capture_intent",
        {
            "intent_text": "remind me about the dentist",
            "requested_action": "remind",
            "due_at": "2026-09-02T20:00:00Z",
            "subject": "the dentist",
        },
    )
    assert result["status"] == "captured"
    intent = db.get(CapturedIntent, result["captured_intent_id"])
    intent.created_at = _stored(NOW - timedelta(days=10))
    db.commit()
    assert intent.status == "pending"
    assert db.query(StagedAction).count() == 0

    lines = _lines(run_my_day_worker(lambda: db, now=NOW).speech)

    assert _line_with(lines, "the dentist") == "A reminder (recorded; not set yet): the dentist — today at 4 PM."
    assert not any(line.startswith("Nothing is on record") for line in lines)

    later = _local(2026, 9, 2, 16, 30)
    resolve_captured_intents(db, now=_stored(later))
    stage_resolved_actions(db, now=_stored(later))
    assert db.query(StagedAction).count() == 1

    lines = _lines(run_my_day_worker(lambda: db, now=later).speech)

    assert _line_with(lines, "the dentist") == (
        "A reminder (waiting for his yes): the dentist — still open, was due earlier today at 4 PM."
    )


def test_recent_undated_pending_intent_is_recorded_but_not_set(db):
    """An unresolved voice reminder with no time is durable and must be named."""

    call = _call(db)
    result = execute_tool(
        db,
        call.id,
        "capture_intent",
        {
            "intent_text": "remind me to water the plants",
            "requested_action": "remind",
            "subject": "water the plants",
        },
    )
    intent = db.get(CapturedIntent, result["captured_intent_id"])
    intent.created_at = _stored(NOW - timedelta(hours=1))
    db.commit()

    lines = _lines(run_my_day_worker(lambda: db, now=NOW).speech)

    assert _line_with(lines, "water the plants") == (
        "A reminder (recorded; not set yet): water the plants — no time on record."
    )
    assert not any(line.startswith("Nothing is on record") for line in lines)


@pytest.mark.parametrize("raw", ["2026-09-02T16:00:00-04:00", "2026-09-02T16:00:00"])
def test_captured_due_time_with_offset_or_as_home_wall_time_is_stored_as_utc(db, raw):
    """P03-2: the only due-time writer (`pipeline._coerce_datetime`) kept the
    wall-clock digits of an offset-bearing string and dropped the offset,
    while every reader decodes storage as naive UTC. An aware string
    converts to UTC; a naive one is his home wall time (the brain talks to
    a local user), then the same conversion. 4 PM EDT → 20:00 stored, and
    My Day at 3 PM says today at 4 PM — not "still open" at noon."""

    captured = capture_intent(
        db,
        call_log_id=_call(db).id,
        intent_text="remind me about the dentist",
        subject="the dentist",
        due_at=raw,
    )
    assert captured.due_at == datetime(2026, 9, 2, 20, 0)

    lines = _lines(run_my_day_worker(lambda: db, now=NOW).speech)

    assert _line_with(lines, "the dentist").endswith("— today at 4 PM.")


def test_captured_due_time_in_utc_still_round_trips_and_datetimes_pass_through(db):
    """P03-2 guard: a 'Z' string is unchanged by the fix, and a datetime
    object (the seed's UTC-naive convention) is stored as given."""

    call = _call(db)
    zulu = capture_intent(db, call_log_id=call.id, intent_text="z", subject="z", due_at="2026-09-02T20:00:00Z")
    assert zulu.due_at == datetime(2026, 9, 2, 20, 0)
    given = datetime(2026, 9, 2, 20, 0)
    naive = capture_intent(db, call_log_id=call.id, intent_text="n", subject="n", due_at=given)
    assert naive.due_at == given
    assert capture_intent(db, call_log_id=call.id, intent_text="u", subject="u", due_at=None).due_at is None


def test_aware_datetime_object_is_normalized_to_naive_utc(db):
    aware = datetime(2026, 9, 2, 16, 0, tzinfo=timezone(timedelta(hours=-4)))

    captured = capture_intent(
        db,
        call_log_id=_call(db).id,
        intent_text="remind me about the dentist",
        subject="the dentist",
        due_at=aware,
    )

    assert captured.due_at == datetime(2026, 9, 2, 20, 0)


@pytest.mark.parametrize(
    "wall_time, problem",
    [
        ("2026-03-08T02:30:00", "does not exist"),
        ("2026-11-01T01:30:00", "ambiguous"),
    ],
)
def test_dst_gap_and_fold_require_an_explicit_offset(db, monkeypatch, wall_time, problem):
    monkeypatch.setattr(
        "app.parker.rollup.home_timezone", lambda: ZoneInfo("America/New_York")
    )

    with pytest.raises(ValueError, match=problem):
        capture_intent(
            db,
            call_log_id=_call(db).id,
            intent_text="remind me then",
            subject="the appointment",
            due_at=wall_time,
        )


def test_seven_reminders_are_cut_at_six_with_a_more_line_never_silently(db):
    """P03-3: the six-line reminder cap cut silently and the render item
    then told the model to deny anything missing. Seven undated reminders:
    exactly six lines plus one honest "…and 1 more" line, limit line last."""

    for i in range(7):
        _reminder(db, f"thing {i}", status="executed", created=NOW - timedelta(hours=i + 1))

    lines = _lines(run_my_day_worker(lambda: db, now=NOW).speech)

    reminders = [line for line in lines if line.startswith("A reminder")]
    assert len(reminders) == 6
    more = _line_with(lines, "more reminders", "never say he has none")
    assert more == "…and 1 more reminders Parker did not list here — never say he has none."
    assert lines.index(more) == lines.index(reminders[-1]) + 1
    assert lines[-1] == MY_DAY_LIMIT_LINE


def test_five_reminders_have_no_more_line(db):
    for i in range(5):
        _reminder(db, f"thing {i}", status="executed", created=NOW - timedelta(hours=i + 1))

    lines = _lines(run_my_day_worker(lambda: db, now=NOW).speech)

    assert len([line for line in lines if line.startswith("A reminder")]) == 5
    assert not any("more reminders" in line for line in lines)
    assert lines[-1] == MY_DAY_LIMIT_LINE


def test_generic_plan_note_is_explicitly_date_uncertain(db):
    _note(
        db,
        "Dentist appointment at 3 PM.",
        created=NOW - timedelta(days=3),
        memory_type="event",
        source="manual",
    )

    lines = _lines(run_my_day_worker(lambda: db, now=NOW).speech)

    assert _line_with(lines, "Dentist appointment").endswith(
        "— its plan date is not explicit, so the date is uncertain."
    )


def test_relevant_plan_note_is_not_hidden_behind_twenty_newer_memories(db):
    _note(
        db,
        "Dentist appointment at 3 PM.",
        created=NOW - timedelta(days=1),
        memory_type="event",
        source="manual",
    )
    for index in range(20):
        _note(
            db,
            f"Unrelated family memory {index}.",
            created=NOW - timedelta(minutes=index + 1),
            memory_type="fact",
            source="manual",
        )

    lines = _lines(run_my_day_worker(lambda: db, now=NOW).speech)

    assert any("Dentist appointment" in line for line in lines)
    assert not any(line.startswith("Nothing is on record") for line in lines)


def test_more_than_four_plan_notes_report_the_omitted_count(db):
    for index in range(6):
        _note(
            db,
            f"Appointment plan {index} at {index + 1} PM.",
            created=NOW - timedelta(minutes=index + 1),
            memory_type="event",
            source="manual",
        )

    lines = _lines(run_my_day_worker(lambda: db, now=NOW).speech)

    assert len([line for line in lines if line.startswith("A note")]) == 4
    assert _line_with(lines, "more plan-like notes") == (
        "…and 2 more plan-like notes Parker did not list here — never say he has none."
    )


def test_global_cap_counts_records_inside_source_summaries(db, monkeypatch):
    """A nested source summary represents its raw records, not one line."""

    for index in range(6):
        _note(
            db,
            f"Appointment plan {index} at {index + 1} PM.",
            created=NOW - timedelta(minutes=index + 1),
            memory_type="event",
            source="manual",
        )
    monkeypatch.setattr(
        realtime_workers,
        "_my_day_medication_lines",
        lambda _db, _now: [f"Medication record {index}." for index in range(12)],
    )
    monkeypatch.setattr(realtime_workers, "_my_day_reminder_lines", lambda _db, _now: [])

    lines = _lines(run_my_day_worker(lambda: db, now=NOW).speech)

    assert _line_with(lines, "more Parker did not list here") == (
        "…and 9 more Parker did not list here — never say he has none."
    )


def test_busy_day_never_caps_away_a_source_failure(db, monkeypatch):
    monkeypatch.setattr(
        realtime_workers,
        "_my_day_medication_lines",
        lambda _db, _now: [f"Safe schedule line {index}." for index in range(12)],
    )

    def broken(_db, _now):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(realtime_workers, "_my_day_reminder_lines", broken)
    monkeypatch.setattr(realtime_workers, "_my_day_note_lines", lambda _db, _now: [])

    lines = _lines(run_my_day_worker(lambda: db, now=NOW).speech)

    assert _line_with(lines, "could not read his reminders")
    assert _line_with(lines, "more Parker did not list here")
    assert not any(line.startswith("Nothing is on record") for line in lines)
