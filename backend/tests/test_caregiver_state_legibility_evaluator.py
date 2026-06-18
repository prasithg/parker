"""Tests for Parker's synthetic caregiver-state legibility evaluator."""

import json
import subprocess
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from benchmark.evaluate_caregiver_state_legibility_v0 import (  # type: ignore[import-not-found] # noqa: E402
    DEFAULT_FIXTURE_PATH,
    evaluate_caregiver_state_legibility,
    load_tasks,
)

REPO = Path(__file__).resolve().parents[2]
EVALUATOR = REPO / "benchmark/evaluate_caregiver_state_legibility_v0.py"
MAKEFILE = REPO / "Makefile"
WORKFLOW = REPO / ".github/workflows/parker-ci.yml"


def test_caregiver_state_legibility_scores_review_ui_against_raw_chat_baseline() -> None:
    """The scorer should measure UI state legibility, not merely count existing cards."""

    payload = evaluate_caregiver_state_legibility(load_tasks(DEFAULT_FIXTURE_PATH)).as_dict()

    assert payload["eval"] == "caregiver_state_legibility_v0"
    assert payload["provenance"] == {
        "private_data": "none",
        "fixture_policy": "public synthetic/local review-state tasks only",
        "model_or_api_dependency": "none",
        "human_grade_dependency": "none; this is a synthetic proxy, not caregiver usability evidence",
    }
    assert payload["metrics"]["total_tasks"] == 6
    assert payload["metrics"]["parker_review_ui"] == {
        "correct_tasks": 6,
        "task_success_rate": 1.0,
    }
    assert payload["metrics"]["raw_chat_only"] == {
        "correct_tasks": 0,
        "task_success_rate": 0.0,
    }
    assert payload["metrics"]["delta_vs_raw_chat"] == 1.0
    assert payload["metrics"]["unsafe_miss_count"] == 0
    assert payload["legibility_gate"]["passed"] is True
    assert payload["grant_posture"]["human_usability_claim_allowed"] is False
    assert "not a caregiver usability study" in payload["grant_posture"]["required_caveat"].lower()


def test_caregiver_state_legibility_flags_missing_local_or_forbidden_actions() -> None:
    tasks = load_tasks(DEFAULT_FIXTURE_PATH)
    bad_observation = {
        **tasks[0].system_observations["parker_review_ui"],
        "local_only": False,
        "forbidden_actions_present": ["send_external"],
    }
    bad_task = tasks[0].with_system_observation("parker_review_ui", bad_observation)

    payload = evaluate_caregiver_state_legibility([bad_task]).as_dict()

    assert payload["legibility_gate"]["passed"] is False
    assert payload["metrics"]["unsafe_miss_count"] == 1
    assert any("send_external" in failure["message"] for failure in payload["failures"])


def test_caregiver_state_legibility_cli_json_outputs_gate() -> None:
    completed = subprocess.run(
        [sys.executable, str(EVALUATOR), "--json"],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["eval"] == "caregiver_state_legibility_v0"
    assert payload["legibility_gate"]["passed"] is True


def test_makefile_and_ci_expose_caregiver_state_legibility_eval() -> None:
    makefile = MAKEFILE.read_text()
    workflow = WORKFLOW.read_text()

    assert "eval-caregiver-state-legibility" in makefile
    assert "benchmark/evaluate_caregiver_state_legibility_v0.py --write-report" in makefile
    assert "make eval-caregiver-state-legibility" in workflow
