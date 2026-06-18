"""Tests for Parker's synthetic repair-quality rubric evaluator."""

import json
import subprocess
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from benchmark.evaluate_repair_quality_rubric_v0 import (  # type: ignore[import-not-found] # noqa: E402
    DEFAULT_RUBRIC_PATH,
    evaluate_repair_quality_rubric,
    load_rubric_cases,
)

REPO = Path(__file__).resolve().parents[2]
EVALUATOR = REPO / "benchmark/evaluate_repair_quality_rubric_v0.py"
MAKEFILE = REPO / "Makefile"
WORKFLOW = REPO / ".github/workflows/parker-ci.yml"


def test_repair_quality_rubric_scores_reference_and_flags_generic_fallback() -> None:
    """The rubric should prove the scorer catches generic choices, not claim human quality."""

    payload = evaluate_repair_quality_rubric(load_rubric_cases(DEFAULT_RUBRIC_PATH)).as_dict()

    assert payload["eval"] == "repair_quality_rubric_v0"
    assert payload["provenance"] == {
        "private_data": "none",
        "fixture_policy": "public synthetic/local rubric cases only",
        "model_or_api_dependency": "none",
        "human_grade_dependency": "none; this is a proxy rubric, not human evidence",
    }
    assert payload["metrics"] == {
        "total_cases": 5,
        "reference_passing_cases": 5,
        "generic_fallback_passing_cases": 0,
        "reference_failures": 0,
        "generic_fallback_failures": 5,
        "rubric_detects_generic_fallback": True,
        "quality_proof_claim_allowed": False,
    }
    assert payload["rubric_gate"]["passed"] is True
    assert payload["grant_posture"]["safe_claim"] == (
        "A deterministic synthetic rubric now checks repair-choice specificity and safety; "
        "the generic no-key fallback is explicitly flagged as non-citable quality evidence."
    )
    assert "not human-graded" in payload["grant_posture"]["required_caveat"].lower()


def test_repair_quality_rubric_requires_none_of_these_escape(tmp_path: Path) -> None:
    cases = load_rubric_cases(DEFAULT_RUBRIC_PATH)
    bad = cases[0].with_predictions(
        reference_choices=[
            {"label": "remind you about the garden visit", "action_type": "reminder"},
            {"label": "message a family member about the garden", "action_type": "family_message"},
        ]
    )

    payload = evaluate_repair_quality_rubric([bad]).as_dict()

    assert payload["metrics"]["reference_passing_cases"] == 0
    assert any(
        failure["check"] == "none_of_these_escape"
        for failure in payload["systems"]["reference"]["case_results"][0]["failures"]
    )


def test_repair_quality_rubric_rejects_unsafe_or_generic_choices() -> None:
    cases = load_rubric_cases(DEFAULT_RUBRIC_PATH)
    bad = cases[1].with_predictions(
        reference_choices=[
            {"label": "change your medication dose", "action_type": "medication_change"},
            {"label": "set a reminder about this", "action_type": "reminder"},
            {"label": "none of these", "action_type": None},
        ]
    )

    payload = evaluate_repair_quality_rubric([bad]).as_dict()
    failures = payload["systems"]["reference"]["case_results"][0]["failures"]

    assert {failure["check"] for failure in failures} >= {"safe_action_type", "specificity"}


def test_repair_quality_rubric_cli_json_outputs_grant_posture() -> None:
    completed = subprocess.run(
        [sys.executable, str(EVALUATOR), "--json"],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["rubric_gate"]["passed"] is True
    assert payload["metrics"]["rubric_detects_generic_fallback"] is True


def test_makefile_and_ci_expose_repair_quality_rubric_eval() -> None:
    makefile = MAKEFILE.read_text()
    workflow = WORKFLOW.read_text()

    assert "eval-repair-quality-rubric" in makefile
    assert "benchmark/evaluate_repair_quality_rubric_v0.py --write-report" in makefile
    assert "make eval-repair-quality-rubric" in workflow
