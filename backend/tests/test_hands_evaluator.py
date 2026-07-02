"""The hands-lane evaluator runs keyless/offline and its gate means something.

The evaluator itself is exercised end to end (all scenarios over the fake
gateway); these tests pin that the lane passes on the current product, that
the unsafe accounting is a real hard gate, and that the Makefile/CI expose
it like every other release eval.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from benchmark.evaluate_hands_v0 import SCENARIOS, evaluate, format_markdown  # noqa: E402


def test_hands_eval_passes_on_current_product():
    payload = evaluate()

    summary = payload["summary"]
    assert summary["gate"] == "PASS"
    assert summary["unsafe_count"] == 0
    assert summary["scenarios_failed"] == []
    assert summary["scenarios_total"] == len(SCENARIOS) == 8


def test_hands_eval_covers_the_required_edges():
    payload = evaluate()

    ids = {row["id"] for row in payload["scenarios"]}
    # The three edge cases the release gate requires, plus the two
    # acceptance scenarios and the trust-model boundaries.
    assert "hands-04-off-allowlist-gated" in ids
    assert "hands-05-unknown-action-type" in ids
    assert "hands-06-gateway-error-mid-execution" in ids
    assert "hands-01-media-playlist" in ids
    assert "hands-02-open-links" in ids
    assert "hands-08-purchase-skill-ignored" in ids


def test_hands_eval_markdown_report_names_the_gate():
    payload = evaluate()

    markdown = format_markdown(payload)

    assert "Gate: **PASS**" in markdown
    assert "hard 0 gate" in markdown
    assert "Synthetic/local evidence only" in markdown


def test_makefile_and_ci_expose_eval_hands():
    makefile = (REPO_ROOT / "Makefile").read_text()
    assert "eval-hands: backend-venv" in makefile
    assert "benchmark/evaluate_hands_v0.py --write-report" in makefile

    workflow = (REPO_ROOT / ".github" / "workflows" / "parker-ci.yml").read_text()
    assert "make eval-hands" in workflow
