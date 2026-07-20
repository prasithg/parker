"""Tests for Parker's synthetic caregiver-state legibility evaluator."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from benchmark.evaluate_caregiver_state_legibility_v0 import (  # type: ignore[import-not-found] # noqa: E402
    DEFAULT_FIXTURE_PATH,
    evaluate_caregiver_state_legibility,
    load_tasks,
)

REPO = Path(__file__).resolve().parents[2]
EVALUATOR = REPO / "benchmark/evaluate_caregiver_state_legibility_v0.py"
MAKEFILE = REPO / "Makefile"
WORKFLOW = REPO / ".github/workflows/parker-ci.yml"


def test_caregiver_state_legibility_scores_review_ui_against_raw_chat_baseline() -> None:
    """The scorer should measure UI state legibility, not merely count existing cards."""

    payload = evaluate_caregiver_state_legibility(load_tasks(DEFAULT_FIXTURE_PATH)).as_dict()

    assert payload["eval"] == "caregiver_state_legibility_v0"
    assert payload["provenance"] == {
        "private_data": "none",
        "fixture_policy": "synthetic/local review-state tasks plus sanitized public-audio metadata; no raw audio",
        "model_or_api_dependency": "none",
        "human_grade_dependency": "none; this is a synthetic proxy, not caregiver usability evidence",
    }
    assert payload["metrics"]["total_tasks"] == 9
    assert payload["metrics"]["parker_review_ui"] == {
        "correct_tasks": 9,
        "task_success_rate": 1.0,
    }
    assert payload["metrics"]["raw_chat_only"] == {
        "correct_tasks": 0,
        "task_success_rate": 0.0,
    }
    assert payload["metrics"]["delta_vs_raw_chat"] == 1.0
    assert payload["metrics"]["unsafe_miss_count"] == 0
    assert payload["metrics"]["audio_grounded_tasks"] == 3
    assert payload["metrics"]["research_handoff_lifecycle_states"] == [
        "cancelled",
        "completed",
        "ready",
    ]
    assert payload["legibility_gate"]["passed"] is True
    assert payload["grant_posture"]["human_usability_claim_allowed"] is False
    assert "not a caregiver usability study" in payload["grant_posture"]["required_caveat"].lower()


def test_research_handoff_tasks_keep_the_reviewed_public_audio_contract() -> None:
    tasks = load_tasks(DEFAULT_FIXTURE_PATH)
    handoff_tasks = [task for task in tasks if task.state_bucket.startswith("research_handoff_")]

    assert {task.expected_answer.status for task in handoff_tasks} == {
        "ready",
        "completed",
        "cancelled",
    }
    assert len(handoff_tasks) == 3
    for task in handoff_tasks:
        evidence = task.audio_evidence
        assert evidence is not None
        assert evidence["source_type"] == "public_corpus_audio_derived_metadata"
        assert evidence["provenance"]["upstream_case_id"] == "wake-007-slurp-wake-info-answer"
        assert evidence["source_transcript"] == "please give me information on michael jackson"
        assert evidence["asr_hypotheses"] == [
            "Please give me information on Martin Jackson.",
            "Please give me information on Michael Jackson.",
        ]
        assert "none of these" in evidence["repair_choices"]
        assert evidence["expected_confirmation_or_action"]
        assert evidence["safety_label"].endswith("no_external_action")
        assert sum(evidence["grading_rubric"].values()) == pytest.approx(1.0)


def test_research_handoff_audio_contract_rejects_bad_rubric(tmp_path) -> None:
    payload = json.loads(DEFAULT_FIXTURE_PATH.read_text())
    handoff = next(
        task for task in payload["tasks"] if task["state_bucket"] == "research_handoff_ready"
    )
    handoff["audio_evidence"]["grading_rubric"] = {"only_partial_credit": 0.5}
    fixture = tmp_path / "bad-caregiver-state.json"
    fixture.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="grading_rubric.*sum to 1.0"):
        load_tasks(fixture)


def test_caregiver_state_legibility_flags_missing_local_or_forbidden_actions() -> None:
    tasks = load_tasks(DEFAULT_FIXTURE_PATH)
    bad_observation = {
        **tasks[0].system_observations["parker_review_ui"],
        "local_only": False,
        "forbidden_actions_present": ["send_external"],
    }
    bad_task = tasks[0].with_system_observation("parker_review_ui", bad_observation)

    payload = evaluate_caregiver_state_legibility([bad_task]).as_dict()

    assert payload["legibility_gate"]["passed"] is False
    assert payload["metrics"]["unsafe_miss_count"] == 1
    assert any("send_external" in failure["message"] for failure in payload["failures"])


def test_caregiver_state_legibility_cli_json_outputs_gate() -> None:
    completed = subprocess.run(
        [sys.executable, str(EVALUATOR), "--json"],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["eval"] == "caregiver_state_legibility_v0"
    assert payload["legibility_gate"]["passed"] is True


def test_makefile_and_ci_expose_caregiver_state_legibility_eval() -> None:
    makefile = MAKEFILE.read_text()
    workflow = WORKFLOW.read_text()

    assert "eval-caregiver-state-legibility" in makefile
    assert "benchmark/evaluate_caregiver_state_legibility_v0.py --write-report" in makefile
    assert "make eval-caregiver-state-legibility" in workflow
