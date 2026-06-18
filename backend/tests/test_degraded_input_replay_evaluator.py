"""Tests for the degraded-input replay eval harness."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from benchmark.evaluate_degraded_input_replay_v0 import (  # type: ignore[import-not-found] # noqa: E402
    DEFAULT_CASES_PATH,
    evaluate,
    load_cases,
)

REPO = Path(__file__).resolve().parents[2]
EVALUATOR = REPO / "benchmark/evaluate_degraded_input_replay_v0.py"


def test_degraded_input_replay_cases_are_synthetic_heldout_and_valid():
    cases = load_cases(DEFAULT_CASES_PATH)

    assert len(cases) == 3
    assert {case.privacy for case in cases} == {"synthetic"}
    assert {case.split for case in cases} == {"heldout_night4_smoke"}
    assert all(case.degradation_slices for case in cases)
    assert {case.expected_action_type for case in cases} == {"reminder", "family_message"}


def test_degraded_input_replay_primary_metric_compares_repair_against_no_repair():
    result = evaluate(load_cases(DEFAULT_CASES_PATH))
    payload = result.as_dict()
    primary = payload["pre_registered_primary_metric"]
    metrics = payload["baseline_metrics"]

    assert primary["name"] == "intent_recovery_accuracy_delta_vs_non_interactive"
    assert primary["non_interactive_no_repair"] == 0.0
    assert primary["parker_repair_protocol"] == 1.0
    assert primary["delta"] == pytest.approx(1.0)
    assert primary["threshold_met"] is True
    assert primary["safety_critical_misses"] == 0
    assert metrics["parker_repair_protocol"]["median_turns_to_resolution"] == 2
    assert metrics["non_interactive_no_repair"]["median_turns_to_resolution"] is None


def test_degraded_input_replay_reports_one_shot_keyword_baseline_as_secondary_comparator():
    result = evaluate(load_cases(DEFAULT_CASES_PATH))
    payload = result.as_dict()
    metrics = payload["baseline_metrics"]
    secondary = payload["secondary_comparisons"]["one_shot_keyword_baseline"]
    one_shot_rows = {
        row["case_id"]: row for row in payload["case_results"]["one_shot_keyword_baseline"]
    }

    assert metrics["one_shot_keyword_baseline"]["intent_recovery_accuracy"] == pytest.approx(2 / 3)
    assert metrics["one_shot_keyword_baseline"]["repair_initiated_rate"] == 0.0
    assert metrics["one_shot_keyword_baseline"]["safety_critical_misses"] == 0
    assert secondary["baseline"] == "one_shot_keyword_baseline"
    assert secondary["baseline_intent_recovery_accuracy"] == pytest.approx(2 / 3)
    assert secondary["delta_vs_parker"] == pytest.approx(1 / 3)
    assert one_shot_rows["deg-001-reminder-tomato-evening"]["recovered_intent"] is True
    assert one_shot_rows["deg-002-family-message-physio"]["recovered_intent"] is True
    assert one_shot_rows["deg-003-reminder-garden-call"]["recovered_intent"] is False


def test_degraded_input_replay_cli_json_outputs_primary_metric():
    completed = subprocess.run(
        [sys.executable, str(EVALUATOR), "--json"],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["total_cases"] == 3
    assert payload["pre_registered_primary_metric"]["delta"] == pytest.approx(1.0)
    assert payload["provenance"]["private_data"] == "none"
