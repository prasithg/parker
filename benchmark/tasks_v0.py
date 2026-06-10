"""Loader/validator for Parker task taxonomy fixtures (v0).

Structural validation only: field presence, enum membership, and internal
consistency rules that hold without importing the backend. Cross-checks
against the backend action policy live in backend/tests/test_parker_task_fixtures.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

TASK_CLASSES = {
    "speech_repair",
    "family_message",
    "reminder_followup",
    "appointment_prep",
    "exercise_start",
    "media_playlist",
    "research_summary",
    "item_search",
    "non_response_escalation",
    "unsafe_request",
}

# Expected safe handling of the task, independent of v0 implementation status.
ROUTES = {"answer", "clarify", "confirm", "escalate", "refuse", "human_approval"}

SPEAKERS = {"patient", "caregiver", "system"}

REQUIRED_FIELDS = {"example_id", "task_class", "speaker", "transcript", "context", "gold"}
REQUIRED_GOLD_FIELDS = {"action_type", "route", "escalation_candidate", "notes"}


def load_tasks(path: Path) -> list[dict[str, Any]]:
    """Load and validate task fixtures; raise ValueError on the first problem."""

    rows: list[dict[str, Any]] = []
    with path.open() as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSONL: {exc}") from exc

    seen_ids: set[str] = set()
    for row in rows:
        validate_task(row)
        if row["example_id"] in seen_ids:
            raise ValueError(f"duplicate example_id: {row['example_id']}")
        seen_ids.add(row["example_id"])
    return rows


def validate_task(row: dict[str, Any]) -> None:
    """Validate one task fixture row."""

    example_id = row.get("example_id", "<unknown>")
    missing = REQUIRED_FIELDS - set(row)
    if missing:
        raise ValueError(f"task {example_id} missing fields: {sorted(missing)}")

    if row["task_class"] not in TASK_CLASSES:
        raise ValueError(f"task {example_id} invalid task_class: {row['task_class']}")
    if row["speaker"] not in SPEAKERS:
        raise ValueError(f"task {example_id} invalid speaker: {row['speaker']}")

    gold = row["gold"]
    if not isinstance(gold, dict):
        raise ValueError(f"task {example_id} gold must be object")
    gold_missing = REQUIRED_GOLD_FIELDS - set(gold)
    if gold_missing:
        raise ValueError(f"task {example_id} gold missing fields: {sorted(gold_missing)}")

    route = gold["route"]
    if route not in ROUTES:
        raise ValueError(f"task {example_id} invalid route: {route}")
    if not isinstance(gold["escalation_candidate"], bool):
        raise ValueError(f"task {example_id} escalation_candidate must be bool")

    # A task needs either an utterance or a context signal to act on.
    if row["transcript"] is None and row["context"] is None:
        raise ValueError(f"task {example_id} must have transcript or context")

    # Internal consistency rules.
    if route == "escalate" and not gold["escalation_candidate"]:
        raise ValueError(f"task {example_id}: route 'escalate' requires escalation_candidate=true")
    if route == "clarify" and gold["action_type"] is not None:
        raise ValueError(f"task {example_id}: route 'clarify' must not commit to an action_type")
    if route in {"confirm", "refuse", "human_approval", "escalate"} and gold["action_type"] is None:
        raise ValueError(f"task {example_id}: route '{route}' requires an action_type")
