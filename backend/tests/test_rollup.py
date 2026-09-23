"""Weekly rollup: home-local buckets, exact metric math, aggregates only.

The binding contracts pinned here:

- rollup week buckets use the HOME-LOCAL timezone, not UTC (a stored
  Sunday-late-UTC row belongs to the local Monday's week);
- the metric definitions in app/parker/rollup.py compute exactly;
- repeated-error groups need the same normalized failed intent on ≥2
  distinct local days and surface as hashes, never text;
- the emitted markdown/JSON contain zero transcript text;
- the Makefile exposes the one-command `make rollup` target.
"""

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import settings
from app.conversation.outcomes import InteractionOutcome, normalize_intent
from app.conversation.textloop import TextSession
from app.db.models import CallLog
from app.parker import rollup
from app.parker.rollup import (
    build_weekly_rollup,
    render_rollup_markdown,
    week_start_for,
    write_rollup_files,
)

AUCKLAND = ZoneInfo("Pacific/Auckland")  # UTC+12/+13: the sharpest date-skew case
WEEK_OF = date(2026, 8, 10)  # a Monday


def test_home_timezone_keeps_named_zone_dst_rules(monkeypatch):
    new_york = ZoneInfo("America/New_York")
    monkeypatch.setattr(rollup, "get_localzone", lambda: new_york, raising=False)

    resolved = rollup.home_timezone()

    winter = datetime(2026, 1, 15, 9, tzinfo=resolved).utcoffset()
    summer = datetime(2026, 7, 15, 9, tzinfo=resolved).utcoffset()
    assert getattr(resolved, "key", None) == "America/New_York"
    assert winter != summer


def _call(db) -> int:
    call = CallLog(call_sid="CA_ROLLUP", call_type="text_loop")
    db.add(call)
    db.commit()
    db.refresh(call)
    return call.id


def _row(
    db,
    call_log_id: int,
    *,
    outcome: str,
    opened_at: datetime,
    normalized_intent: str | None = None,
    action_type: str | None = None,
) -> None:
    db.add(
        InteractionOutcome(
            call_log_id=call_log_id,
            opened_at=opened_at,
            closed_at=opened_at,
            outcome=outcome,
            closing_kind="test",
            action_type=action_type,
            normalized_intent=normalized_intent,
        )
    )
    db.commit()


def test_week_start_is_monday():
    assert week_start_for(date(2026, 8, 13)) == date(2026, 8, 10)  # Thursday → Monday
    assert week_start_for(date(2026, 8, 10)) == date(2026, 8, 10)  # Monday → itself
    assert week_start_for(date(2026, 8, 16)) == date(2026, 8, 10)  # Sunday → same week


def test_rollup_buckets_use_home_local_time_not_utc(db):
    call_id = _call(db)
    # Stored (naive UTC) Sunday 2026-08-09 23:30 = Monday 2026-08-10 11:30
    # in Auckland — INSIDE the week of Aug 10 locally, outside it by UTC.
    _row(db, call_id, outcome="understood_first_try", opened_at=datetime(2026, 8, 9, 23, 30))
    # Stored Sunday 2026-08-16 14:00 UTC = Monday 2026-08-17 02:00 local —
    # OUTSIDE the week of Aug 10 locally despite the UTC date saying Sunday.
    _row(db, call_id, outcome="understood_first_try", opened_at=datetime(2026, 8, 16, 14, 0))

    rollup = build_weekly_rollup(db, week_of=WEEK_OF, tz=AUCKLAND)

    assert rollup["week_start"] == "2026-08-10"
    assert rollup["week_end"] == "2026-08-16"
    assert rollup["outcome_counts"]["understood_first_try"] == 1
    assert rollup["totals"]["all_interactions"] == 1


def test_rollup_metric_definitions_compute_exactly(db):
    call_id = _call(db)
    when = datetime(2026, 8, 11, 2, 0)  # mid-week local either way
    for outcome, count in (
        ("understood_first_try", 3),
        ("repaired_success", 2),
        ("repair_abandoned", 1),
        ("wrong_action", 1),
        ("refused_safety", 2),
        ("no_response", 1),
        ("ambient_noop", 5),
    ):
        for _ in range(count):
            _row(db, call_id, outcome=outcome, opened_at=when)

    rollup = build_weekly_rollup(db, week_of=WEEK_OF, tz=AUCKLAND)

    assert rollup["totals"]["requests"] == 7
    assert rollup["totals"]["directed_interactions"] == 10
    assert rollup["totals"]["all_interactions"] == 15
    metrics = rollup["metrics"]
    assert metrics["unassisted_success_rate"] == round(5 / 7, 4)
    assert metrics["first_attempt_rate"] == round(3 / 7, 4)
    assert metrics["repair_success_rate"] == round(2 / 3, 4)
    assert metrics["nuisance_choice_rate"] == round(1 / 8, 4)
    assert metrics["refusal_share_of_directed"] == round(2 / 10, 4)


def test_rollup_with_no_data_reports_none_rates(db):
    rollup = build_weekly_rollup(db, week_of=WEEK_OF, tz=AUCKLAND)

    assert rollup["totals"]["all_interactions"] == 0
    assert all(value is None for value in rollup["metrics"].values())
    markdown = render_rollup_markdown(rollup)
    assert "n/a (no data)" in markdown


def test_repeated_errors_need_two_distinct_local_days_and_stay_hashed(db):
    call_id = _call(db)
    signature = normalize_intent("call the physio about tuesday")
    # Same failed intent on two distinct local days.
    _row(db, call_id, outcome="repair_abandoned", opened_at=datetime(2026, 8, 11, 2, 0),
         normalized_intent=signature)
    _row(db, call_id, outcome="wrong_action", opened_at=datetime(2026, 8, 12, 2, 0),
         normalized_intent=signature)
    # A different failure seen only once — must not surface.
    _row(db, call_id, outcome="repair_abandoned", opened_at=datetime(2026, 8, 12, 3, 0),
         normalized_intent=normalize_intent("start the stretching video"))
    # Same-day repeats of a third — must not surface (needs distinct days).
    same_day = normalize_intent("put on the quiz show")
    for hour in (4, 5):
        _row(db, call_id, outcome="repair_abandoned",
             opened_at=datetime(2026, 8, 12, hour, 0), normalized_intent=same_day)

    rollup = build_weekly_rollup(db, week_of=WEEK_OF, tz=AUCKLAND)

    repeated = rollup["repeated_errors"]
    assert len(repeated) == 1
    group = repeated[0]
    assert group["occurrences"] == 2
    assert group["distinct_days"] == ["2026-08-11", "2026-08-12"]
    assert sorted(group["outcomes"]) == ["repair_abandoned", "wrong_action"]
    # Hash only — no transcript-derived text anywhere in the group.
    assert signature not in json.dumps(rollup)
    assert "physio" not in json.dumps(rollup)
    assert len(group["signature"]) == 8


def test_rollup_artifact_contains_no_transcript_text(db, monkeypatch):
    """End to end from real utterances: nothing the user said may appear in
    the artifact, even with repair-capture consent ON."""

    monkeypatch.setattr(settings, "repair_event_capture_consented", True)
    call = CallLog(call_sid="CA_ROLLUP_E2E", call_type="text_loop")
    db.add(call)
    db.commit()
    db.refresh(call)
    session = TextSession(db, call.id)
    session.handle("Remind me to water the tomato plants this evening")
    session.handle("Call... the... you know... the one with the garden...")
    session.handle("1")
    session.handle("Should I take half my pills tomorrow?")

    week = week_start_for(datetime.utcnow().date())
    rollup = build_weekly_rollup(db, week_of=week, tz=ZoneInfo("UTC"))
    markdown = render_rollup_markdown(rollup)
    blob = markdown + json.dumps(rollup)

    for fragment in ("tomato", "garden", "pills", "water the", "Sarah"):
        assert fragment not in blob
    # Four handled lines are three interactions: "1" closed the repair.
    assert rollup["totals"]["all_interactions"] == 3
    assert rollup["outcome_counts"]["repaired_success"] == 1


def test_write_rollup_files_writes_markdown_and_json(db, tmp_path):
    rollup = build_weekly_rollup(db, week_of=WEEK_OF, tz=AUCKLAND)
    markdown = render_rollup_markdown(rollup)

    md_path, json_path = write_rollup_files(rollup, markdown, directory=tmp_path)

    assert md_path == tmp_path / "parker-rollup-week-2026-08-10.md"
    assert json_path == tmp_path / "parker-rollup-week-2026-08-10.json"
    assert "Parker weekly rollup" in md_path.read_text(encoding="utf-8")
    parsed = json.loads(json_path.read_text(encoding="utf-8"))
    assert parsed["week_start"] == "2026-08-10"


def test_makefile_exposes_one_command_weekly_rollup():
    makefile = (Path(__file__).resolve().parents[2] / "Makefile").read_text(encoding="utf-8")

    assert "rollup: backend-venv" in makefile
    assert "app.parker.rollup" in makefile
