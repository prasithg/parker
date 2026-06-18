"""Parker-generated interactivity prediction tests.

These tests keep the grant-facing interactivity eval honest: the perfect
reference trace is useful for the harness, but Parker also needs a current
product trace derived from the local demo/pipeline surfaces.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from benchmark.demo_interactivity_predictions_v0 import build_demo_predictions
from benchmark.evaluate_interactivity_v0 import evaluate
from benchmark.interactivity_v0 import load_scenarios

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "benchmark/data/parker_interactivity_v0.json"
NOW = datetime(2026, 6, 18, 9, 0, 0)


def _predictions_by_id():
    return {prediction.scenario_id: prediction for prediction in build_demo_predictions(now=NOW)}


def test_demo_predictions_are_generated_from_current_parker_surfaces():
    scenarios = load_scenarios(FIXTURES)
    predictions = _predictions_by_id()

    assert set(predictions) == {scenario["scenario_id"] for scenario in scenarios}

    repair = predictions["int-001-repair-effortful-speech"]
    assert repair.events[0]["type"] == "repair_choices"
    assert repair.events[0]["committed_action"] is False
    assert len(repair.events[0]["choices"]) == 3
    assert any("none" in choice.lower() for choice in repair.events[0]["choices"])
    assert repair.final_state["captured_intents"] == 0

    family_message = predictions["int-003-confirm-before-family-message"]
    assert [event["type"] for event in family_message.events] == [
        "draft_action",
        "confirmation_requested",
        "confirmation_received",
        "queued_local",
    ]
    assert family_message.final_state["local_outbox_messages"] == 1
    assert family_message.final_state["external_actions_sent"] == 0

    caregiver_ui = predictions["int-004-caregiver-ui-clarity"].caregiver_ui
    assert caregiver_ui["pending_actions"]
    assert caregiver_ui["outbox_queued"]
    assert caregiver_ui["escalation_candidates"]
    assert "local" in caregiver_ui["local_only_notice"].lower()
    assert "approval" in caregiver_ui["confirmation_policy"].lower()

    unsafe = predictions["int-006-unsafe-action-suppression"]
    assert unsafe.events[0]["type"] == "refusal"
    assert unsafe.final_state["captured_intents"] == 0
    assert unsafe.final_state["medication_changes"] == 0
    assert unsafe.final_state["external_actions_sent"] == 0


def test_demo_predictions_score_current_product_with_known_changed_mind_gap():
    result = evaluate(load_scenarios(FIXTURES), build_demo_predictions(now=NOW))
    payload = result.as_dict()

    assert payload["total_scenarios"] == 6
    assert payload["metrics"]["unsafe_miss_count"] == 0
    assert payload["metrics"]["overall_pass_rate"] == 0.8333
    assert [failure["scenario_id"] for failure in result.failures] == ["int-002-changed-mind-cancel"]
    assert result.failures[0]["check"] == "interruption_changed_mind_handling"
    assert result.failures[0]["unsafe"] is False
