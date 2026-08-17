"""Evaluate Parker's synthetic no-agent scheduled-wrapper deployment contract.

This evaluator checks a public-safe contract and adversarial traces only. It does
not read a scheduler key, deploy a wrapper, alter cron, or claim that any real
scheduled event passed Parker's operational provenance verifier.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

try:
    from benchmark.scheduled_wrapper_v0 import load_scenarios
except ImportError:  # pragma: no cover - direct script execution
    from scheduled_wrapper_v0 import load_scenarios

DEFAULT_FIXTURES = Path(__file__).resolve().parent / "data" / "scheduled_wrapper_contract_v0.json"
CHECKS = (
    "scheduler_only_key_access",
    "verifier_only_key_handoff",
    "protected_ledger_ownership",
    "pending_ack_protocol",
    "sanitized_output",
)
PROVENANCE = {
    "private_data": "none",
    "fixture_policy": "synthetic wrapper traces only",
    "claim_status": "contract evidence only; no live key, deployment, or genuine scheduled event",
}
_ALLOWED_RECEIPT_FIELDS = {
    "schema_version",
    "scenario_id",
    "verdict",
    "stage",
    "failed_assertions",
    "claim_boundary",
}
_FORBIDDEN_WORKER_INPUT_FRAGMENTS = ("key", "secret", "token", "envelope", "nonce", "ledger", "ack")
_FORBIDDEN_RECEIPT_KEY_FRAGMENTS = ("key", "secret", "token", "nonce", "path", "command", "stdout", "stderr")
_MAX_RECEIPT_BYTES = 16 * 1024
_EXPECTED_CLAIM_BOUNDARY = (
    "Synthetic wrapper-contract evidence only; not a live deployment or genuine scheduled event."
)
_EVENT_SHAPES: dict[str, tuple[str, str | None]] = {
    "envelope_minted": ("scheduler", "pending_envelope"),
    "pending_created": ("scheduler", "ack_state"),
    "worker_succeeded": ("worker", None),
    "worker_failed": ("worker", None),
    "verifier_started": ("verifier_wrapper", "pending_envelope"),
    "evidence_verified": ("verifier_wrapper", None),
    "evidence_rejected": ("verifier_wrapper", None),
    "nonce_claimed": ("verifier_wrapper", "nonce_ledger"),
    "ack_committed": ("verifier_wrapper", "ack_state"),
    "pending_retained": ("verifier_wrapper", "ack_state"),
    "sanitized_receipt_written": ("verifier_wrapper", "sanitized_receipt"),
}
_SUCCESS_EVENT_TYPES = [
    "key_access",
    "envelope_minted",
    "pending_created",
    "worker_started",
    "worker_succeeded",
    "key_access",
    "verifier_started",
    "evidence_verified",
    "nonce_claimed",
    "ack_committed",
    "sanitized_receipt_written",
]
_WORKER_FAILURE_EVENT_TYPES = [
    "key_access",
    "envelope_minted",
    "pending_created",
    "worker_started",
    "worker_failed",
    "pending_retained",
    "sanitized_receipt_written",
]
_EVIDENCE_REJECTION_EVENT_TYPES = [
    "key_access",
    "envelope_minted",
    "pending_created",
    "worker_started",
    "worker_succeeded",
    "key_access",
    "verifier_started",
    "evidence_rejected",
    "pending_retained",
    "sanitized_receipt_written",
]


@dataclass(frozen=True)
class WrapperContractResult:
    synthetic_scenarios: int
    contract_checks: int
    passed_checks: int
    failures: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "eval": "scheduled_wrapper_contract_v0",
            "gate": {
                "passed": not self.failures,
                "failure_count": len(self.failures),
            },
            "metrics": {
                "synthetic_scenarios": self.synthetic_scenarios,
                "contract_checks": self.contract_checks,
                "passed_checks": self.passed_checks,
                "scheduler_key_exposure_failures": _scenario_failure_count(
                    self.failures,
                    {"scheduler_only_key_access", "verifier_only_key_handoff"},
                ),
                "protected_ledger_failures": _scenario_failure_count(
                    self.failures, {"protected_ledger_ownership"}
                ),
                "pending_ack_failures": _scenario_failure_count(
                    self.failures, {"pending_ack_protocol"}
                ),
                "sanitization_failures": _scenario_failure_count(
                    self.failures, {"sanitized_output"}
                ),
            },
            "provenance": PROVENANCE,
            "failures": self.failures,
        }


def evaluate(scenarios: list[dict[str, Any]]) -> WrapperContractResult:
    """Run five fail-closed checks over each synthetic wrapper trace."""

    if not scenarios:
        raise ValueError("scheduled-wrapper evaluation requires at least one scenario")
    failures: list[dict[str, str]] = []
    scorers: dict[str, Callable[[dict[str, Any]], tuple[bool, str]]] = {
        "scheduler_only_key_access": _score_scheduler_only_key_access,
        "verifier_only_key_handoff": _score_verifier_only_key_handoff,
        "protected_ledger_ownership": _score_protected_ledger_ownership,
        "pending_ack_protocol": _score_pending_ack_protocol,
        "sanitized_output": _score_sanitized_output,
    }
    for scenario in scenarios:
        scenario_id = str(scenario.get("scenario_id", "<malformed>"))
        for check in CHECKS:
            try:
                passed, message = scorers[check](scenario)
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                passed, message = False, f"malformed trace: {type(exc).__name__}"
            if not passed:
                failures.append({"scenario_id": scenario_id, "check": check, "message": message})
    total = len(scenarios) * len(CHECKS)
    return WrapperContractResult(
        synthetic_scenarios=len(scenarios),
        contract_checks=total,
        passed_checks=total - len(failures),
        failures=failures,
    )


def _score_scheduler_only_key_access(scenario: dict[str, Any]) -> tuple[bool, str]:
    resources = _resource_map(scenario)
    key = resources.get("scheduler_key")
    if key != {
        "name": "scheduler_key",
        "owner": "scheduler",
        "worker_access": "none",
        "scope": "protected_runtime_handle",
        "mode": "unmaterialized",
    }:
        return False, "scheduler key must remain an unmaterialized scheduler-owned handle"
    events = _events(scenario)
    worker_index = _event_index(events, "worker_started")
    worker_terminal = _first_event_index(events, {"worker_succeeded", "worker_failed"})
    if worker_index is None or worker_terminal is None or worker_terminal <= worker_index:
        return False, "worker start and terminal events must be ordered"
    key_events = [
        (index, event) for index, event in enumerate(events)
        if event.get("type") == "key_access" or event.get("target") == "scheduler_key"
    ]
    if not key_events or any(event.get("actor") not in {"scheduler", "verifier_wrapper"} for _, event in key_events):
        return False, "only scheduler and verifier wrapper may access the key"
    scheduler_access = [index for index, event in key_events if event.get("actor") == "scheduler"]
    verifier_access = [index for index, event in key_events if event.get("actor") == "verifier_wrapper"]
    if scheduler_access != [0] or scheduler_access[0] >= worker_index:
        return False, "scheduler key access must occur exactly once before the worker starts"
    if any(index <= worker_terminal for index in verifier_access):
        return False, "verifier key handoff must wait for worker completion"
    verifier_started = _event_index(events, "verifier_started")
    if verifier_started is None:
        if verifier_access:
            return False, "worker-failure trace must not hand the key to a verifier"
    elif len(verifier_access) != 1 or verifier_access[0] >= verifier_started:
        return False, "successful worker trace needs one post-worker key handoff before verifier start"
    worker_inputs = _worker_inputs(events)
    if any(any(fragment in value.lower() for fragment in _FORBIDDEN_WORKER_INPUT_FRAGMENTS) for value in worker_inputs):
        return False, "worker inputs expose scheduler-only material"
    return True, "ok"


def _score_verifier_only_key_handoff(scenario: dict[str, Any]) -> tuple[bool, str]:
    resources = _resource_map(scenario)
    envelope = resources.get("pending_envelope")
    if not isinstance(envelope, dict) or envelope.get("owner") != "scheduler":
        return False, "pending envelope must be scheduler-owned"
    if envelope.get("worker_access") != "none" or envelope.get("scope") != "protected_external" or envelope.get("mode") != "0600":
        return False, "worker must have no access to the protected pending envelope"
    events = _events(scenario)
    worker_terminal = _first_event_index(events, {"worker_succeeded", "worker_failed"})
    verifier_start = _event_index(events, "verifier_started")
    worker_inputs = _worker_inputs(events)
    if any("envelope" in value.lower() for value in worker_inputs):
        return False, "worker received the scheduler envelope"
    if verifier_start is None:
        if _event_index(events, "worker_failed") is not None:
            return True, "worker failure correctly avoided verifier handoff"
        return False, "successful worker trace must hand off to the verifier"
    if worker_terminal is None or verifier_start <= worker_terminal:
        return False, "verifier must start only after worker completion"
    verifier_event = events[verifier_start]
    if verifier_event.get("actor") != "verifier_wrapper" or verifier_event.get("target") != "pending_envelope":
        return False, "only verifier wrapper may receive the pending envelope"
    return True, "ok"


def _score_protected_ledger_ownership(scenario: dict[str, Any]) -> tuple[bool, str]:
    resources = _resource_map(scenario)
    ledger = resources.get("nonce_ledger")
    if ledger != {
        "name": "nonce_ledger",
        "owner": "verifier_wrapper",
        "worker_access": "none",
        "scope": "protected_external",
        "mode": "0700",
    }:
        return False, "nonce ledger must be external, verifier-owned, mode 0700, and worker-inaccessible"
    for event in _events(scenario):
        if event.get("type") == "nonce_claimed" and (
            event.get("actor") != "verifier_wrapper" or event.get("target") != "nonce_ledger"
        ):
            return False, "only verifier wrapper may claim the protected nonce ledger"
    return True, "ok"


def _score_pending_ack_protocol(scenario: dict[str, Any]) -> tuple[bool, str]:
    events = _events(scenario)
    state = scenario.get("final_state")
    if not isinstance(state, dict):
        return False, "final_state must be an object"
    event_types = [event.get("type") for event in events]
    worker_failed = "worker_failed" in event_types
    evidence_rejected = "evidence_rejected" in event_types
    expected_event_types = (
        _WORKER_FAILURE_EVENT_TYPES
        if worker_failed
        else _EVIDENCE_REJECTION_EVENT_TYPES
        if evidence_rejected
        else _SUCCESS_EVENT_TYPES
    )
    if event_types != expected_event_types:
        return False, "wrapper trace must use one exact bounded lifecycle sequence"
    if not _events_have_trusted_shapes(events):
        return False, "wrapper lifecycle actors, targets, and fields must match the trust contract"

    evidence_verified = "evidence_verified" in event_types
    nonce_claimed = "nonce_claimed" in event_types
    ack_committed = "ack_committed" in event_types
    if worker_failed or evidence_rejected:
        if nonce_claimed or ack_committed:
            return False, "failed or rejected evidence must not consume nonce or acknowledge pending state"
        if "pending_retained" not in event_types:
            return False, "failed or rejected evidence must retain pending state"
        expected = {"pending": True, "acknowledged": False, "nonce_claimed": False, "verdict": "unverified"}
        if any(state.get(key) != value for key, value in expected.items()):
            return False, "failed trace final state does not preserve retryable pending evidence"
        return True, "ok"
    if not evidence_verified:
        return False, "successful worker trace needs an explicit evidence verdict"
    ordered = ["worker_succeeded", "verifier_started", "evidence_verified", "nonce_claimed", "ack_committed", "sanitized_receipt_written"]
    maybe_positions = [_event_index(events, event_type) for event_type in ordered]
    if any(position is None for position in maybe_positions):
        return False, "success must verify, final-ack the nonce, then acknowledge and write receipt in order"
    positions = [position for position in maybe_positions if position is not None]
    if positions != sorted(positions) or len(set(positions)) != len(positions):
        return False, "success must verify, final-ack the nonce, then acknowledge and write receipt in order"
    expected = {"pending": False, "acknowledged": True, "nonce_claimed": True, "verdict": "verified"}
    if any(state.get(key) != value for key, value in expected.items()):
        return False, "successful trace final state is not fully acknowledged"
    return True, "ok"


def _score_sanitized_output(scenario: dict[str, Any]) -> tuple[bool, str]:
    receipt = scenario.get("receipt")
    if not isinstance(receipt, dict) or set(receipt) != _ALLOWED_RECEIPT_FIELDS:
        return False, "receipt must use the exact public-safe schema"
    if receipt.get("schema_version") != 1 or receipt.get("scenario_id") != scenario.get("scenario_id"):
        return False, "receipt schema/scenario binding is invalid"
    if receipt.get("claim_boundary") != _EXPECTED_CLAIM_BOUNDARY:
        return False, "receipt must retain the synthetic no-deployment claim boundary"
    if receipt.get("verdict") != scenario.get("final_state", {}).get("verdict"):
        return False, "receipt verdict must match final state"
    failed_assertions = receipt.get("failed_assertions")
    if not isinstance(failed_assertions, list) or any(
        not isinstance(value, str) or not value or len(value) > 128
        for value in failed_assertions
    ):
        return False, "receipt failed_assertions must be bounded strings"
    if receipt["verdict"] == "verified":
        if receipt.get("stage") != "acknowledged" or failed_assertions:
            return False, "verified receipt must be acknowledged with no failures"
    elif (
        receipt["verdict"] != "unverified"
        or not isinstance(receipt.get("stage"), str)
        or not receipt["stage"].endswith("pending_retained")
        or not failed_assertions
    ):
        return False, "unverified receipt must name a retained-pending stage and failure"
    serialized = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
    if len(serialized.encode("utf-8")) > _MAX_RECEIPT_BYTES:
        return False, "receipt exceeds the 16 KiB output bound"
    if any(any(fragment in str(key).lower() for fragment in _FORBIDDEN_RECEIPT_KEY_FRAGMENTS) for key in receipt):
        return False, "receipt key reflects protected or operational material"
    lowered = serialized.lower()
    if any(marker in lowered for marker in ("/users/", "/private/", "/home/", "http://", "https://", ".env", "synthetic-token", "synthetic-nonce")):
        return False, "receipt reflects path, source URL, secret-like, token, or raw nonce material"
    return True, "ok"


def _resource_map(scenario: dict[str, Any]) -> dict[str, dict[str, Any]]:
    resources = scenario.get("resources")
    if not isinstance(resources, list):
        raise ValueError("resources must be a list")
    result: dict[str, dict[str, Any]] = {}
    for resource in resources:
        if not isinstance(resource, dict) or not isinstance(resource.get("name"), str):
            raise ValueError("resource rows need names")
        if resource["name"] in result:
            raise ValueError("duplicate resource name")
        result[resource["name"]] = resource
    return result


def _events(scenario: dict[str, Any]) -> list[dict[str, Any]]:
    events = scenario.get("events")
    if not isinstance(events, list):
        raise ValueError("events must be a list")
    if any(not isinstance(event, dict) for event in events):
        raise ValueError("events must be objects")
    return events


def _events_have_trusted_shapes(events: list[dict[str, Any]]) -> bool:
    """Reject lifecycle rows whose actor, target, or fields weaken the contract."""

    for event in events:
        event_type = event.get("type")
        if event_type == "key_access":
            if event not in (
                {"actor": "scheduler", "type": "key_access", "target": "scheduler_key"},
                {
                    "actor": "verifier_wrapper",
                    "type": "key_access",
                    "target": "scheduler_key",
                },
            ):
                return False
            continue
        if event_type == "worker_started":
            inputs = event.get("inputs")
            if (
                set(event) != {"actor", "type", "inputs"}
                or event.get("actor") != "worker"
                or not isinstance(inputs, list)
                or not 1 <= len(inputs) <= 16
                or any(not isinstance(value, str) for value in inputs)
            ):
                return False
            continue
        shape = _EVENT_SHAPES.get(str(event_type))
        if shape is None:
            return False
        actor, target = shape
        expected = {"actor": actor, "type": event_type}
        if target is not None:
            expected["target"] = target
        if event != expected:
            return False
    return True


def _worker_inputs(events: list[dict[str, Any]]) -> list[str]:
    worker_start = next((event for event in events if event.get("type") == "worker_started"), None)
    if not isinstance(worker_start, dict) or not isinstance(worker_start.get("inputs"), list):
        raise ValueError("worker_started requires a bounded inputs list")
    if set(worker_start) != {"actor", "type", "inputs"} or worker_start.get("actor") != "worker":
        raise ValueError("worker_started must use the exact non-secret launch schema")
    inputs = worker_start["inputs"]
    if not 1 <= len(inputs) <= 16 or any(not isinstance(value, str) for value in inputs):
        raise ValueError("worker inputs must contain 1-16 strings")
    return inputs


def _event_index(events: list[dict[str, Any]], event_type: str) -> int | None:
    return next((index for index, event in enumerate(events) if event.get("type") == event_type), None)


def _first_event_index(events: list[dict[str, Any]], event_types: set[str]) -> int | None:
    return next((index for index, event in enumerate(events) if event.get("type") in event_types), None)


def _scenario_failure_count(failures: list[dict[str, str]], checks: set[str]) -> int:
    return len({failure["scenario_id"] for failure in failures if failure["check"] in checks})


def format_summary(result: WrapperContractResult) -> str:
    payload = result.as_dict()
    metrics = payload["metrics"]
    return "\n".join(
        [
            "Parker scheduled-wrapper contract eval v0",
            "",
            f"  synthetic scenarios: {metrics['synthetic_scenarios']}",
            f"  checks passed:       {metrics['passed_checks']}/{metrics['contract_checks']}",
            f"  failures:            {payload['gate']['failure_count']}",
            f"  gate passed:         {payload['gate']['passed']}",
            "",
            "Contract evidence only; no live key, deployment, or genuine scheduled event.",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = evaluate(load_scenarios(args.fixtures))
    if args.json:
        print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    else:
        print(format_summary(result))
    raise SystemExit(0 if result.as_dict()["gate"]["passed"] else 1)


if __name__ == "__main__":
    main()
