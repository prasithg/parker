"""Tests for the Patient Curiosity Loop evaluator (general brain lane).

Everything here is the deterministic lane — a scripted fake search client,
no network. The --live lane (real brain + live web search) is exercised
manually via ``make eval-curiosity-loop LIVE=1`` on the demo laptop.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from benchmark.evaluate_curiosity_loop_v0 import (  # type: ignore[import-not-found] # noqa: E402
    SCRIPTED_TRACES,
    run_failure_cases,
    run_scripted,
    run_stop_races,
)


def test_scripted_traces_cover_the_three_dad_topics_with_followups():
    ids = {trace["id"] for trace in SCRIPTED_TRACES}
    assert ids == {"weather-today-tomorrow", "score-then-followup", "interest-then-followup"}
    for trace in SCRIPTED_TRACES:
        assert len(trace["turns"]) == 2  # opening question + one follow-up
        assert trace["turns"][1].get("followup") is True


def test_scripted_traces_all_pass_through_the_general_lane():
    rows = run_scripted()
    failed = [row["id"] for row in rows if not row["ok"]]
    assert failed == []
    # Searched turns carry citation-backed sources; interest turns claim none.
    sourced = [row for row in rows if row["sources"]]
    assert len(sourced) == 4
    assert all(source["url"] for row in sourced for source in row["sources"])
    assert all(source["fresh_as_of"] for row in sourced for source in row["sources"])


def test_followups_require_history_by_construction():
    """The fake client refuses follow-ups without their anchor turn in
    history — so a passing follow-up proves the harness carried context."""

    rows = run_scripted()
    followups = [row for row in rows if row["followup"]]
    assert len(followups) == 3
    assert all(row["ok"] for row in followups)
    assert all("referring back" not in row["speech"] for row in followups)


def test_failure_cases_all_contained():
    rows = run_failure_cases()
    failed = [row["id"] for row in rows if not row["ok"]]
    assert failed == []
    assert {row["id"] for row in rows} >= {
        "brain-down-then-recovers",
        "refusal-before-brain",
        "purchase-held-at-human-gate",
        "vague-question-reasks",
    }


def test_stop_races_produce_zero_stale_results():
    races = run_stop_races(rounds=5)  # the full 20 runs in the eval itself
    assert races["ok"] is True
    assert races["stale_results"] == 0
