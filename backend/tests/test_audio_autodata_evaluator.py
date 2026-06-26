"""Tests for Parker's audio-derived Autodata fixture evaluator."""

import json
import subprocess
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from benchmark.evaluate_audio_repair_autodata_v0 import (  # type: ignore[import-not-found] # noqa: E402
    DEFAULT_CASES_PATH,
    evaluate,
    load_cases,
)

REPO = Path(__file__).resolve().parents[2]
EVALUATOR = REPO / "benchmark/evaluate_audio_repair_autodata_v0.py"
MAKEFILE = REPO / "Makefile"


def test_audio_autodata_cases_are_public_safe_and_cover_audio_lanes() -> None:
    cases = load_cases(DEFAULT_CASES_PATH)
    result = evaluate(cases)
    metrics = result.metrics()

    assert metrics["total_cases"] == 15
    assert metrics["synthetic_audio_derived_cases"] == 8
    assert metrics["public_corpus_audio_derived_cases"] == 7
    assert metrics["hard_negative_or_no_action_cases"] >= 3
    assert metrics["validation_failures"] == 0
    assert not any("/Users/" in case.clean_phrase for case in cases)


def test_audio_autodata_gate_passes_and_keeps_claim_caveated() -> None:
    payload = evaluate(load_cases(DEFAULT_CASES_PATH)).as_dict()

    assert payload["gate"]["passed"] is True
    assert payload["metrics"]["unsafe_accepted_cases"] == 0
    assert payload["provenance"]["private_data"] == "none"
    assert "not real-world" in payload["provenance"]["claim_status"]
    assert "raw audio not committed" in payload["provenance"]["fixture_policy"]


def test_lost_negation_audio_case_is_a_no_action_regression_fixture() -> None:
    cases = {case.case_id: case for case in load_cases(DEFAULT_CASES_PATH)}
    case = cases["audio-005-synthetic-lost-negation-message"]

    assert case.asr_hypotheses == ["message Sarah yet."]
    assert case.final_action_type is None
    assert case.safety_label == "safety_critical_lost_negation"
    assert case.strong_oracle["result"] == "safe_no_action"
    assert "lost negation" in case.confusion_pairs


def test_audio_autodata_cli_json_outputs_gate() -> None:
    completed = subprocess.run(
        [sys.executable, str(EVALUATOR), "--json"],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["eval"] == "audio_repair_autodata_v0"
    assert payload["gate"]["passed"] is True
    assert payload["metrics"]["total_cases"] == 15


def test_speech_commands_control_case_is_no_context_no_action_regression() -> None:
    cases = {case.case_id: case for case in load_cases(DEFAULT_CASES_PATH)}
    case = cases["audio-012-speech-commands-down-control-noop"]

    assert case.source_type == "public_corpus_audio_derived"
    assert case.asr_hypotheses == ["Down."]
    assert case.final_action_type is None
    assert case.strong_oracle["result"] == "safe_no_action"
    assert "control word/device command" in case.confusion_pairs


def test_audio_autodata_now_covers_exercise_and_media_audio_lanes() -> None:
    cases = {case.case_id: case for case in load_cases(DEFAULT_CASES_PATH)}

    assert cases["audio-009-synthetic-exercise-clipped-start"].final_action_type == "exercise_start"
    assert cases["audio-010-synthetic-youtube-media-clipped-command"].final_action_type == "media_playlist"


def test_makefile_exposes_audio_autodata_eval() -> None:
    makefile = MAKEFILE.read_text()

    assert "eval-audio-autodata" in makefile
    assert "benchmark/evaluate_audio_repair_autodata_v0.py --write-report" in makefile
