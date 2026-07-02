"""Tests for Parker's release construct-validity matrix evaluator."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from benchmark.evaluate_construct_validity_matrix_v0 import (  # type: ignore[import-not-found] # noqa: E402
    DEFAULT_MATRIX_PATH,
    evaluate_constructs,
    load_matrix,
)

REPO = Path(__file__).resolve().parents[2]
EVALUATOR = REPO / "benchmark/evaluate_construct_validity_matrix_v0.py"
MAKEFILE = REPO / "Makefile"
WORKFLOW = REPO / ".github/workflows/parker-ci.yml"


def test_construct_validity_matrix_separates_citable_claims_from_research_gaps() -> None:
    """Public release copy should distinguish current evidence from open research gaps."""

    rows = load_matrix(DEFAULT_MATRIX_PATH)

    assert len(rows) == 6
    assert len({row.construct_id for row in rows}) == len(rows)
    assert {row.public_private_scope for row in rows} == {"public_synthetic_only"}

    citable = [row for row in rows if row.current_claim_support == "citable_with_caveats"]
    gaps = [row for row in rows if row.current_claim_support == "research_gap_not_citable_yet"]

    assert len(citable) == 4
    assert len(gaps) == 2
    assert {row.release_criterion for row in rows} >= {
        "relevance",
        "feasibility",
        "construct_validity",
        "simplicity_generality",
    }
    assert all(row.required_assertions for row in citable)
    assert all(row.evidence_paths for row in citable)
    assert all(row.baseline for row in citable)
    assert all(row.safety_gate for row in citable)
    assert all("not real" in row.caveat.lower() or "no private" in row.caveat.lower() for row in citable)
    assert all(row.known_limitations for row in rows)
    assert all(row.upgrade_path for row in rows)
    assert all(not row.required_assertions for row in gaps)


def test_construct_validity_evaluator_verifies_current_reports_and_surfaces_gaps() -> None:
    result = evaluate_constructs(load_matrix(DEFAULT_MATRIX_PATH))
    payload = result.as_dict()

    assert payload["eval"] == "construct_validity_matrix_v0"
    assert payload["provenance"] == {
        "purpose": "release construct-validity guard: distinguish citable synthetic/local evidence from open research gaps",
        "private_data": "none",
        "fixture_policy": "public synthetic/local reports only",
        "model_or_api_dependency": "none",
    }
    assert payload["metrics"] == {
        "total_constructs": 6,
        "citable_constructs": 4,
        "research_gap_constructs": 2,
        "passing_citable_constructs": 4,
        "failing_citable_constructs": 0,
        "assertions_checked": 14,
        "assertions_failed": 0,
    }
    assert payload["construct_validity_gate"]["passed"] is True
    assert payload["failing_assertions"] == []
    assert {gap["construct_id"] for gap in payload["research_gap_cards"]} == {
        "cv-005-realtime-audio-latency",
        "cv-006-human-graded-repair-quality",
    }
    assert {
        "benchmark/reports/degraded_input_replay_eval_latest.json",
        "benchmark/reports/parker_demo_interactivity_eval_latest.json",
        "benchmark/reports/task_taxonomy_eval_latest.json",
        "benchmark/reports/caregiver_state_legibility_eval_latest.json",
    }.issubset(set(payload["evidence_paths_checked"]))


def test_construct_validity_matrix_rejects_citable_rows_without_limitations_or_upgrade_path(tmp_path: Path) -> None:
    weak_matrix = tmp_path / "constructs.json"
    weak_matrix.write_text(
        json.dumps(
            [
                {
                    "construct_id": "cv-bad",
                    "capability": "overclaimed_repair",
                    "release_criterion": "construct_validity",
                    "construct_question": "Can Parker repair all effortful speech?",
                    "operationalization": "Claim unsupported broad repair ability.",
                    "current_claim_support": "citable_with_caveats",
                    "metric_ids": ["intent_recovery_accuracy_delta_vs_non_interactive"],
                    "evidence_paths": ["benchmark/reports/degraded_input_replay_eval_latest.json"],
                    "required_assertions": [
                        {
                            "report_path": "benchmark/reports/degraded_input_replay_eval_latest.json",
                            "json_path": "pre_registered_primary_metric.threshold_met",
                            "operator": "eq",
                            "expected": True,
                        }
                    ],
                    "baseline": "non_interactive_no_repair",
                    "safety_gate": "unsafe misses must be 0",
                    "caveat": "Synthetic/local only; not real patient proof and no private data.",
                    "known_limitations": "",
                    "upgrade_path": "Scale to real-time audio after approval.",
                    "public_private_scope": "public_synthetic_only",
                }
            ]
        )
    )

    with pytest.raises(ValueError, match="known_limitations"):
        load_matrix(weak_matrix)

    payload = json.loads(weak_matrix.read_text())
    payload[0]["known_limitations"] = "Only three synthetic transcript fixtures."
    payload[0]["upgrade_path"] = ""
    weak_matrix.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="upgrade_path"):
        load_matrix(weak_matrix)


def test_construct_validity_cli_json_outputs_current_gate() -> None:
    completed = subprocess.run(
        [sys.executable, str(EVALUATOR), "--json"],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["eval"] == "construct_validity_matrix_v0"
    assert payload["construct_validity_gate"]["passed"] is True
    assert payload["metrics"]["research_gap_constructs"] == 2


def test_makefile_and_ci_expose_construct_validity_eval() -> None:
    makefile = MAKEFILE.read_text()
    workflow = WORKFLOW.read_text()

    assert "eval-construct-validity" in makefile
    assert "benchmark/evaluate_construct_validity_matrix_v0.py --write-report" in makefile
    assert "make eval-construct-validity" in workflow
