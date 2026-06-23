"""Tests for deterministic repair-choice prompt construction."""

from pathlib import Path

import pytest

import sys
sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.conversation.repair import (
    MAX_LABEL_LENGTH,
    NONE_OF_THESE_LABEL,
    build_repair_prompt,
)
from app.conversation.tools import TOOL_DEFINITIONS, execute_tool
from app.db.models import CallLog, CapturedIntent
from benchmark.tasks_v0 import load_tasks

FIXTURES = Path(__file__).resolve().parents[2] / "benchmark/data/parker_tasks_v0.jsonl"


def test_two_candidates_yield_three_choices_with_none_of_these_last():
    prompt = build_repair_prompt([
        ("call Mary", "family_message"),
        ("remind you to call Mary", "reminder"),
    ])

    assert len(prompt.choices) == 3
    assert prompt.choices[-1].label == NONE_OF_THESE_LABEL
    assert prompt.choices[-1].action_type is None
    assert [choice.action_type for choice in prompt.candidates] == ["family_message", "reminder"]


def test_three_candidates_yield_four_choices():
    prompt = build_repair_prompt([
        ("send Sarah a message", "family_message"),
        ("set a reminder", "reminder"),
        ("you were just telling me about your day", None),
    ])

    assert len(prompt.choices) == 4
    assert prompt.candidates[2].action_type is None


def test_candidate_count_is_enforced():
    with pytest.raises(ValueError, match="2-3 candidates"):
        build_repair_prompt([("only one choice", "reminder")])
    with pytest.raises(ValueError, match="2-3 candidates"):
        build_repair_prompt([
            ("a", "reminder"), ("b", "family_message"),
            ("c", "exercise_start"), ("d", "media_playlist"),
        ])
    with pytest.raises(ValueError, match="2-3 candidates"):
        build_repair_prompt([])


def test_labels_are_validated():
    with pytest.raises(ValueError, match="must not be blank"):
        build_repair_prompt([("", "reminder"), ("ok", "family_message")])
    with pytest.raises(ValueError, match="too long"):
        build_repair_prompt([("x" * (MAX_LABEL_LENGTH + 1), "reminder"), ("ok", "family_message")])
    with pytest.raises(ValueError, match="duplicate"):
        build_repair_prompt([("call mary", "reminder"), ("Call Mary", "family_message")])
    with pytest.raises(ValueError, match="appended automatically"):
        build_repair_prompt([("None of these", None), ("ok", "reminder")])


def test_prohibited_and_unknown_action_types_are_rejected():
    with pytest.raises(ValueError, match="prohibited"):
        build_repair_prompt([
            ("adjust your medication", "medication_change"),
            ("set a reminder", "reminder"),
        ])
    with pytest.raises(ValueError, match="unknown to policy taxonomy"):
        build_repair_prompt([
            ("teleport", "teleport_patient"),
            ("set a reminder", "reminder"),
        ])


def test_prompt_never_carries_execution_state():
    prompt = build_repair_prompt([
        ("call Mary", "family_message"),
        ("remind you to call Mary", "reminder"),
    ])
    # Frozen value object: choosing requires going back through the pipeline gates.
    with pytest.raises(AttributeError):
        prompt.question = "changed"


def test_spoken_text_contains_all_choices():
    prompt = build_repair_prompt(
        [("call Mary", "family_message"), ("remind you to call Mary", "reminder")],
        question="Did you mean:",
    )
    spoken = prompt.as_spoken_text()

    assert spoken == "Did you mean: 1) call Mary, 2) remind you to call Mary, or 3) none of these?"


def _call(db):
    call = CallLog(call_sid="CA_REPAIR", call_type="check_in")
    db.add(call)
    db.commit()
    db.refresh(call)
    return call


def test_offer_repair_choices_is_registered_as_tool():
    names = {tool["function"]["name"] for tool in TOOL_DEFINITIONS}

    assert "offer_repair_choices" in names


def test_offer_repair_choices_tool_returns_spoken_prompt(db):
    call = _call(db)

    result = execute_tool(
        db,
        call.id,
        "offer_repair_choices",
        {
            "candidates": [
                {"label": "call your neighbor Mary", "action_type": "family_message"},
                {"label": "remind you to call Mary", "action_type": "reminder"},
            ],
        },
    )

    assert result["status"] == "offered"
    assert "1) call your neighbor Mary" in result["spoken_prompt"]
    assert [choice["action_type"] for choice in result["choices"]] == [
        "family_message", "reminder", None,
    ]
    assert result["choices"][-1]["label"] == NONE_OF_THESE_LABEL
    # Conversation-level only: nothing persisted.
    assert db.query(CapturedIntent).count() == 0


def test_offer_repair_choices_tool_rejects_unsafe_candidates(db):
    call = _call(db)

    result = execute_tool(
        db,
        call.id,
        "offer_repair_choices",
        {
            "candidates": [
                {"label": "adjust your medication", "action_type": "medication_change"},
                {"label": "set a reminder", "action_type": "reminder"},
            ],
        },
    )

    assert result["status"] == "rejected"
    assert "prohibited" in result["message"]
    assert db.query(CapturedIntent).count() == 0


def test_selected_repair_choice_flows_into_capture_intent(db):
    call = _call(db)

    offered = execute_tool(
        db,
        call.id,
        "offer_repair_choices",
        {
            "candidates": [
                {"label": "remind you to call Mary tomorrow", "action_type": "reminder"},
                {"label": "send Mary a message now", "action_type": "family_message"},
            ],
        },
    )
    assert offered["status"] == "offered"

    # The user picks choice 1; the model then captures that interpretation.
    picked = offered["choices"][0]
    captured = execute_tool(
        db,
        call.id,
        "capture_intent",
        {
            "intent_text": picked["label"],
            "requested_action": picked["action_type"],
            "subject": "call Mary",
        },
    )

    assert captured["status"] == "captured"
    saved = db.get(CapturedIntent, captured["captured_intent_id"])
    assert saved.status == "pending"
    assert saved.requested_action == "reminder"
    # Exactly one side effect: the pending intent, still gated by the pipeline.
    assert db.query(CapturedIntent).count() == 1


def test_clarify_fixtures_can_be_offered_repair_prompts():
    """Every speech_repair/clarify fixture supports a valid synthetic repair prompt."""

    # Synthetic candidate interpretations per ambiguous fixture.
    candidates_by_example = {
        "task-001": [
            ("call your neighbor with the garden", "family_message"),
            ("remind you to visit the garden", "reminder"),
        ],
        "task-002": [
            ("message her about Thursday's doctor visit", "family_message"),
            ("prepare notes for the Thursday appointment", "appointment_note"),
            ("something else on Thursday", None),
        ],
    }
    clarify_tasks = [task for task in load_tasks(FIXTURES) if task["gold"]["route"] == "clarify"]
    assert clarify_tasks

    for task in clarify_tasks:
        candidates = candidates_by_example[task["example_id"]]
        prompt = build_repair_prompt(candidates)
        assert 3 <= len(prompt.choices) <= 4
        assert prompt.choices[-1].action_type is None
