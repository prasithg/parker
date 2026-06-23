"""Repair-choice quality evaluator (v0).

Runs a set of effortful-speech utterances through ``suggest_repair_candidates``
with the real Claude haiku model and prints the generated choices for human
review. This is an *observability* tool, not a pass/fail test — candidate
quality is subjective and graded by the pilot family, not by an automated
metric.

Requires ``ANTHROPIC_API_KEY`` to be set; skips gracefully otherwise.

Usage:
    python3 benchmark/evaluate_repair_v0.py
    python3 benchmark/evaluate_repair_v0.py --write-report
    make eval-repair
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

# Fixtures: (utterance, expected_action_hint)
# expected_action_hint is for human reference only — the eval prints whether
# the model's action_type matched; it does not fail on mismatches.
REPAIR_FIXTURES: list[tuple[str, str]] = [
    # Disfluent, person mentioned
    ("Call... the... you know... the one with the garden...", "family_message"),
    # Disfluent, no person
    ("I need to... the thing... you know... the doctor thing", "reminder"),
    # Trailing off mid-sentence
    ("The physio said I should... every morning...", "reminder"),
    # Ambiguous — could be message or reminder
    ("My daughter — tell her — the visit...", "family_message"),
    # Multiple ellipses, no clear subject
    ("That... the... uh... Tuesday appointment...", "reminder"),
    # Implicit ask via "can you"
    ("Can you... you know... the thing with the calendar", "reminder"),
    # Fragmented family message
    ("Rohan should know about... the thing with my leg", "family_message"),
    # Completely vague
    ("The one with the... no the other one", "family_message"),
]


def run_eval(client, verbose: bool = True) -> list[dict]:
    sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
    from app.conversation.repair import suggest_repair_candidates

    results = []
    for utterance, hint in REPAIR_FIXTURES:
        candidates = suggest_repair_candidates(utterance, client=client)
        labels = [lbl for lbl, _ in candidates]
        action_types = [at for _, at in candidates]
        hint_matched = hint in action_types
        result = {
            "utterance": utterance,
            "expected_hint": hint,
            "candidates": [{"label": lbl, "action_type": at} for lbl, at in candidates],
            "hint_matched": hint_matched,
        }
        results.append(result)
        if verbose:
            match_flag = "✓" if hint_matched else "~"
            print(f"  {match_flag}  {utterance!r}")
            for i, (lbl, at) in enumerate(candidates, 1):
                print(f"     {i}) [{at}] {lbl}")
            print()

    matched = sum(1 for r in results if r["hint_matched"])
    if verbose:
        print(f"action_type hint matched: {matched}/{len(results)}")
        print("(hint match is guidance only — labels are the primary quality signal)")
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-report", action="store_true")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ANTHROPIC_API_KEY not set — skipping repair eval (no model available).")
        print("Set the key to run: ANTHROPIC_API_KEY=sk-ant-... make eval-repair")
        return

    try:
        import anthropic
    except ImportError:
        print("anthropic package not installed — run: make backend-venv")
        return

    client = anthropic.Anthropic(api_key=api_key)
    print(f"Repair-choice quality eval — {len(REPAIR_FIXTURES)} fixtures — {date.today()}\n")
    results = run_eval(client)

    if args.write_report:
        reports_dir = Path(__file__).parent / "reports"
        reports_dir.mkdir(exist_ok=True)
        report_path = reports_dir / f"repair_eval_{date.today()}.json"
        report_path.write_text(json.dumps({"date": str(date.today()), "results": results}, indent=2))
        print(f"\nReport written to {report_path}")


if __name__ == "__main__":
    main()
