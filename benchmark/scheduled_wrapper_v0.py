"""Schema loader for synthetic Parker scheduled-wrapper contract traces."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
_REQUIRED_SCENARIO_FIELDS = {
    "scenario_id",
    "description",
    "resources",
    "events",
    "final_state",
    "receipt",
}


def load_scenarios(path: Path) -> list[dict[str, Any]]:
    """Load bounded, synthetic-only wrapper traces and reject malformed fixtures."""

    payload = json.loads(path.read_text())
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("scheduled-wrapper fixture needs schema_version 1")
    if payload.get("privacy") != "synthetic":
        raise ValueError("scheduled-wrapper fixtures must be explicitly synthetic")
    claim_boundary = payload.get("claim_boundary")
    if not isinstance(claim_boundary, str) or "no live key" not in claim_boundary.lower():
        raise ValueError("scheduled-wrapper fixture needs the no-live-key claim boundary")
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("scheduled-wrapper fixture needs at least one scenario")
    if len(scenarios) > 32:
        raise ValueError("scheduled-wrapper fixture exceeds the 32-scenario bound")

    seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    for row in scenarios:
        if not isinstance(row, dict) or set(row) != _REQUIRED_SCENARIO_FIELDS:
            raise ValueError("scheduled-wrapper scenarios must use the exact bounded schema")
        scenario_id = row.get("scenario_id")
        if not isinstance(scenario_id, str) or not scenario_id or len(scenario_id) > 128:
            raise ValueError("scheduled-wrapper scenario_id must be 1-128 characters")
        if scenario_id in seen:
            raise ValueError(f"duplicate scheduled-wrapper scenario_id: {scenario_id}")
        seen.add(scenario_id)
        if not isinstance(row.get("description"), str) or not row["description"]:
            raise ValueError(f"{scenario_id}: description must be non-empty")
        if not isinstance(row.get("resources"), list) or len(row["resources"]) > 16:
            raise ValueError(f"{scenario_id}: resources must be a bounded list")
        if not isinstance(row.get("events"), list) or not 1 <= len(row["events"]) <= 64:
            raise ValueError(f"{scenario_id}: events must contain 1-64 rows")
        if not isinstance(row.get("final_state"), dict) or not isinstance(row.get("receipt"), dict):
            raise ValueError(f"{scenario_id}: final_state and receipt must be objects")
        validated.append(row)
    return validated
