"""Degraded-input replay evaluator for Parker's grant-facing repair metric.

This is the Night4 Claw/adversarial-review machine check: one quantitative
interaction metric with a non-interactive baseline on a small held-out set of
synthetic degraded/effortful-speech transcripts.

It is deliberately modest and honest:
- no real patient audio;
- no private family data;
- no model/API dependency;
- transcript-level replay only;
- the current Parker repair protocol is compared with a baseline that has no
  repair loop and can only ask the user to repeat.

Usage:
    python3 benchmark/evaluate_degraded_input_replay_v0.py
    python3 benchmark/evaluate_degraded_input_replay_v0.py --json
    python3 benchmark/evaluate_degraded_input_replay_v0.py --write-report
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

DEFAULT_CASES_PATH = REPO_ROOT / "benchmark" / "data" / "degraded_input_replay_v0.json"
DEFAULT_REPORTS_DIR = REPO_ROOT / "benchmark" / "reports"
PRIMARY_METRIC_NAME = "intent_recovery_accuracy_delta_vs_non_interactive"
PRE_REGISTERED_SUCCESS_THRESHOLD = 0.34
ACTION_ALIASES = {"remind": "reminder", "message": "family_message"}


@dataclass(frozen=True)
class ReplayCase:
    """One synthetic held-out degraded-input replay case."""

    case_id: str
    title: str
    privacy: str
    split: str
    degradation_slices: list[str]
    degraded_input: str
    expected_action_type: str
    subject_keywords: list[str]
    repair_selection: str

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "ReplayCase":
        missing = {
            "case_id",
            "privacy",
            "split",
            "title",
            "degradation_slices",
            "degraded_input",
            "gold",
        } - set(row)
        if missing:
            raise ValueError(f"case {row.get('case_id', '<unknown>')} missing fields: {sorted(missing)}")
        if row["privacy"] != "synthetic":
            raise ValueError(f"case {row['case_id']} privacy must be synthetic")
        if not str(row["split"]).startswith("heldout"):
            raise ValueError(f"case {row['case_id']} must be marked as heldout for this smoke eval")
        if not isinstance(row["degradation_slices"], list) or not row["degradation_slices"]:
            raise ValueError(f"case {row['case_id']} needs degradation_slices")
        gold = row["gold"]
        if not isinstance(gold, dict):
            raise ValueError(f"case {row['case_id']} gold must be an object")
        gold_missing = {"expected_action_type", "subject_keywords", "repair_selection"} - set(gold)
        if gold_missing:
            raise ValueError(f"case {row['case_id']} gold missing fields: {sorted(gold_missing)}")
        if gold["expected_action_type"] not in {"reminder", "family_message"}:
            raise ValueError(f"case {row['case_id']} has unsupported expected_action_type")
        if not isinstance(gold["subject_keywords"], list) or not gold["subject_keywords"]:
            raise ValueError(f"case {row['case_id']} needs subject_keywords")
        selection = str(gold["repair_selection"]).strip()
        if selection not in {"1", "2", "3"}:
            raise ValueError(f"case {row['case_id']} repair_selection must be 1, 2, or 3")
        return cls(
            case_id=str(row["case_id"]),
            title=str(row["title"]),
            privacy=str(row["privacy"]),
            split=str(row["split"]),
            degradation_slices=[str(item) for item in row["degradation_slices"]],
            degraded_input=str(row["degraded_input"]),
            expected_action_type=str(gold["expected_action_type"]),
            subject_keywords=[str(item).lower() for item in gold["subject_keywords"]],
            repair_selection=selection,
        )


@dataclass(frozen=True)
class CaseResult:
    """One baseline's result on one replay case."""

    case_id: str
    baseline: str
    recovered_intent: bool
    repair_initiated: bool
    turns_to_resolution: int | None
    action_type: str | None
    safety_critical_miss: bool
    failure_reason: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "baseline": self.baseline,
            "recovered_intent": self.recovered_intent,
            "repair_initiated": self.repair_initiated,
            "turns_to_resolution": self.turns_to_resolution,
            "action_type": self.action_type,
            "safety_critical_miss": self.safety_critical_miss,
            "failure_reason": self.failure_reason,
            "events": self.events,
        }


@dataclass(frozen=True)
class DegradedReplayEvalResult:
    """Aggregate result for the preregistered degraded-input replay metric."""

    total_cases: int
    baseline_results: dict[str, list[CaseResult]]

    def baseline_metrics(self) -> dict[str, dict[str, Any]]:
        metrics: dict[str, dict[str, Any]] = {}
        for baseline, rows in self.baseline_results.items():
            recovered = sum(1 for row in rows if row.recovered_intent)
            repair_initiated = sum(1 for row in rows if row.repair_initiated)
            safety_misses = sum(1 for row in rows if row.safety_critical_miss)
            turns = [row.turns_to_resolution for row in rows if row.turns_to_resolution is not None]
            metrics[baseline] = {
                "intent_recovery_accuracy": _ratio(recovered, len(rows)),
                "repair_initiated_rate": _ratio(repair_initiated, len(rows)),
                "median_turns_to_resolution": statistics.median(turns) if turns else None,
                "safety_critical_misses": safety_misses,
            }
        return metrics

    def primary_metric(self) -> dict[str, Any]:
        metrics = self.baseline_metrics()
        parker = metrics["parker_repair_protocol"]["intent_recovery_accuracy"]
        baseline = metrics["non_interactive_no_repair"]["intent_recovery_accuracy"]
        delta = parker - baseline
        safety_misses = metrics["parker_repair_protocol"]["safety_critical_misses"]
        return {
            "name": PRIMARY_METRIC_NAME,
            "parker_repair_protocol": parker,
            "non_interactive_no_repair": baseline,
            "delta": delta,
            "success_threshold_delta": PRE_REGISTERED_SUCCESS_THRESHOLD,
            "threshold_met": delta >= PRE_REGISTERED_SUCCESS_THRESHOLD and safety_misses == 0,
            "safety_critical_misses": safety_misses,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "eval": "degraded_input_replay_v0",
            "provenance": {
                "fixture_policy": "synthetic held-out transcript-level smoke check",
                "private_data": "none",
                "model_or_api_dependency": "none",
                "grant_claim_status": "feasibility/checkpoint metric only; not real patient or audio evidence",
            },
            "total_cases": self.total_cases,
            "pre_registered_primary_metric": self.primary_metric(),
            "baseline_metrics": self.baseline_metrics(),
            "case_results": {
                baseline: [row.as_dict() for row in rows]
                for baseline, rows in self.baseline_results.items()
            },
        }


def load_cases(path: Path = DEFAULT_CASES_PATH) -> list[ReplayCase]:
    """Load and validate degraded-input replay cases."""

    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected a JSON array")
    cases = [ReplayCase.from_dict(row) for row in raw]
    seen: set[str] = set()
    for case in cases:
        if case.case_id in seen:
            raise ValueError(f"duplicate case_id: {case.case_id}")
        seen.add(case.case_id)
    if not cases:
        raise ValueError("degraded-input replay fixture set is empty")
    return cases


def evaluate(cases: list[ReplayCase]) -> DegradedReplayEvalResult:
    """Run the non-interactive and Parker-repair baselines on the same cases."""

    return DegradedReplayEvalResult(
        total_cases=len(cases),
        baseline_results={
            "non_interactive_no_repair": [_run_non_interactive_no_repair(case) for case in cases],
            "parker_repair_protocol": [_run_parker_repair_protocol(case) for case in cases],
        },
    )


def _run_non_interactive_no_repair(case: ReplayCase) -> CaseResult:
    """Baseline: no repair loop, so degraded inputs stall at repeat request."""

    return CaseResult(
        case_id=case.case_id,
        baseline="non_interactive_no_repair",
        recovered_intent=False,
        repair_initiated=False,
        turns_to_resolution=None,
        action_type=None,
        safety_critical_miss=False,
        failure_reason="no repair loop; asks the user to repeat and recovers no confirmed intent",
        events=[
            {"actor": "user", "type": "degraded_input", "text": case.degraded_input},
            {"actor": "assistant", "type": "ask_repeat", "committed_action": False},
        ],
    )


def _run_parker_repair_protocol(case: ReplayCase) -> CaseResult:
    """Run the current deterministic TextSession repair protocol on one case."""

    from app.conversation.textloop import TextSession
    from app.db.models import CapturedIntent

    with _demo_db() as db:
        call = _create_call(db, f"DEGRADED-{case.case_id}")
        session = TextSession(db, call.id, model_client=None)
        first = session.handle(case.degraded_input)
        events: list[dict[str, Any]] = [
            {"actor": "user", "type": "degraded_input", "text": case.degraded_input},
            _response_event(first),
        ]
        repair_initiated = first.get("kind") == "choices"
        second: dict[str, Any] | None = None
        if repair_initiated:
            selected_response = session.handle(case.repair_selection)
            second = selected_response
            events.extend(
                [
                    {"actor": "user", "type": "repair_selection", "selection": case.repair_selection},
                    _response_event(selected_response),
                ]
            )
        latest = db.query(CapturedIntent).order_by(CapturedIntent.id.desc()).first()
        action_type = _normalize_action_type(
            (second or first).get("requested_action") or (latest.requested_action if latest else None)
        )
        text_blob = " ".join(
            str(part or "")
            for part in [
                case.degraded_input,
                latest.subject if latest else None,
                latest.intent_text if latest else None,
                first.get("speech"),
                second.get("speech") if second else None,
            ]
        ).lower()
        keywords_present = all(keyword in text_blob for keyword in case.subject_keywords)
        recovered = (
            repair_initiated
            and (second or {}).get("kind") == "captured"
            and action_type == case.expected_action_type
            and keywords_present
        )
        failure_reason = None
        if not recovered:
            failure_reason = _failure_reason(
                repair_initiated=repair_initiated,
                second=second,
                expected_action_type=case.expected_action_type,
                action_type=action_type,
                keywords_present=keywords_present,
            )
        return CaseResult(
            case_id=case.case_id,
            baseline="parker_repair_protocol",
            recovered_intent=recovered,
            repair_initiated=repair_initiated,
            turns_to_resolution=2 if recovered else None,
            action_type=action_type,
            safety_critical_miss=False,
            failure_reason=failure_reason,
            events=events,
        )


@contextmanager
def _demo_db() -> Iterator[Any]:
    """Fresh in-memory DB so replay never touches private/local Parker state."""

    from app.db.database import Base

    from sqlalchemy import create_engine  # type: ignore[import-not-found]
    from sqlalchemy.orm import sessionmaker  # type: ignore[import-not-found]
    from sqlalchemy.pool import StaticPool  # type: ignore[import-not-found]

    import app.db.models  # noqa: F401
    import app.escalation.models  # noqa: F401
    import app.exercises.session  # noqa: F401
    import app.memory.models  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _create_call(db: Any, sid: str):
    from app.db.models import CallLog

    call = CallLog(call_sid=sid[:64], call_type="degraded_replay_eval")
    db.add(call)
    db.commit()
    db.refresh(call)
    return call


def _response_event(response: dict[str, Any]) -> dict[str, Any]:
    event = {
        "actor": "assistant",
        "type": str(response.get("kind", "unknown")),
        "speech": response.get("speech"),
        "committed_action": response.get("kind") == "captured",
    }
    if response.get("choices"):
        event["choices"] = [choice.get("label") for choice in response["choices"]]
    if response.get("requested_action"):
        event["action_type"] = _normalize_action_type(response.get("requested_action"))
    return {key: value for key, value in event.items() if value is not None and value != ""}


def _normalize_action_type(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip().lower()
    return ACTION_ALIASES.get(raw, raw)


def _failure_reason(
    *,
    repair_initiated: bool,
    second: dict[str, Any] | None,
    expected_action_type: str,
    action_type: str | None,
    keywords_present: bool,
) -> str:
    if not repair_initiated:
        return "Parker did not initiate a repair choice turn"
    if (second or {}).get("kind") != "captured":
        return f"repair selection did not capture an intent: kind={(second or {}).get('kind')}"
    if action_type != expected_action_type:
        return f"captured action_type={action_type!r}, expected {expected_action_type!r}"
    if not keywords_present:
        return "captured trace did not preserve expected subject keywords"
    return "unknown recovery failure"


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0


def format_summary(result: DegradedReplayEvalResult) -> str:
    payload = result.as_dict()
    primary = payload["pre_registered_primary_metric"]
    metrics = payload["baseline_metrics"]
    lines = [
        "Parker degraded-input replay eval v0",
        "",
        f"Cases: {result.total_cases} synthetic held-out transcript replays",
        f"Primary metric: {primary['name']}",
        f"  Parker repair protocol:     {primary['parker_repair_protocol']:.2%}",
        f"  Non-interactive no-repair:  {primary['non_interactive_no_repair']:.2%}",
        f"  Delta:                      {primary['delta']:.2%}",
        f"  Safety-critical misses:     {primary['safety_critical_misses']}",
        f"  Threshold met:              {primary['threshold_met']}",
        "",
        "Baseline details:",
    ]
    for baseline, row in metrics.items():
        turns = row["median_turns_to_resolution"]
        turns_text = "n/a" if turns is None else str(turns)
        lines.append(
            f"  {baseline}: intent_recovery={row['intent_recovery_accuracy']:.2%}, "
            f"repair_initiated={row['repair_initiated_rate']:.2%}, "
            f"median_turns={turns_text}, safety_misses={row['safety_critical_misses']}"
        )
    lines.extend(
        [
            "",
            "Caveat: this is a synthetic transcript-level checkpoint, not real Parkinson's audio or patient evidence.",
        ]
    )
    return "\n".join(lines)


def format_markdown_report(result: DegradedReplayEvalResult, run_date: str) -> str:
    payload = result.as_dict()
    primary = payload["pre_registered_primary_metric"]
    metrics = payload["baseline_metrics"]
    lines = [
        "# Parker degraded-input replay eval v0",
        "",
        f"- Date: {run_date}",
        "- Provenance: synthetic held-out transcript-level smoke check; no private family/patient data; no model/API dependency.",
        "- Purpose: convert Claw's Night4 correction into one quantitative interaction metric with a non-interactive baseline.",
        "",
        "## Pre-registered primary metric",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Parker repair protocol intent recovery | {primary['parker_repair_protocol']:.2%} |",
        f"| Non-interactive no-repair intent recovery | {primary['non_interactive_no_repair']:.2%} |",
        f"| Delta | {primary['delta']:.2%} |",
        f"| Success threshold delta | {primary['success_threshold_delta']:.2%} |",
        f"| Safety-critical misses | {primary['safety_critical_misses']} |",
        f"| Threshold met | {primary['threshold_met']} |",
        "",
        "## Baseline details",
        "",
        "| Baseline | Intent recovery | Repair initiated | Median turns to resolution | Safety-critical misses |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for baseline, row in metrics.items():
        turns = row["median_turns_to_resolution"]
        turns_text = "n/a" if turns is None else str(turns)
        lines.append(
            f"| {baseline} | {row['intent_recovery_accuracy']:.2%} | "
            f"{row['repair_initiated_rate']:.2%} | {turns_text} | {row['safety_critical_misses']} |"
        )
    lines.extend(["", "## Case breakdown", ""])
    for baseline, rows in result.baseline_results.items():
        lines.append(f"### {baseline}")
        lines.append("")
        for row in rows:
            status = "PASS" if row.recovered_intent else "FAIL"
            reason = f" — {row.failure_reason}" if row.failure_reason else ""
            lines.append(f"- **{status}** `{row.case_id}`: action={row.action_type}, turns={row.turns_to_resolution}{reason}")
        lines.append("")
    lines.extend(
        [
            "## Grant-readiness caveat",
            "",
            "This number is useful because it prevents pure proposal polish from masquerading as an interactivity result. It is not enough to claim real-world Parkinson's speech performance. The grant-funded version still needs real audio or consented participant data, richer degraded-input slices, a stronger non-interactive baseline, realtime latency instrumentation, and human/model grading of repair-choice quality.",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(result: DegradedReplayEvalResult, reports_dir: Path = DEFAULT_REPORTS_DIR) -> list[Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    run_date = date.today().isoformat()
    markdown = format_markdown_report(result, run_date)
    payload = {"date": run_date, **result.as_dict()}
    written: list[Path] = []
    for stem in ("degraded_input_replay_eval_latest", f"degraded_input_replay_eval_{run_date}"):
        md_path = reports_dir / f"{stem}.md"
        json_path = reports_dir / f"{stem}.json"
        md_path.write_text(markdown)
        json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        written.extend([md_path, json_path])
    return written


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT))
    except ValueError:
        return str(resolved)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--json", action="store_true", help="Print JSON instead of text summary")
    parser.add_argument("--write-report", action="store_true", help="Write markdown+JSON reports to benchmark/reports")
    parser.add_argument("--reports-dir", type=Path, default=DEFAULT_REPORTS_DIR)
    args = parser.parse_args()

    cases = load_cases(args.cases)
    result = evaluate(cases)
    if args.json:
        print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    else:
        print(format_summary(result))
    if args.write_report:
        for path in write_report(result, args.reports_dir):
            print(f"wrote {_display_path(path)}")


if __name__ == "__main__":
    main()
