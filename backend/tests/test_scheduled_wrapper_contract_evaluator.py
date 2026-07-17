"""Synthetic negative controls for the trusted scheduled-wrapper contract."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from benchmark.evaluate_scheduled_wrapper_v0 import evaluate
from benchmark.scheduled_wrapper_v0 import load_scenarios

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "benchmark/data/scheduled_wrapper_contract_v0.json"
EVALUATOR = REPO / "benchmark/evaluate_scheduled_wrapper_v0.py"


def _scenarios() -> list[dict]:
    return load_scenarios(FIXTURES)


def _scenario(scenario_id: str) -> dict:
    return copy.deepcopy(next(row for row in _scenarios() if row["scenario_id"] == scenario_id))


def test_reference_wrapper_contract_covers_success_and_retryable_failures():
    result = evaluate(_scenarios()).as_dict()

    assert result["gate"] == {"passed": True, "failure_count": 0}
    assert result["metrics"] == {
        "synthetic_scenarios": 3,
        "contract_checks": 15,
        "passed_checks": 15,
        "scheduler_key_exposure_failures": 0,
        "protected_ledger_failures": 0,
        "pending_ack_failures": 0,
        "sanitization_failures": 0,
    }
    assert result["provenance"] == {
        "private_data": "none",
        "fixture_policy": "synthetic wrapper traces only",
        "claim_status": "contract evidence only; no live key, deployment, or genuine scheduled event",
    }


def test_worker_key_or_envelope_handoff_is_a_hard_failure():
    leaked = _scenario("wrapper-001-success-final-ack")
    worker_start = next(event for event in leaked["events"] if event["type"] == "worker_started")
    worker_start["inputs"].extend(["scheduler_key", "scheduler_envelope"])

    result = evaluate([leaked]).as_dict()

    assert result["gate"]["passed"] is False
    assert result["metrics"]["scheduler_key_exposure_failures"] == 1
    assert any(failure["check"] == "scheduler_only_key_access" for failure in result["failures"])

    missing_handoff = _scenario("wrapper-001-success-final-ack")
    missing_handoff["events"] = [
        event for event in missing_handoff["events"]
        if not (event["type"] == "key_access" and event["actor"] == "verifier_wrapper")
    ]

    missing_result = evaluate([missing_handoff]).as_dict()

    assert missing_result["gate"]["passed"] is False
    assert any(
        failure["check"] == "scheduler_only_key_access"
        for failure in missing_result["failures"]
    )


def test_worker_access_to_nonce_ledger_is_a_hard_failure():
    writable = _scenario("wrapper-001-success-final-ack")
    ledger = next(resource for resource in writable["resources"] if resource["name"] == "nonce_ledger")
    ledger["worker_access"] = "write"
    writable["events"].insert(
        4,
        {"actor": "worker", "type": "nonce_claimed", "target": "nonce_ledger"},
    )

    result = evaluate([writable]).as_dict()

    assert result["gate"]["passed"] is False
    assert result["metrics"]["protected_ledger_failures"] == 1
    assert any(failure["check"] == "protected_ledger_ownership" for failure in result["failures"])


def test_worker_failure_cannot_ack_or_consume_pending_run():
    eager_ack = _scenario("wrapper-002-worker-failure-retains-pending")
    receipt_index = next(
        index for index, event in enumerate(eager_ack["events"])
        if event["type"] == "sanitized_receipt_written"
    )
    eager_ack["events"][receipt_index:receipt_index] = [
        {"actor": "verifier_wrapper", "type": "nonce_claimed", "target": "nonce_ledger"},
        {"actor": "verifier_wrapper", "type": "ack_committed", "target": "ack_state"},
    ]
    eager_ack["final_state"].update({"pending": False, "acknowledged": True, "nonce_claimed": True})

    result = evaluate([eager_ack]).as_dict()

    assert result["gate"]["passed"] is False
    assert result["metrics"]["pending_ack_failures"] == 1
    assert any(failure["check"] == "pending_ack_protocol" for failure in result["failures"])


def test_receipt_cannot_reflect_key_token_nonce_path_or_unbounded_output():
    reflected = _scenario("wrapper-003-verifier-failure-retains-pending")
    reflected["receipt"].update(
        {
            "token": "synthetic-token-must-not-leak",
            "nonce": "synthetic-nonce-must-not-leak",
            "input_path": "/private/synthetic-family-label.json",
            "error": "x" * 20_000,
        }
    )

    result = evaluate([reflected]).as_dict()

    assert result["gate"]["passed"] is False
    assert result["metrics"]["sanitization_failures"] == 1
    assert any(failure["check"] == "sanitized_output" for failure in result["failures"])


def test_cli_json_reports_contract_gate_without_live_activation():
    completed = subprocess.run(
        [sys.executable, str(EVALUATOR), "--json"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["gate"]["passed"] is True
    assert payload["metrics"]["synthetic_scenarios"] == 3
    assert payload["provenance"]["claim_status"].endswith("no live key, deployment, or genuine scheduled event")
