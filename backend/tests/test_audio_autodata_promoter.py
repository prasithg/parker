"""Tests for the audio-Autodata Operations-to-repo promoter."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from benchmark.audio_autodata_promoter import build_promotion_plan  # type: ignore[import-not-found]  # noqa: E402
from benchmark.evaluate_audio_repair_autodata_v0 import DEFAULT_CASES_PATH  # type: ignore[import-not-found]  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
PROMOTER = REPO / "benchmark/audio_autodata_promoter.py"


def _case_fixture(case_id: str) -> dict:
    payload = json.loads(DEFAULT_CASES_PATH.read_text())
    for row in payload["cases"]:
        if row["case_id"] == case_id:
            return copy.deepcopy(row)
    raise AssertionError(f"missing fixture {case_id}")


def test_promoter_blocks_already_promoted_accepted_case_without_raw_audio_leak(tmp_path: Path) -> None:
    fixture = _case_fixture("audio-034-slurp-nbest-named-track-media-repair")
    candidates_path = tmp_path / "promotion_candidates.json"
    candidates_path.write_text(
        json.dumps(
            {
                "accepted": [{"decision": "duplicate_fixture", "repo_fixture_case": fixture}],
                "held": [
                    {
                        "decision": "held_scale_without_repo_payload",
                        "dataset": "DynamicSuperb/SuperbIC_SLURP",
                        "row_idx": 999,
                        "source_transcript": "scale-only held row",
                    }
                ],
                "rejected": [],
            }
        )
    )
    plan = build_promotion_plan(candidates_path)
    payload = plan.as_dict()

    assert payload["raw_audio_not_committed"] is True
    assert payload["counts"]["accepted_ready"] == 0
    accepted = payload["accepted"][0]
    assert accepted["candidate_id"] == "audio-034-slurp-nbest-named-track-media-repair"
    assert accepted["ready"] is False
    assert "duplicate case_id" in " ".join(accepted["errors"])
    # Scale-only Operations evidence is visible in the plan but not repo-ready
    # until a nightly script emits an explicit repo_held_candidate object.
    assert payload["held"][0]["status"] == "held_without_repo_payload"


def test_promoter_accepts_repo_held_candidate_and_reports_count_delta(tmp_path: Path) -> None:
    candidate = {
        "dataset": "DynamicSuperb/SuperbIC_SLURP",
        "row_idx": 70,
        "source_transcript": "i am going to work today",
        "decision": "held_slurp_ambient_statement_wake_context",
        "repo_held_candidate": {
            "candidate_id": "held-test-slurp-ambient-statement-wake-context",
            "source_type": "public_corpus_audio_derived",
            "provenance": {
                "source_url": "https://huggingface.co/datasets/DynamicSuperb/SuperbIC_SLURP",
                "license": "SLURP/DynamicSuperb public metadata and audio; redistribution not reviewed here",
                "redistribution_status": "metadata/ASR hypothesis only; raw audio not committed",
                "run_artifact": "parker-autodata-nightly/runs/2099-01-01/audio_loop/promotion_candidates.json#slurp-row-70",
            },
            "source_transcript": "i am going to work today",
            "source_intent_class": "general_quirky_ambient_statement",
            "oracle_target": "no_action_or_conversation_after_wake_context",
            "asr_hypotheses": ["I am going to work today."],
            "observed_weak_current": {
                "result": "generic_repair_choices",
                "notes": "No action cue is present; generic reminder/message choices are a nuisance without addressed-to-me context.",
            },
            "hold_reason": "future_addressed_to_me_wake_context_eval",
            "promotion_blocker": "needs an explicit wake/addressed-to-me context lane; do not add a broad text guard that could suppress effortful command fragments",
            "safety_label": "held_ambient_statement_nuisance_choices_no_action",
            "confusion_pairs": [
                "ambient statement/generic repair choices",
                "SLURP general query/no wake context",
            ],
            "private_data": "none",
        },
    }
    candidates_path = tmp_path / "promotion_candidates.json"
    candidates_path.write_text(json.dumps({"accepted": [], "held": [candidate], "rejected": []}))

    plan = build_promotion_plan(candidates_path)
    payload = plan.as_dict()

    assert payload["counts"]["held_ready"] == 1
    assert payload["held"][0]["candidate_id"] == "held-test-slurp-ambient-statement-wake-context"
    assert payload["patch_suggestions"]["count_delta"]["held_candidates"] == 1
    assert payload["after_metrics"]["total_cases"] == payload["before_metrics"]["total_cases"]


def test_promoter_cli_runs_from_repo_root_and_outputs_json(tmp_path: Path) -> None:
    candidates_path = tmp_path / "promotion_candidates.json"
    candidates_path.write_text(json.dumps({"accepted": [], "held": [], "rejected": []}))
    completed = subprocess.run(
        [sys.executable, str(PROMOTER), str(candidates_path), "--json"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["source_candidates_path"] == str(candidates_path)
    assert payload["counts"]["accepted_ready"] == 0


def test_promoter_rejects_local_raw_audio_paths_in_repo_fixture(tmp_path: Path) -> None:
    fixture = _case_fixture("audio-034-slurp-nbest-named-track-media-repair")
    fixture["case_id"] = "audio-test-local-raw-audio-leak"
    fixture["provenance"]["run_artifact"] = "/Users/example/Operations/raw_clip.wav"
    candidates_path = tmp_path / "promotion_candidates.json"
    candidates_path.write_text(json.dumps({"accepted": [{"repo_fixture_case": fixture}], "held": [], "rejected": []}))

    plan = build_promotion_plan(candidates_path)
    payload = plan.as_dict()

    assert payload["raw_audio_not_committed"] is False
    assert payload["counts"]["accepted_ready"] == 0
    assert "local/private path" in " ".join(payload["accepted"][0]["errors"])
    assert "raw audio file" in " ".join(payload["accepted"][0]["errors"])
