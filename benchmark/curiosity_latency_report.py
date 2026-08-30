"""Aggregate the Patient Curiosity Loop latency receipts against the budgets.

Reads ``PARKER_HOME/receipts/converse_latency.jsonl`` (written locally by
every converse turn — server stage timings — and by the page — client
marks) and prints an aggregate-only view against the execution plan's
budgets. ``--write-report`` writes the same aggregates to
``benchmark/reports/`` — never any utterance content, which the receipts
do not contain in the first place.

Budgets (validate, not claim):
- listening indicator under 100 ms;
- touch Stop -> silence under 150 ms;
- warmed ASR after Done: median under 1000 ms, p95 under 1500 ms;
- live answer first audio: median under 5000 ms after Done.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parents[1] / "backend"))

BUDGETS = [
    # (metric label, source, field, aggregate, limit ms)
    ("listening indicator", "client", "start_to_listening_ms", "median", 100),
    ("stop -> silence", "client", "stop_to_silence_ms", "median", 150),
    ("ASR after Done (median)", "server", "asr", "median", 1000),
    ("ASR after Done (p95)", "server", "asr", "p95", 1500),
    ("server turn total (median)", "server", "total_after_done", "median", 5000),
    ("first audio after Done (median)", "client", "done_to_first_audio_ms", "median", 5000),
]


def load_receipts(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def values_for(rows: list[dict[str, Any]], source: str, field: str) -> list[float]:
    values = []
    for row in rows:
        if row.get("recorded_by") != source:
            continue
        value = row.get(field) if source == "client" else (row.get("timings_ms") or {}).get(field)
        if isinstance(value, (int, float)):
            values.append(float(value))
    return values


def aggregate(values: list[float], how: str) -> float | None:
    if not values:
        return None
    if how == "median":
        return statistics.median(values)
    if how == "p95":
        ordered = sorted(values)
        return ordered[min(len(ordered) - 1, int(round(0.95 * len(ordered))) )]
    raise ValueError(how)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--receipts", default=None, help="override the receipts file path")
    args = parser.parse_args()

    from app import paths

    receipts_path = (
        Path(args.receipts)
        if args.receipts
        else paths.receipts_dir() / "converse_latency.jsonl"
    )
    rows = load_receipts(receipts_path)
    server_turns = [row for row in rows if row.get("recorded_by") == "server"]
    print(f"Curiosity loop latency — {len(rows)} receipts "
          f"({len(server_turns)} server turns) from {receipts_path}")
    if not rows:
        print("No receipts yet. Run turns at /parker/converse (or the smoke script) first.")
        return 0

    lines = [
        f"# Patient Curiosity Loop latency — {date.today()}",
        "",
        f"{len(rows)} receipts, {len(server_turns)} server turns. Aggregates only;",
        "receipts never contain utterance text. Budgets are experiment targets",
        "to validate, not public claims.",
        "",
        "| Metric | n | median ms | p95 ms | budget ms | status |",
        "|---|---|---|---|---|---|",
    ]
    failures = []
    seen: set[tuple[str, str]] = set()
    for label, source, field, how, limit in BUDGETS:
        values = values_for(rows, source, field)
        value = aggregate(values, how)
        status = "no data"
        if value is not None:
            status = "within" if value <= limit else "OVER"
            if status == "OVER":
                failures.append((label, value, limit))
        print(f"  {label:32s} n={len(values):3d} {how}="
              f"{'-' if value is None else str(int(value))} ms (budget {limit})  {status}")
        if (source, field) not in seen:
            seen.add((source, field))
            med = aggregate(values, "median")
            p95 = aggregate(values, "p95")
            lines.append(
                f"| {label} | {len(values)} | "
                f"{'-' if med is None else int(med)} | {'-' if p95 is None else int(p95)} | "
                f"{limit} | {status} |"
            )

    if failures:
        dominant = max(failures, key=lambda f: f[1] / f[2])
        print(f"\nDominant failing stage: {dominant[0]} "
              f"({int(dominant[1])} ms vs {dominant[2]} ms budget)")
        lines += ["", f"Dominant failing stage: {dominant[0]} "
                      f"({int(dominant[1])} ms vs {dominant[2]} ms budget)"]
    else:
        print("\nAll measured stages within budget (unmeasured stages report 'no data').")
        lines += ["", "All measured stages within budget (unmeasured stages report 'no data')."]

    if args.write_report:
        reports_dir = Path(__file__).parent / "reports"
        reports_dir.mkdir(exist_ok=True)
        for name in (
            f"curiosity_latency_{date.today()}.md",
            "curiosity_latency_latest.md",
        ):
            (reports_dir / name).write_text("\n".join(lines) + "\n")
        print(f"Report written to {reports_dir / f'curiosity_latency_{date.today()}.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
