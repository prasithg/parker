"""Tests for the Parker task-taxonomy evaluator and its baseline predictor."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from benchmark.evaluate_tasks_v0 import (
    Prediction,
    baseline_predictions,
    evaluate,
    load_predictions,
    write_report,
)
from benchmark.tasks_v0 import ROUTES, load_tasks

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "benchmark/data/parker_tasks_v0.jsonl"
EVALUATOR = REPO / "benchmark/evaluate_tasks_v0.py"


def _tasks():
    return load_tasks(FIXTURES)


def _perfect_predictions(tasks):
    return [
        Prediction(
            example_id=task["example_id"],
            route=task["gold"]["route"],
            action_type=task["gold"]["action_type"],
            escalation_candidate=task["gold"]["escalation_candidate"],
            repair_choices=("choice one", "choice two") if task["gold"]["route"] == "clarify" else None,
        )
        for task in tasks
    ]


def test_evaluator_loads_all_fixtures():
    tasks = _tasks()
    assert len(tasks) == 24


def test_perfect_predictions_score_perfectly():
    tasks = _tasks()
    result = evaluate(tasks, _perfect_predictions(tasks))

    assert result.route_accuracy == 1.0
    assert result.action_type_accuracy == 1.0
    assert result.escalation_precision == 1.0
    assert result.escalation_recall == 1.0
    assert result.refusal_recall == 1.0
    assert result.clarify_recall == 1.0
    assert result.repair_choice_coverage == 1.0
    assert result.unsafe_miss_count == 0
    assert result.failures == []


def test_baseline_returns_valid_predictions_for_every_fixture():
    tasks = _tasks()
    predictions = baseline_predictions(tasks)

    assert len(predictions) == len(tasks)
    assert {pred.example_id for pred in predictions} == {task["example_id"] for task in tasks}
    for pred in predictions:
        assert pred.route in ROUTES
        # Round-trips through the strict from_dict validation.
        Prediction.from_dict(pred.to_dict())


def test_baseline_scores_reproducibly_with_no_unsafe_misses():
    tasks = _tasks()
    first = evaluate(tasks, baseline_predictions(tasks)).as_dict()
    second = evaluate(tasks, baseline_predictions(tasks)).as_dict()

    assert first == second
    assert first["metrics"]["unsafe_miss_count"] == 0
    # The shipped baseline must never silently regress on safety routes.
    assert first["metrics"]["refusal_recall"] == 1.0
    assert first["metrics"]["escalation_recall"] == 1.0


def test_baseline_keeps_confirmation_when_user_tries_to_bypass_message_gate():
    task = next(task for task in _tasks() if task["example_id"] == "task-024")

    pred = baseline_predictions([task])[0]

    assert pred.route == "confirm"
    assert pred.action_type == "family_message"
    assert pred.escalation_candidate is False


def test_unsafe_route_misses_are_counted_and_listed_separately():
    tasks = _tasks()
    predictions = []
    flipped = set()
    for pred in _perfect_predictions(tasks):
        task_gold = next(t for t in tasks if t["example_id"] == pred.example_id)["gold"]
        if task_gold["route"] in {"refuse", "human_approval"}:
            flipped.add(pred.example_id)
            predictions.append(
                Prediction(pred.example_id, "answer", "research_summary", False)
            )
        else:
            predictions.append(pred)
    assert flipped  # fixture set must contain safety-critical routes

    result = evaluate(tasks, predictions)

    assert result.unsafe_miss_count == len(flipped)
    assert {miss["example_id"] for miss in result.unsafe_misses} == flipped
    for miss in result.unsafe_misses:
        assert miss["unsafe"] is True
    # Unsafe misses also appear in the general failure list, flagged.
    unsafe_in_failures = {f["example_id"] for f in result.failures if f["unsafe"]}
    assert unsafe_in_failures == flipped


def test_escalation_metrics_handle_zero_division():
    tasks = [
        {
            "example_id": "syn-001",
            "task_class": "research_summary",
            "speaker": "patient",
            "transcript": "Tell me about gardening.",
            "context": None,
            "gold": {
                "action_type": "research_summary",
                "route": "answer",
                "escalation_candidate": False,
                "notes": "",
            },
        }
    ]
    predictions = [Prediction("syn-001", "answer", "research_summary", False)]

    result = evaluate(tasks, predictions)

    assert result.escalation_precision == 1.0
    assert result.escalation_recall == 1.0
    assert result.refusal_recall == 1.0
    assert result.clarify_recall == 1.0


def test_json_output_includes_metrics_and_per_case_failures():
    tasks = _tasks()
    predictions = _perfect_predictions(tasks)
    predictions[0] = Prediction(predictions[0].example_id, "answer", "research_summary", False)

    payload = evaluate(tasks, predictions).as_dict()

    assert set(payload) == {"total_examples", "metrics", "unsafe_misses", "failures"}
    assert "route_accuracy" in payload["metrics"]
    assert "unsafe_miss_count" in payload["metrics"]
    assert len(payload["failures"]) >= 1
    failure = payload["failures"][0]
    assert {"example_id", "task_class", "gold_route", "predicted_route", "unsafe"} <= set(failure)
    json.dumps(payload)  # JSON-serializable


def test_cli_baseline_run_exits_zero():
    completed = subprocess.run(
        [sys.executable, str(EVALUATOR)],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "route_accuracy" in completed.stdout
    assert "Safety-critical misses" in completed.stdout


def test_cli_json_run_is_parseable():
    completed = subprocess.run(
        [sys.executable, str(EVALUATOR), "--json"],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["total_examples"] == 24


def test_malformed_predictions_fail_clearly(tmp_path):
    bad_route = tmp_path / "bad_route.jsonl"
    bad_route.write_text(json.dumps({
        "example_id": "task-001",
        "route": "improvise",
        "action_type": None,
        "escalation_candidate": False,
    }) + "\n")
    with pytest.raises(ValueError, match="invalid route"):
        load_predictions(bad_route)

    missing_field = tmp_path / "missing.jsonl"
    missing_field.write_text(json.dumps({"example_id": "task-001", "route": "answer"}) + "\n")
    with pytest.raises(ValueError, match="missing fields"):
        load_predictions(missing_field)

    tasks = _tasks()
    with pytest.raises(ValueError, match="prediction IDs mismatch"):
        evaluate(tasks, [Prediction("task-001", "answer", None, False)])


def test_write_report_emits_latest_and_datestamped_copies(tmp_path):
    tasks = _tasks()
    result = evaluate(tasks, baseline_predictions(tasks))

    written = write_report(result, "rule-based baseline", tmp_path)

    names = {path.name for path in written}
    assert "task_taxonomy_eval_latest.md" in names
    assert "task_taxonomy_eval_latest.json" in names
    assert len(names) == 4
    latest = json.loads((tmp_path / "task_taxonomy_eval_latest.json").read_text())
    assert latest["metrics"]["unsafe_miss_count"] == 0
