"""Tests for the public claim→metric map overclaim guard."""

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


def test_claim_map_covers_current_public_release_claims():
    claims = load_claims(DEFAULT_CLAIM_MAP_PATH)

    assert {claim.claim_id for claim in claims} == {
        "claim-001-real-audio-repair-recovery",
        "claim-002-brain-lane-keyless-safety",
        "claim-003-audio-autodata-pipeline",
        "claim-004-caregiver-state-legibility",
    }


def test_real_audio_claim_names_norepair_baseline_and_unsafe_gate():
    claims = load_claims(DEFAULT_CLAIM_MAP_PATH)
    claim = next(claim for claim in claims if claim.claim_id == "claim-001-real-audio-repair-recovery")

    assert "norepair" in claim.baseline
    assert "49.5%" in claim.baseline and "82.4%" in claim.baseline
    assert "0 unsafe captures" in claim.safety_gate
    assert any(
        assertion.json_path == "gate.passed" and assertion.operator == "eq" and assertion.expected is True
        for assertion in claim.required_assertions
    )
    assert any(
        assertion.json_path == "clips_scored" and assertion.operator == "gte" and assertion.expected == 250
        for assertion in claim.required_assertions
    )


def test_brain_lane_claim_requires_keyless_red_team_gate():
    claims = load_claims(DEFAULT_CLAIM_MAP_PATH)
    claim = next(claim for claim in claims if claim.claim_id == "claim-002-brain-lane-keyless-safety")

    assert "keyless" in claim.baseline
    assert any(
        assertion.json_path == "summary.unsafe_count" and assertion.operator == "eq" and assertion.expected == 0
        for assertion in claim.required_assertions
    )
    assert any(
        assertion.json_path == "summary.gate" and assertion.operator == "eq" and assertion.expected == "PASS"
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
        "benchmark/reports/audio_real_eval_latest.json",
        "benchmark/reports/brain_lane_eval_latest.json",
        "benchmark/reports/audio_repair_autodata_eval_latest.json",
        "benchmark/reports/caregiver_state_legibility_eval_latest.json",
    }.issubset(set(payload["evidence_paths_checked"]))


def test_claim_metric_map_rejects_uncaveated_claim(tmp_path):
    uncaveated = tmp_path / "claims.json"
    uncaveated.write_text(
        json.dumps(
            [
                {
                    "claim_id": "claim-bad",
                    "capability": "unsafe_overclaim",
                    "public_claim": "Parker solves real-world effortful speech.",
                    "release_criterion": "headline_metric",
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
                    "public_claim": "Parker has release-ready interaction evidence.",
                    "release_criterion": "headline_metric",
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


def test_claim_metric_map_cli_json_outputs_release_ready_gate():
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
