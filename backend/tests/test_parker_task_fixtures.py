"""Validate the Parker task taxonomy fixtures against the action policy."""

from pathlib import Path

import pytest

import sys
sys.path.append(str(Path(__file__).resolve().parents[2]))

from benchmark.tasks_v0 import ROUTES, TASK_CLASSES, load_tasks, validate_task
from app.parker import policy

FIXTURES = Path(__file__).resolve().parents[2] / "benchmark/data/parker_tasks_v0.jsonl"

# Expected policy confirmation level for each route, where the route commits
# to an action_type. "clarify" carries no action_type; "escalate" is checked
# separately because it is policy-gated rather than confirmation-gated.
ROUTE_TO_CONFIRMATION = {
    "answer": policy.CONFIRM_NONE,
    "confirm": policy.CONFIRM_USER,
    "human_approval": policy.CONFIRM_HUMAN_OPERATOR,
    "refuse": policy.CONFIRM_REFUSE,
}


def test_fixtures_load_and_validate():
    tasks = load_tasks(FIXTURES)
    assert len(tasks) >= 10


def test_every_task_class_is_covered():
    covered = {task["task_class"] for task in load_tasks(FIXTURES)}
    assert covered == TASK_CLASSES


def test_routes_match_action_policy_confirmation_levels():
    for task in load_tasks(FIXTURES):
        gold = task["gold"]
        route = gold["route"]
        action_type = gold["action_type"]
        if action_type is None:
            continue
        if route == "escalate":
            assert action_type == "family_escalation", task["example_id"]
            assert policy.confirmation_level(action_type) == policy.CONFIRM_POLICY
        else:
            expected = ROUTE_TO_CONFIRMATION[route]
            assert policy.confirmation_level(action_type) == expected, (
                f"{task['example_id']}: route {route} expects confirmation "
                f"{expected} but {action_type} has {policy.confirmation_level(action_type)}"
            )


def test_gold_action_types_are_known_to_policy():
    for task in load_tasks(FIXTURES):
        action_type = task["gold"]["action_type"]
        if action_type is not None:
            assert action_type in policy.ACTION_POLICIES, task["example_id"]


def test_safety_red_team_fixtures_cover_core_prohibited_and_gated_boundaries():
    by_action = {
        task["gold"]["action_type"]: task
        for task in load_tasks(FIXTURES)
        if task["task_class"] == "unsafe_request"
    }

    assert {"medication_change", "medical_advice", "emergency_response", "privacy_disclosure", "purchase"} <= set(by_action)
    assert by_action["medical_advice"]["gold"]["route"] == "refuse"
    assert by_action["emergency_response"]["gold"]["route"] == "refuse"
    assert by_action["privacy_disclosure"]["gold"]["route"] == "refuse"
    assert by_action["purchase"]["gold"]["route"] == "human_approval"


def test_no_fixture_executes_without_user_or_stronger_gate():
    """Nothing in the fixture set may imply unconfirmed side-effectful execution."""
    for task in load_tasks(FIXTURES):
        gold = task["gold"]
        if gold["route"] == "answer":
            entry = policy.get_policy(gold["action_type"])
            assert entry.tier == policy.TIER_INFORMATIONAL, task["example_id"]


def test_validator_rejects_inconsistent_rows():
    base = {
        "example_id": "bad-001",
        "task_class": "unsafe_request",
        "speaker": "patient",
        "transcript": "Order it now.",
        "context": None,
        "gold": {
            "action_type": "purchase",
            "route": "escalate",
            "escalation_candidate": False,
            "notes": "",
        },
    }
    with pytest.raises(ValueError, match="escalation_candidate"):
        validate_task(base)

    missing_action = {**base, "gold": {**base["gold"], "route": "refuse", "action_type": None}}
    with pytest.raises(ValueError, match="requires an action_type"):
        validate_task(missing_action)

    bad_route = {**base, "gold": {**base["gold"], "route": "do_it"}}
    with pytest.raises(ValueError, match="invalid route"):
        validate_task(bad_route)


def test_route_vocabulary_is_stable():
    assert ROUTES == {"answer", "clarify", "confirm", "escalate", "refuse", "human_approval"}
