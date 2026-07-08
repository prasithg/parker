"""Tests for Parker's wake/addressed-to-me audio-context evaluator."""

import json
import subprocess
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from benchmark.evaluate_wake_context_audio_v0 import (  # type: ignore[import-not-found] # noqa: E402
    DEFAULT_CASES_PATH,
    evaluate,
    load_cases,
)

REPO = Path(__file__).resolve().parents[2]
EVALUATOR = REPO / "benchmark/evaluate_wake_context_audio_v0.py"
MAKEFILE = REPO / "Makefile"


def test_wake_context_cases_pass_and_cover_context_lanes() -> None:
    cases = load_cases(DEFAULT_CASES_PATH)
    payload = evaluate(cases)
    metrics = payload["metrics"]

    assert metrics["total_cases"] == 7
    assert metrics["public_corpus_audio_derived_cases"] == 7
    assert metrics["ambient_cases"] == 3
    assert metrics["wake_confirmed_cases"] == 4
    assert metrics["ambient_noop_cases"] == 3
    assert metrics["wake_answer_cases"] == 3
    assert metrics["wake_repair_choice_cases"] == 1
    assert metrics["unsafe_cases"] == 0
    assert metrics["nuisance_choice_failures"] == 0
    assert payload["gate"]["passed"] is True


def test_wake_context_ambient_rows_are_silent_noops_not_repair_prompts() -> None:
    payload = evaluate(load_cases(DEFAULT_CASES_PATH))
    ambient = [
        result
        for result in payload["results"]
        if result["context"].get("addressed_to_parker") is False
    ]

    assert len(ambient) == 3
    for result in ambient:
        assert result["observed_kind"] == "ambient_noop"
        assert result["speech"] == ""
        assert result["choice_count"] == 0
        assert result["captured_intents"] == 0


def test_wake_context_wake_rows_split_answers_from_confirmation_gated_actions() -> None:
    payload = evaluate(load_cases(DEFAULT_CASES_PATH))
    by_id = {result["case_id"]: result for result in payload["results"]}

    assert by_id["wake-004-slurp-wake-chat-answer"]["observed_kind"] == "answer"
    assert by_id["wake-005-slurp-wake-events-answer"]["observed_kind"] == "answer"
    assert by_id["wake-007-slurp-wake-info-answer"]["observed_kind"] == "answer"
    media = by_id["wake-006-slurp-wake-media-still-repairs"]
    assert media["observed_kind"] == "choices"
    assert media["first_choice_action_type"] == "media_playlist"
    assert media["captured_intents"] == 0


def test_wake_context_cli_json_outputs_gate() -> None:
    completed = subprocess.run(
        [sys.executable, str(EVALUATOR), "--json"],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["eval"] == "wake_context_audio_v0"
    assert payload["gate"]["passed"] is True
    assert payload["metrics"]["total_cases"] == 7


def test_makefile_exposes_wake_context_eval() -> None:
    makefile = MAKEFILE.read_text()

    assert "eval-wake-context" in makefile
    assert "benchmark/evaluate_wake_context_audio_v0.py --write-report" in makefile
