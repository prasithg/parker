"""Weekly interaction-outcome rollup: EXP-001's baseline numbers, locally.

``make rollup`` aggregates the interaction_outcomes table (one row per
directed interaction; app/conversation/outcomes.py) into a local
markdown + JSON pair under ``PARKER_HOME/rollups/``. Aggregates only:
the artifact contains counts, rates, dates, and hashes — never
transcript text, subjects, names, or normalized intent signatures — so
it is safe to show any family member. Raw rows stay in SQLite under the
existing local policies.

Timezone: rows are stored naive-UTC (the schema-wide convention), but a
person's week runs on their clock — buckets here use the HOME-LOCAL
timezone (the machine's local zone by default, injectable for tests).
This deliberately differs from the eval-report freshness convention,
which is UTC; that convention is for citable evidence files, not for
describing someone's week.

Metric definitions (the source of truth; docs quote these):

- Protocolized Functional Phrase practice rows remain in local SQLite with
  ``source=functional_phrase_practice`` but are excluded from this natural-use
  EXP-001 rollup so rehearsed attempts cannot inflate daily-use rates.

- A *request* is an interaction whose outcome is one of
  understood_first_try, repaired_success, repair_abandoned, wrong_action.
- unassisted_success_rate = (understood_first_try + repaired_success)
  / requests. These two are the outcomes where the request was
  UNDERSTOOD and captured/answered with no human helper stepping in —
  the North Star numerator. This measures understanding at capture, not
  fulfillment: an execution failure or a declined offer after a correct
  capture stays a success here and is visible in the staged-action audit
  rows instead. wrong_action and repair_abandoned are the failure modes.
- first_attempt_rate = understood_first_try / requests ("understood on
  the first try"). Note repaired_success may take MORE than one repair
  turn (repair_turns is stored per row); the strict ≥90%
  first-try-or-one-repair capability target is measured by the
  real-audio harness, and its strict analog here would be
  first_try + (repaired_success with repair_turns == 1).
- repair_success_rate = repaired_success / (repaired_success +
  repair_abandoned): of the interactions where Parker asked a repair
  question, how many resolved.
- nuisance_choice_rate = repair_abandoned / (requests + no_response):
  how often Parker asked a repair question that led nowhere, relative to
  everything directed at it. The live-defect motivating this metric was
  ambient noise drawing choice prompts.
- refused_safety is EXCLUDED from requests and reported separately:
  a refusal is Parker enforcing a boundary, not an understanding success
  or failure, and refusal *correctness* is measured by the safety eval
  lanes. Counting refusals as successes would inflate the North Star;
  counting them as failures would penalize safety.
- no_response (deliberate non-action acknowledgments) and ambient_noop
  (room/TV speech) are reported as volume, outside the request metrics.

Repeated-error detection v0: failed requests (repair_abandoned,
wrong_action) that share a normalized intent signature across ≥2 distinct
home-local days. Signatures are consent-gated at capture; the artifact
shows only an 8-char hash per group — family reads the actual text in the
local review surfaces, never here. This seeds Phase B learning velocity;
its normalization limits are documented on ``normalize_intent``.

Nothing here is medical. Declining numbers are an engineering signal
about Parker, never a health observation about a person.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session
from tzlocal import get_localzone

from app.conversation.outcomes import (
    INTERACTION_OUTCOMES,
    OUTCOME_AMBIENT_NOOP,
    OUTCOME_NO_RESPONSE,
    OUTCOME_REFUSED_SAFETY,
    OUTCOME_REPAIR_ABANDONED,
    OUTCOME_REPAIRED_SUCCESS,
    OUTCOME_UNDERSTOOD_FIRST_TRY,
    OUTCOME_WRONG_ACTION,
    InteractionOutcome,
    signature_hash,
)

FAILED_REQUEST_OUTCOMES = (OUTCOME_REPAIR_ABANDONED, OUTCOME_WRONG_ACTION)
REQUEST_OUTCOMES = (
    OUTCOME_UNDERSTOOD_FIRST_TRY,
    OUTCOME_REPAIRED_SUCCESS,
    OUTCOME_REPAIR_ABANDONED,
    OUTCOME_WRONG_ACTION,
)
REPEATED_ERROR_MIN_DAYS = 2


def home_timezone() -> tzinfo:
    """The home machine's named local zone, including future DST rules."""

    return get_localzone()


def week_start_for(day: date) -> date:
    """The Monday of the week containing ``day`` (ISO weeks; local dates)."""

    return day - timedelta(days=day.weekday())


def _local_date(stored_utc: datetime, tz: tzinfo) -> date:
    """Stored naive-UTC timestamp → home-local calendar date."""

    return stored_utc.replace(tzinfo=timezone.utc).astimezone(tz).date()


def _rate(numerator: int, denominator: int) -> Optional[float]:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def build_weekly_rollup(
    db: Session,
    *,
    week_of: Optional[date] = None,
    tz: Optional[tzinfo] = None,
) -> dict[str, Any]:
    """Aggregate one home-local week (Mon–Sun) of interaction outcomes."""

    zone = tz or home_timezone()
    today_local = datetime.now(timezone.utc).astimezone(zone).date()
    start = week_start_for(week_of or today_local)
    end = start + timedelta(days=6)

    # Pull a superset by UTC (a local week spans at most ±1 day of UTC skew)
    # and bucket precisely by converted local date.
    lower = datetime.combine(start - timedelta(days=1), datetime.min.time())
    upper = datetime.combine(end + timedelta(days=2), datetime.min.time())
    rows = (
        db.query(InteractionOutcome)
        .filter(InteractionOutcome.opened_at >= lower)
        .filter(InteractionOutcome.opened_at < upper)
        .filter(InteractionOutcome.source != "functional_phrase_practice")
        .order_by(InteractionOutcome.opened_at, InteractionOutcome.id)
        .all()
    )
    in_week = [row for row in rows if start <= _local_date(row.opened_at, zone) <= end]

    counts = {outcome: 0 for outcome in sorted(INTERACTION_OUTCOMES)}
    for row in in_week:
        counts[row.outcome] = counts.get(row.outcome, 0) + 1

    uft = counts[OUTCOME_UNDERSTOOD_FIRST_TRY]
    repaired = counts[OUTCOME_REPAIRED_SUCCESS]
    abandoned = counts[OUTCOME_REPAIR_ABANDONED]
    wrong = counts[OUTCOME_WRONG_ACTION]
    refused = counts[OUTCOME_REFUSED_SAFETY]
    no_response = counts[OUTCOME_NO_RESPONSE]
    ambient = counts[OUTCOME_AMBIENT_NOOP]
    requests = uft + repaired + abandoned + wrong

    repeated_errors = _repeated_errors(in_week, zone)

    return {
        "week_start": start.isoformat(),
        "week_end": end.isoformat(),
        "timezone": str(zone),
        "generated_at_local": datetime.now(timezone.utc).astimezone(zone).isoformat(timespec="seconds"),
        "outcome_counts": counts,
        "totals": {
            "requests": requests,
            "directed_interactions": requests + refused + no_response,
            "all_interactions": len(in_week),
        },
        "metrics": {
            "unassisted_success_rate": _rate(uft + repaired, requests),
            "first_attempt_rate": _rate(uft, requests),
            "repair_success_rate": _rate(repaired, repaired + abandoned),
            "nuisance_choice_rate": _rate(abandoned, requests + no_response),
            "refusal_share_of_directed": _rate(refused, requests + refused + no_response),
        },
        "repeated_errors": repeated_errors,
        "notes": [
            "Aggregates only — no transcript text; details live in Parker's local review surfaces.",
            "Outcomes are engineering signals about Parker, never health observations.",
            "Interactions the microphone/ASR never delivered are invisible to this layer.",
            "Protocolized Functional Phrase practice is stored locally but excluded from natural-use rates.",
        ],
    }


def _repeated_errors(rows: list[InteractionOutcome], tz: tzinfo) -> list[dict[str, Any]]:
    """Failed intents recurring across distinct home-local days this week."""

    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.outcome not in FAILED_REQUEST_OUTCOMES or not row.normalized_intent:
            continue
        key = row.normalized_intent
        group = groups.setdefault(
            key,
            {"days": set(), "count": 0, "outcomes": set(), "action_types": set()},
        )
        group["count"] += 1
        group["days"].add(_local_date(row.opened_at, tz).isoformat())
        group["outcomes"].add(row.outcome)
        if row.action_type:
            group["action_types"].add(row.action_type)
    repeated = []
    for key, group in groups.items():
        if len(group["days"]) < REPEATED_ERROR_MIN_DAYS:
            continue
        repeated.append(
            {
                # Hash only: the signature is transcript-derived and stays
                # out of the artifact. Look the failures up in review/repair
                # surfaces on the machine.
                "signature": signature_hash(key),
                "occurrences": group["count"],
                "distinct_days": sorted(group["days"]),
                "outcomes": sorted(group["outcomes"]),
                "action_types": sorted(group["action_types"]),
            }
        )
    repeated.sort(key=lambda item: (-item["occurrences"], item["signature"]))
    return repeated


def render_rollup_markdown(rollup: dict[str, Any]) -> str:
    counts = rollup["outcome_counts"]
    metrics = rollup["metrics"]
    totals = rollup["totals"]

    def pct(value: Optional[float]) -> str:
        return "n/a (no data)" if value is None else f"{value * 100:.1f}%"

    lines = [
        f"# Parker weekly rollup — week of {rollup['week_start']}",
        "",
        f"Local week {rollup['week_start']} → {rollup['week_end']} "
        f"({rollup['timezone']}). Generated {rollup['generated_at_local']}.",
        "",
        "Aggregates only — no transcript text. Raw rows stay on this machine.",
        "",
        "## Outcomes",
        "",
        "| Outcome | Count |",
        "| --- | --- |",
    ]
    for outcome in sorted(counts):
        lines.append(f"| {outcome} | {counts[outcome]} |")
    lines += [
        "",
        f"Requests: {totals['requests']} · Directed interactions: "
        f"{totals['directed_interactions']} · All rows: {totals['all_interactions']}",
        "",
        "## Metrics",
        "",
        f"- Unassisted success rate: {pct(metrics['unassisted_success_rate'])}",
        f"- First-attempt rate: {pct(metrics['first_attempt_rate'])}",
        f"- Repair success rate: {pct(metrics['repair_success_rate'])}",
        f"- Nuisance-choice rate: {pct(metrics['nuisance_choice_rate'])}",
        f"- Refusal share of directed: {pct(metrics['refusal_share_of_directed'])}",
        "",
        "Definitions: a request is understood_first_try + repaired_success +",
        "repair_abandoned + wrong_action. Unassisted success = the first two.",
        "Refusals are reported separately — they are boundary enforcement,",
        "not understanding failures. no_response = deliberate non-action",
        "acknowledgments (controls, counting, unclear context).",
        "",
        "## Repeated errors (v0)",
        "",
    ]
    repeated = rollup["repeated_errors"]
    if not repeated:
        lines.append("None detected this week (or repair-event capture is not consented).")
    else:
        lines += [
            "| Signature | Occurrences | Days | Outcomes |",
            "| --- | --- | --- | --- |",
        ]
        for item in repeated:
            lines.append(
                f"| {item['signature']} | {item['occurrences']} | "
                f"{', '.join(item['distinct_days'])} | {', '.join(item['outcomes'])} |"
            )
        lines += [
            "",
            "Signatures are hashes — the matching utterances stay in the local",
            "review/repair surfaces on this machine.",
        ]
    lines += ["", "---", ""]
    lines += [f"- {note}" for note in rollup["notes"]]
    lines.append("")
    return "\n".join(lines)


def default_rollup_dir() -> Path:
    from app import paths

    return paths.rollups_dir()


def write_rollup_files(
    rollup: dict[str, Any], markdown: str, *, directory: Optional[Path] = None
) -> tuple[Path, Path]:
    target_dir = directory or default_rollup_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    stem = f"parker-rollup-week-{rollup['week_start']}"
    md_path = target_dir / f"{stem}.md"
    json_path = target_dir / f"{stem}.json"
    md_path.write_text(markdown, encoding="utf-8")
    json_path.write_text(json.dumps(rollup, indent=2) + "\n", encoding="utf-8")
    return md_path, json_path


def main() -> None:  # pragma: no cover — CLI entry point (`make rollup`)
    from app.db.database import SessionLocal, create_tables

    parser = argparse.ArgumentParser(description="Weekly Parker interaction-outcome rollup")
    parser.add_argument(
        "--week-of",
        type=date.fromisoformat,
        default=None,
        help="Any local date inside the target week (default: today)",
    )
    args = parser.parse_args()

    create_tables()
    db = SessionLocal()
    try:
        rollup = build_weekly_rollup(db, week_of=args.week_of)
    finally:
        db.close()
    markdown = render_rollup_markdown(rollup)
    md_path, json_path = write_rollup_files(rollup, markdown)
    print(markdown)
    print(f"Written to {md_path} and {json_path} — local files; nothing was sent.")


if __name__ == "__main__":  # pragma: no cover
    main()
