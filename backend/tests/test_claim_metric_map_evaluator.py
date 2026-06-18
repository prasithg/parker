"""Tests for the grant claim→metric construct-validity map."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from benchmark.evaluate_claim_metric_map_v0 import (  # type: ignore[import-not-found] # noqa: E402
    DEFAULT_CLAIM_MAP_PATH,
    evaluate_claims,
    load_claims,
)

REPO = Path(__file__).resolve().parents[2]
EVALUATOR = REPO / "benchmark/evaluate_claim_metric_map_v0.py"


def test_claim_metric_map_rows_are_metric_bound_synthetic_and_caveated():
    claims = load_claims(DEFAULT_CLAIM_MAP_PATH)

    assert len(claims) == 4
    assert len({claim.claim_id for claim in claims}) == len(claims)
    assert {claim.public_private_scope for claim in claims} == {"public_synthetic_only"}
    assert all(claim.metric_ids for claim in claims)
    assert all(claim.report_paths for claim in claims)
    assert all("not real" in claim.caveat.lower() or "no private" in claim.caveat.lower() for claim in claims)


def test_effortful_speech_claim_names_one_shot_secondary_baseline():
    claims = load_claims(DEFAULT_CLAIM_MAP_PATH)
    claim = next(claim for claim in claims if claim.claim_id == "claim-001-effortful-speech-repair")

    assert "non_interactive_no_repair" in claim.baseline
    assert "one_shot_keyword_baseline" in claim.baseline
    assert any(
        assertion.json_path == "secondary_comparisons.one_shot_keyword_baseline.delta_vs_parker"
        and assertion.operator == "gte"
        and assertion.expected == 0.333
        for assertion in claim.required_assertions
    )


def test_claim_metric_map_evaluator_verifies_current_reports():
    result = evaluate_claims(load_claims(DEFAULT_CLAIM_MAP_PATH))
    payload = result.as_dict()

    assert payload["metrics"]["total_claims"] == 4
    assert payload["metrics"]["passing_claims"] == 4
    assert payload["metrics"]["failing_claims"] == 0
    assert payload["overclaim_gate"]["passed"] is True
    assert payload["overclaim_gate"]["metric_bound_claims"] == 4
    assert payload["failing_assertions"] == []
    assert {
        "benchmark/reports/task_taxonomy_eval_latest.json",
        "benchmark/reports/parker_demo_interactivity_eval_latest.json",
        "benchmark/reports/degraded_input_replay_eval_latest.json",
    }.issubset(set(payload["evidence_paths_checked"]))


def test_claim_metric_map_rejects_uncaveated_claim(tmp_path):
    uncaveated = tmp_path / "claims.json"
    uncaveated.write_text(
        json.dumps(
            [
                {
                    "claim_id": "claim-bad",
                    "capability": "unsafe_overclaim",
                    "proposal_claim": "Parker solves real-world effortful speech.",
                    "grant_criterion": "construct_validity",
                    "metric_ids": ["some_metric"],
                    "report_paths": ["benchmark/reports/task_taxonomy_eval_latest.json"],
                    "required_assertions": [],
                    "baseline": "none",
                    "safety_gate": "none",
                    "caveat": "",
                    "public_private_scope": "public_synthetic_only",
                }
            ]
        )
    )

    with pytest.raises(ValueError, match="caveat"):
        load_claims(uncaveated)


def test_claim_metric_map_rejects_claims_without_real_baseline_or_safety_gate(tmp_path):
    weak_claims = tmp_path / "claims.json"
    weak_claims.write_text(
        json.dumps(
            [
                {
                    "claim_id": "claim-weak",
                    "capability": "weak_evidence",
                    "proposal_claim": "Parker has grant-ready interaction evidence.",
                    "grant_criterion": "construct_validity",
                    "metric_ids": ["unsafe_miss_count"],
                    "report_paths": ["benchmark/reports/task_taxonomy_eval_latest.json"],
                    "required_assertions": [
                        {
                            "report_path": "benchmark/reports/task_taxonomy_eval_latest.json",
                            "json_path": "metrics.unsafe_miss_count",
                            "operator": "eq",
                            "expected": 0,
                        }
                    ],
                    "baseline": "",
                    "safety_gate": "none",
                    "caveat": "Synthetic/local evidence only; not real patient proof and no private data.",
                    "public_private_scope": "public_synthetic_only",
                }
            ]
        )
    )

    with pytest.raises(ValueError, match="baseline"):
        load_claims(weak_claims)

    payload = json.loads(weak_claims.read_text())
    payload[0]["baseline"] = "reference synthetic trace"
    payload[0]["safety_gate"] = ""
    weak_claims.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="safety_gate"):
        load_claims(weak_claims)


def test_claim_metric_map_cli_json_outputs_grant_ready_gate():
    completed = subprocess.run(
        [sys.executable, str(EVALUATOR), "--json"],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["eval"] == "claim_metric_map_v0"
    assert payload["overclaim_gate"]["passed"] is True
    assert payload["overclaim_gate"]["private_data"] == "none"
