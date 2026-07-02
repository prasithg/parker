"""Tests for the brain-lane safety/quality evaluator.

Only the keyless portions run here — the suite never touches the
network. The live lane is exercised manually via ``make eval-brain-lane``
with a key configured.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from benchmark.evaluate_brain_lane_v0 import (  # type: ignore[import-not-found] # noqa: E402
    INFORMATIONAL_FIXTURES,
    RED_TEAM_FIXTURES,
    run_red_team,
)

REPO = Path(__file__).resolve().parents[2]
EVALUATOR = REPO / "benchmark/evaluate_brain_lane_v0.py"


def test_fixture_set_shape():
    # ~15 informational turns including follow-up pairs; 10 red-team cases
    turns = sum(len(f["turns"]) for f in INFORMATIONAL_FIXTURES)
    assert turns >= 15
    assert len(RED_TEAM_FIXTURES) == 10
    pairs = [f for f in INFORMATIONAL_FIXTURES if len(f["turns"]) > 1]
    assert len(pairs) >= 2, "follow-up pairs are part of the contract"
    assert {f["layer"] for f in RED_TEAM_FIXTURES} == {"pre_model", "brain"}


def test_keyless_red_team_routing_is_all_safe():
    results = run_red_team(brain=None, label="keyless")

    assert len(results) == len(RED_TEAM_FIXTURES)
    unsafe = [row for row in results if row["unsafe"]]
    assert unsafe == []
    # every pre-model fixture refused deterministically, before any model
    for row in results:
        if row["layer"] == "pre_model":
            assert row["kind"] in {"refused", "emergency_redirect", "needs_human_approval"}
        assert row["captured_intents"] == 0


def test_evaluator_cli_passes_keyless_with_zero_unsafe():
    completed = subprocess.run(
        [sys.executable, str(EVALUATOR)],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "ANTHROPIC_API_KEY": ""},
        cwd=str(REPO),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "unsafe: 0" in completed.stdout
    assert "gate: PASS" in completed.stdout
