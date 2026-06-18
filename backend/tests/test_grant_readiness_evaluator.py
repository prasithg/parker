"""Tests for Parker's grant-readiness rollup evaluator."""

import json
import subprocess
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from benchmark.evaluate_grant_readiness_v0 import (  # type: ignore[import-not-found] # noqa: E402
    REQUIRED_REPORTS,
    evaluate_grant_readiness,
)

REPO = Path(__file__).resolve().parents[2]
EVALUATOR = REPO / "benchmark/evaluate_grant_readiness_v0.py"
MAKEFILE = REPO / "Makefile"


def test_grant_readiness_rollup_summarizes_actionable_proposal_evidence() -> None:
    """One command should tell Pras exactly what the grant can safely claim."""

    payload = evaluate_grant_readiness().as_dict()

    assert payload["eval"] == "grant_readiness_v0"
    assert payload["readiness_gate"]["passed"] is True
    assert payload["readiness_gate"]["blocking_failures"] == []
    assert payload["provenance"] == {
        "private_data": "none",
        "fixture_policy": "public synthetic/local reports only",
        "model_or_api_dependency": "none",
    }

    assert payload["metrics"]["claim_metric_map"] == {
        "total_claims": 4,
        "passing_claims": 4,
        "assertions_checked": 14,
        "assertions_failed": 0,
        "overclaim_gate_passed": True,
    }
    assert payload["metrics"]["construct_validity"] == {
        "total_constructs": 6,
        "citable_constructs": 4,
        "research_gap_constructs": 2,
        "passing_citable_constructs": 4,
        "assertions_checked": 12,
        "assertions_failed": 0,
        "construct_validity_gate_passed": True,
    }
    assert payload["metrics"]["degraded_input_replay"]["synthetic_cases"] == 3
    assert payload["metrics"]["degraded_input_replay"]["parker_recovered"] == 3
    assert payload["metrics"]["degraded_input_replay"]["no_repair_recovered"] == 0
    assert payload["metrics"]["degraded_input_replay"]["one_shot_keyword_baseline_recovered"] == 2
    assert payload["metrics"]["degraded_input_replay"]["unsafe_miss_count"] == 0
    assert payload["metrics"]["task_taxonomy"] == {
        "synthetic_cases": 24,
        "unsafe_miss_count": 0,
        "refusal_recall": 1.0,
        "escalation_recall": 1.0,
    }
    assert payload["metrics"]["demo_interactivity"] == {
        "synthetic_scenarios": 7,
        "overall_pass_rate": 1.0,
        "unsafe_miss_count": 0,
        "confirmation_before_action": 1.0,
        "local_outbox_reversibility": 1.0,
        "caregiver_ui_clarity": 1.0,
    }
    assert payload["metrics"]["repair_quality_rubric"] == {
        "total_cases": 5,
        "reference_passing_cases": 5,
        "generic_fallback_passing_cases": 0,
        "rubric_detects_generic_fallback": True,
        "quality_proof_claim_allowed": False,
    }
    assert payload["grant_summary"]["repair_quality_caveat"] == "Repair-choice specificity is proxy-rubric checked only; human-graded repair quality remains a grant-funded research gap."

    assert len(payload["claim_cards"]) == 4
    assert all(card["status"] == "pass" for card in payload["claim_cards"])
    assert {
        "benchmark/reports/degraded_input_replay_eval_latest.json",
        "benchmark/reports/task_taxonomy_eval_latest.json",
        "benchmark/reports/parker_demo_interactivity_eval_latest.json",
        "benchmark/reports/claim_metric_map_eval_latest.json",
        "benchmark/reports/construct_validity_matrix_eval_latest.json",
        "benchmark/reports/repair_quality_rubric_eval_latest.json",
    }.issubset(set(payload["evidence_paths_checked"]))

    safe_claim = payload["grant_summary"]["safe_claim_line"]
    caveat = payload["grant_summary"]["required_caveat"]
    assert "3 synthetic held-out transcript fixtures" in safe_claim
    assert "one-shot keyword" in safe_claim
    assert "0 unsafe misses" in safe_claim
    assert "not real" in caveat.lower()
    assert "no private" in caveat.lower()


def test_grant_readiness_fails_closed_when_required_report_is_missing(tmp_path: Path) -> None:
    report_paths = dict(REQUIRED_REPORTS)
    report_paths["demo_interactivity"] = tmp_path / "missing-demo-report.json"

    payload = evaluate_grant_readiness(report_paths=report_paths).as_dict()

    assert payload["readiness_gate"]["passed"] is False
    assert any(
        failure["check"] == "demo_interactivity_report"
        and "missing-demo-report.json" in failure["message"]
        for failure in payload["readiness_gate"]["blocking_failures"]
    )


def test_grant_readiness_cli_json_outputs_mobile_briefing_fields() -> None:
    completed = subprocess.run(
        [sys.executable, str(EVALUATOR), "--json"],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["readiness_gate"]["passed"] is True
    assert payload["grant_summary"]["primary_decision"] == "Safe to cite as synthetic/local grant evidence; not safe to present as real-world or clinical proof."


def test_makefile_exposes_one_command_grant_readiness_rollup() -> None:
    makefile = MAKEFILE.read_text()

    assert "eval-grant-readiness" in makefile
    assert "benchmark/evaluate_construct_validity_matrix_v0.py --write-report" in makefile
    assert "benchmark/evaluate_repair_quality_rubric_v0.py --write-report" in makefile
    assert "benchmark/evaluate_grant_readiness_v0.py --write-report" in makefile
