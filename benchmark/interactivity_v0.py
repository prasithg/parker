"""Loader/validator for Parker interactivity eval fixtures (v0).

The fixture set is deliberately synthetic-only and tool-agnostic. It models
interactive traces rather than private conversations so it can later graduate
to a public eval/tooling repo with approval.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DIMENSIONS = {
    "repair_under_uncertain_speech",
    "interruption_changed_mind_handling",
    "confirmation_before_action",
    "caregiver_ui_clarity",
    "latency_turn_count",
    "unsafe_action_suppression",
    "local_outbox_reversibility",
}

REQUIRED_SCENARIO_FIELDS = {
    "scenario_id",
    "dimension",
    "title",
    "privacy",
    "modalities",
    "thinking_machines_alignment",
    "script",
    "gold",
}
REQUIRED_GOLD_FIELDS = {
    "checks",
    "max_turns",
    "max_assistant_latency_ms",
    "forbidden_events",
    "ideal_prediction",
}


def load_scenarios(path: Path) -> list[dict[str, Any]]:
    """Load and validate the Parker interactivity scenario JSON file."""

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON array of scenarios")

    seen_ids: set[str] = set()
    scenarios: list[dict[str, Any]] = []
    for row in data:
        if not isinstance(row, dict):
            raise ValueError(f"{path}: every scenario must be an object")
        validate_scenario(row)
        scenario_id = row["scenario_id"]
        if scenario_id in seen_ids:
            raise ValueError(f"duplicate scenario_id: {scenario_id}")
        seen_ids.add(scenario_id)
        scenarios.append(row)
    return scenarios


def validate_scenario(row: dict[str, Any]) -> None:
    """Validate one interactivity fixture row."""

    scenario_id = str(row.get("scenario_id", "<unknown>"))
    missing = REQUIRED_SCENARIO_FIELDS - set(row)
    if missing:
        raise ValueError(f"scenario {scenario_id} missing fields: {sorted(missing)}")

    if row["dimension"] not in DIMENSIONS:
        raise ValueError(f"scenario {scenario_id} invalid dimension: {row['dimension']}")
    if row["privacy"] != "synthetic":
        raise ValueError(f"scenario {scenario_id} privacy must be synthetic")
    if not isinstance(row["modalities"], list) or not row["modalities"]:
        raise ValueError(f"scenario {scenario_id} modalities must be a non-empty list")
    if not isinstance(row["thinking_machines_alignment"], list) or not row["thinking_machines_alignment"]:
        raise ValueError(f"scenario {scenario_id} thinking_machines_alignment must be non-empty")
    if not isinstance(row["script"], list) or not row["script"]:
        raise ValueError(f"scenario {scenario_id} script must be a non-empty list")
    for index, step in enumerate(row["script"], start=1):
        if not isinstance(step, dict):
            raise ValueError(f"scenario {scenario_id} script step {index} must be an object")
        if step.get("synthetic", True) is not True:
            raise ValueError(f"scenario {scenario_id} script step {index} must be synthetic")

    gold = row["gold"]
    if not isinstance(gold, dict):
        raise ValueError(f"scenario {scenario_id} gold must be an object")
    missing_gold = REQUIRED_GOLD_FIELDS - set(gold)
    if missing_gold:
        raise ValueError(f"scenario {scenario_id} gold missing fields: {sorted(missing_gold)}")
    checks = gold["checks"]
    if not isinstance(checks, list) or not checks:
        raise ValueError(f"scenario {scenario_id} gold checks must be a non-empty list")
    unknown_checks = set(checks) - DIMENSIONS
    if unknown_checks:
        raise ValueError(f"scenario {scenario_id} unknown checks: {sorted(unknown_checks)}")
    if row["dimension"] not in checks:
        raise ValueError(f"scenario {scenario_id} checks must include its primary dimension")
    if not isinstance(gold["max_turns"], int) or gold["max_turns"] <= 0:
        raise ValueError(f"scenario {scenario_id} max_turns must be a positive integer")
    if not isinstance(gold["max_assistant_latency_ms"], int) or gold["max_assistant_latency_ms"] <= 0:
        raise ValueError(f"scenario {scenario_id} max_assistant_latency_ms must be a positive integer")
    if not isinstance(gold["forbidden_events"], list):
        raise ValueError(f"scenario {scenario_id} forbidden_events must be a list")

    if "caregiver_ui_clarity" in checks:
        required = gold.get("caregiver_ui_required")
        if not isinstance(required, list) or not required:
            raise ValueError(f"scenario {scenario_id} caregiver_ui_required must be a non-empty list")

    if "interruption_changed_mind_handling" in checks:
        for field_name in (
            "prior_action_id",
            "revised_action_id",
            "revised_action_type",
            "revised_execution_event_type",
            "expected_active_subject",
        ):
            value = gold.get(field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"scenario {scenario_id} {field_name} must be a non-empty string")
        if gold["prior_action_id"] == gold["revised_action_id"]:
            raise ValueError(f"scenario {scenario_id} revised_action_id must differ from prior_action_id")

    ideal = gold["ideal_prediction"]
    if not isinstance(ideal, dict):
        raise ValueError(f"scenario {scenario_id} ideal_prediction must be an object")
    if not isinstance(ideal.get("events"), list):
        raise ValueError(f"scenario {scenario_id} ideal_prediction.events must be a list")
    if "total_turns" in ideal and (not isinstance(ideal["total_turns"], int) or ideal["total_turns"] <= 0):
        raise ValueError(f"scenario {scenario_id} ideal_prediction.total_turns must be positive")
    if not isinstance(ideal.get("final_state", {}), dict):
        raise ValueError(f"scenario {scenario_id} ideal_prediction.final_state must be an object")
    if not isinstance(ideal.get("caregiver_ui", {}), dict):
        raise ValueError(f"scenario {scenario_id} ideal_prediction.caregiver_ui must be an object")
