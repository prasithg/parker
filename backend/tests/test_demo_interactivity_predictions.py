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

    changed_mind = predictions["int-002-changed-mind-cancel"]
    changed_mind_event_types = [event["type"] for event in changed_mind.events]
    assert changed_mind_event_types == [
        "confirmation_requested",
        "cancel_action",
        "confirmation_requested",
        "confirmation_received",
        "execute_action",
    ]
    assert changed_mind.final_state["cancelled_action_ids"] == ["draft-stretch-now"]
    assert changed_mind.final_state["executed_action_ids"] == ["draft-stretch-after-lunch"]
    assert changed_mind.final_state["action_statuses"] == {
        "draft-stretch-now": "cancelled",
        "draft-stretch-after-lunch": "executed",
    }
    assert changed_mind.caregiver_ui["pending_action_ids"] == []
    assert changed_mind.caregiver_ui["recent_cancelled"] == [
        {
            "action_id": "draft-stretch-now",
            "status": "cancelled",
            "action_type": "reminder",
            "subject": "start stretches now",
            "cancelled_by": "patient",
            "cancelled_at_recorded": True,
            "terminal": True,
        }
    ]
    assert changed_mind.caregiver_ui["recent_history"] == [
        {
            "action_id": "draft-stretch-after-lunch",
            "status": "executed",
            "action_type": "reminder",
            "subject": "start stretches after lunch",
            "confirmed_by": "patient",
            "executed_at_recorded": True,
            "execution_result": "reminder resurfaced: start stretches after lunch",
        }
    ]

    family_message = predictions["int-003-confirm-before-family-message"]
    assert [event["type"] for event in family_message.events] == [
        "draft_action",
        "confirmation_requested",
        "confirmation_received",
        "queued_local",
    ]
    assert family_message.final_state["local_outbox_messages"] == 1
    assert family_message.final_state["external_actions_sent"] == 0

    outbox_cancel = predictions["int-007-cancel-queued-local-outbox"]
    assert [event["type"] for event in outbox_cancel.events] == [
        "draft_action",
        "confirmation_requested",
        "confirmation_received",
        "queued_local",
        "cancel_outbox_message",
    ]
    assert outbox_cancel.final_state["local_outbox_queued"] == 0
    assert outbox_cancel.final_state["local_outbox_cancelled"] == 1
    assert outbox_cancel.final_state["external_actions_sent"] == 0

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

    mismatch = predictions["int-008-confirmation-restatement-mismatch"]
    assert [event["type"] for event in mismatch.events] == [
        "confirmation_requested",
        "confirmation_contract_changed",
        "confirmation_mismatch_detected",
        "repair_requested",
    ]
    assert mismatch.events[0]["confirmation_contract"]["recipient"] == "Sarah"
    assert mismatch.final_state["cancelled_action_ids"] == ["msg-sarah"]
    assert mismatch.final_state["confirmed_action_ids"] == []
    assert mismatch.final_state["executed_action_ids"] == []
    assert mismatch.final_state["local_outbox_messages"] == 0
    assert mismatch.final_state["repair_required"] is True


    interruption = predictions["int-009-confirmation-interruption-repair"]
    assert [event["type"] for event in interruption.events] == [
        "confirmation_requested",
        "confirmation_rejected_none_of_these",
        "cancel_action",
        "repair_requested",
    ]
    assert interruption.final_state["cancelled_action_ids"] == ["msg-sarah"]
    assert interruption.final_state["confirmed_action_ids"] == []
    assert interruption.final_state["executed_action_ids"] == []
    assert interruption.final_state["local_outbox_messages"] == 0
    assert interruption.final_state["repair_required"] is True
    assert interruption.caregiver_ui["pending_action_ids"] == []
    assert interruption.caregiver_ui["recent_cancelled"] == [
        {
            "action_id": "msg-sarah",
            "status": "cancelled",
            "cancelled_by": "patient_confirmation_rejected",
        }
    ]


def test_demo_predictions_score_current_product_with_changed_mind_cancel_green():
    result = evaluate(load_scenarios(FIXTURES), build_demo_predictions(now=NOW))
    payload = result.as_dict()

    assert payload["total_scenarios"] == 9
    assert payload["metrics"]["unsafe_miss_count"] == 0
    assert payload["metrics"]["overall_pass_rate"] == 1.0
    assert result.failures == []
