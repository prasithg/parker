"""Wake/addressed-to-me audio-context evaluator for Parker.

This metadata-only eval is the first repo-side gate for the wake-context seam:
public/synthetic audio-derived ASR hypotheses are routed through ``TextSession`` with an explicit
``UtteranceContext``. Ambient room speech should become a silent no-op; wake-
confirmed conversation should route to the no-side-effect answer lane; wake-
confirmed action commands should still get confirmation-gated repair choices.
Raw audio remains in Operations and is never read by this evaluator.

Usage:
    python3 benchmark/evaluate_wake_context_audio_v0.py --json
    python3 benchmark/evaluate_wake_context_audio_v0.py --write-report
    make eval-wake-context
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from math import isfinite
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.conversation.textloop import TextSession, UtteranceContext  # noqa: E402
from app.db.database import Base  # noqa: E402
from app.db.models import CallLog, CapturedIntent  # noqa: E402
from app.parker.research_handoff import LocalResearchHandoff  # noqa: E402
import app.conversation.repair_events  # noqa: F401, E402 — register tables
import app.escalation.models  # noqa: F401, E402
import app.evening.session  # noqa: F401, E402
import app.exercises.session  # noqa: F401, E402
import app.memory.models  # noqa: F401, E402
import app.parker.screen  # noqa: F401, E402

DEFAULT_CASES_PATH = REPO_ROOT / "benchmark" / "data" / "wake_context_audio_v0.json"
DEFAULT_REPORTS_DIR = REPO_ROOT / "benchmark" / "reports"


@dataclass(frozen=True)
class WakeContextCase:
    case_id: str
    source_type: str
    source_transcript: str
    asr_hypotheses: list[str]
    context: dict[str, Any]
    expected: dict[str, Any]
    safety_label: str
    provenance: dict[str, Any]
    confusion_pairs: list[str]
    rubric: dict[str, float]

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "WakeContextCase":
        required = {
            "case_id",
            "source_type",
            "provenance",
            "source_transcript",
            "asr_hypotheses",
            "context",
            "expected",
            "safety_label",
            "rubric",
            "confusion_pairs",
        }
        missing = required - set(row)
        case_id = str(row.get("case_id", "<unknown>"))
        if missing:
            raise ValueError(f"wake-context case {case_id} missing fields: {sorted(missing)}")
        if row.get("private_data", "none") != "none":
            raise ValueError(f"wake-context case {case_id} must not contain private data")
        asr = row["asr_hypotheses"]
        if not isinstance(asr, list) or not asr or not all(isinstance(item, str) for item in asr):
            raise ValueError(f"wake-context case {case_id} needs string asr_hypotheses")
        if not isinstance(row["context"], dict):
            raise ValueError(f"wake-context case {case_id} context must be an object")
        if "addressed_to_parker" not in row["context"]:
            raise ValueError(f"wake-context case {case_id} context needs addressed_to_parker")
        if not isinstance(row["expected"], dict) or "kind" not in row["expected"]:
            raise ValueError(f"wake-context case {case_id} expected needs kind")
        if not isinstance(row["provenance"], dict):
            raise ValueError(f"wake-context case {case_id} provenance must be an object")
        if not isinstance(row["confusion_pairs"], list) or not row["confusion_pairs"]:
            raise ValueError(f"wake-context case {case_id} needs confusion_pairs")
        raw_rubric = row["rubric"]
        if not isinstance(raw_rubric, dict) or not raw_rubric:
            raise ValueError(f"wake-context case {case_id} rubric must be a non-empty object")
        try:
            rubric = {str(key): float(value) for key, value in raw_rubric.items()}
        except (TypeError, ValueError) as exc:
            raise ValueError(f"wake-context case {case_id} rubric weights must be numeric") from exc
        if any(not isfinite(weight) or weight <= 0 or weight > 1 for weight in rubric.values()):
            raise ValueError(f"wake-context case {case_id} rubric weights must be in (0, 1]")
        if abs(sum(rubric.values()) - 1.0) > 0.001:
            raise ValueError(f"wake-context case {case_id} rubric weights must sum to 1.0")
        return cls(
            case_id=case_id,
            source_type=str(row["source_type"]),
            source_transcript=str(row["source_transcript"]),
            asr_hypotheses=[str(item) for item in asr],
            context=row["context"],
            expected=row["expected"],
            safety_label=str(row["safety_label"]),
            provenance=row["provenance"],
            confusion_pairs=[str(item) for item in row["confusion_pairs"]],
            rubric=rubric,
        )


def _walk_strings(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_strings(child)
    elif isinstance(value, str):
        yield value


def load_cases(path: Path = DEFAULT_CASES_PATH) -> list[WakeContextCase]:
    payload = json.loads(path.read_text())
    if payload.get("private_data") != "none":
        raise ValueError("wake-context fixture set must be marked private_data=none")
    if any("/Users/" in text for text in _walk_strings(payload)):
        raise ValueError("wake-context fixtures must not contain local /Users paths")
    return [WakeContextCase.from_dict(row) for row in payload.get("cases", [])]


@contextmanager
def _memory_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _fresh_session(db: Session, case_id: str) -> TextSession:
    call = CallLog(call_sid=f"WAKE-CONTEXT-{case_id}", call_type="wake_context_eval")
    db.add(call)
    db.commit()
    db.refresh(call)
    return TextSession(db, call.id, model_client=None)


def _run_case(case: WakeContextCase) -> dict[str, Any]:
    primary, *alternates = case.asr_hypotheses
    expected = case.expected
    context = UtteranceContext(
        addressed_to_parker=bool(case.context.get("addressed_to_parker")),
        source=str(case.context.get("source") or "fixture"),
        note=case.context.get("note"),
    )
    with _memory_session() as db:
        session = _fresh_session(db, case.case_id)
        response = session.handle(primary, alternates=alternates, context=context)
        selected_response: dict[str, Any] | None = None
        handoff_response: dict[str, Any] | None = None
        if "selection_position" in expected:
            selected_response = session.handle(str(expected["selection_position"]), context=context)
        if "research_handoff_selection_position" in expected:
            handoff_response = session.handle(
                str(expected["research_handoff_selection_position"]), context=context
            )
        captured = db.query(CapturedIntent).count()
        research_handoffs = db.query(LocalResearchHandoff).all()
        research_handoff = research_handoffs[0] if len(research_handoffs) == 1 else None

    choices = response.get("choices") or []
    checks = {
        "kind": response.get("kind") == expected["kind"],
        "no_capture": captured == 0 if expected.get("no_capture") else True,
        "no_choices": not choices if expected.get("no_choices") else True,
        "speech": response.get("speech", "") == expected["speech"] if "speech" in expected else True,
        "action_type": (
            response.get("action_type") == expected["action_type"]
            if "action_type" in expected
            else True
        ),
        "first_choice_action_type": (
            bool(choices) and choices[0].get("action_type") == expected["first_choice_action_type"]
            if "first_choice_action_type" in expected
            else True
        ),
        "first_choice_label": (
            bool(choices) and choices[0].get("label") == expected["first_choice_label"]
            if "first_choice_label" in expected
            else True
        ),
        "choice_labels": (
            [choice.get("label") for choice in choices] == expected["choice_labels"]
            if "choice_labels" in expected
            else True
        ),
        "selected_kind": (
            selected_response is not None and selected_response.get("kind") == expected["selected_kind"]
            if "selected_kind" in expected
            else True
        ),
        "resolved_query": (
            selected_response is not None
            and selected_response.get("resolved_query") == expected["resolved_query"]
            if "resolved_query" in expected
            else True
        ),
        "informational_repair_family": (
            selected_response is not None
            and selected_response.get("informational_repair_family")
            == expected["informational_repair_family"]
            if "informational_repair_family" in expected
            else True
        ),
        "research_handoff_offered": (
            selected_response is not None
            and bool(selected_response.get("research_handoff_offered"))
            is bool(expected["research_handoff_offered"])
            if "research_handoff_offered" in expected
            else True
        ),
        "research_handoff_selected_kind": (
            handoff_response is not None
            and handoff_response.get("kind") == expected["research_handoff_selected_kind"]
            if "research_handoff_selected_kind" in expected
            else True
        ),
        "research_handoff_query": (
            research_handoff is not None
            and research_handoff.query == expected["research_handoff_query"]
            if "research_handoff_query" in expected
            else True
        ),
        "research_handoff_status": (
            research_handoff is not None
            and research_handoff.status == expected["research_handoff_status"]
            if "research_handoff_status" in expected
            else True
        ),
        "research_handoff_provenance_status": (
            research_handoff is not None
            and research_handoff.provenance_status
            == expected["research_handoff_provenance_status"]
            if "research_handoff_provenance_status" in expected
            else True
        ),
    }
    unexpected_handoff = bool(research_handoffs) and "research_handoff_selection_position" not in expected
    unsafe = (captured > 0 and expected.get("no_capture", True)) or unexpected_handoff
    nuisance_choice_failure = expected.get("no_choices") and bool(choices)
    passed = all(checks.values()) and not unsafe
    return {
        "case_id": case.case_id,
        "source_type": case.source_type,
        "source_transcript": case.source_transcript,
        "primary_asr": primary,
        "context": case.context,
        "expected_kind": expected["kind"],
        "observed_kind": response.get("kind"),
        "captured_intents": captured,
        "choice_count": len(choices),
        "first_choice_action_type": choices[0].get("action_type") if choices else None,
        "first_choice_label": choices[0].get("label") if choices else None,
        "action_type": response.get("action_type"),
        "selected_kind": selected_response.get("kind") if selected_response else None,
        "resolved_query": selected_response.get("resolved_query") if selected_response else None,
        "informational_repair": bool(
            selected_response and selected_response.get("informational_repair")
        ),
        "informational_repair_family": (
            selected_response.get("informational_repair_family") if selected_response else None
        ),
        "research_handoff_offered": bool(
            selected_response and selected_response.get("research_handoff_offered")
        ),
        "research_handoff_created": research_handoff is not None,
        "research_handoff_query": research_handoff.query if research_handoff else None,
        "research_handoff_status": research_handoff.status if research_handoff else None,
        "research_handoff_provenance_status": (
            research_handoff.provenance_status if research_handoff else None
        ),
        "research_handoff_selected_kind": (
            handoff_response.get("kind") if handoff_response else None
        ),
        "checks": checks,
        "passed": passed,
        "unsafe": unsafe,
        "nuisance_choice_failure": nuisance_choice_failure,
        "speech": response.get("speech", ""),
        "safety_label": case.safety_label,
    }


def evaluate(cases: list[WakeContextCase]) -> dict[str, Any]:
    results = [_run_case(case) for case in cases]
    ambient = [case for case in cases if case.context.get("addressed_to_parker") is False]
    wake = [case for case in cases if case.context.get("addressed_to_parker") is True]
    public = [case for case in cases if case.source_type == "public_corpus_audio_derived"]
    synthetic = [case for case in cases if case.source_type == "synthetic_audio_derived"]
    metrics = {
        "total_cases": len(cases),
        "public_corpus_audio_derived_cases": len(public),
        "synthetic_audio_derived_cases": len(synthetic),
        "ambient_cases": len(ambient),
        "wake_confirmed_cases": len(wake),
        "passed_cases": sum(1 for result in results if result["passed"]),
        "ambient_noop_cases": sum(1 for result in results if result["observed_kind"] == "ambient_noop"),
        "wake_answer_cases": sum(1 for result in results if result["observed_kind"] == "answer"),
        "wake_repair_choice_cases": sum(1 for result in results if result["observed_kind"] == "choices"),
        "wake_informational_repair_answer_cases": sum(
            1 for result in results if result["informational_repair"] and result["selected_kind"] == "answer"
        ),
        "wake_research_handoff_created_cases": sum(
            1 for result in results if result["research_handoff_created"]
        ),
        "wake_context_required_cases": sum(
            1 for result in results if result["observed_kind"] == "context_required"
        ),
        "wake_refusal_cases": sum(1 for result in results if result["observed_kind"] == "refused"),
        "wake_local_capture_cases": sum(1 for result in results if result["observed_kind"] == "captured"),
        "wake_item_search_cases": sum(
            1
            for result in results
            if result["observed_kind"] == "answer" and result["action_type"] == "item_search"
        ),
        "wake_human_approval_cases": sum(
            1 for result in results if result["observed_kind"] == "needs_human_approval"
        ),
        "unsafe_cases": sum(1 for result in results if result["unsafe"]),
        "nuisance_choice_failures": sum(1 for result in results if result["nuisance_choice_failure"]),
    }
    gates = [
        ("has_cases", metrics["total_cases"] >= 5),
        ("has_public_audio_metadata", metrics["public_corpus_audio_derived_cases"] >= 5),
        ("has_synthetic_audio_metadata", metrics["synthetic_audio_derived_cases"] >= 1),
        ("has_ambient_lane", metrics["ambient_cases"] >= 2),
        ("has_wake_confirmed_lane", metrics["wake_confirmed_cases"] >= 2),
        ("all_cases_pass", metrics["passed_cases"] == metrics["total_cases"]),
        ("no_unsafe_cases", metrics["unsafe_cases"] == 0),
        ("no_nuisance_choices_where_forbidden", metrics["nuisance_choice_failures"] == 0),
    ]
    return {
        "eval": "wake_context_audio_v0",
        "date": date.today().isoformat(),
        "metrics": metrics,
        "gate": {
            "passed": all(ok for _, ok in gates),
            "checks": [{"name": name, "passed": ok} for name, ok in gates],
        },
        "provenance": {
            "fixture_policy": "metadata-only public/synthetic ASR/source labels; raw audio remains in Operations and is not committed",
            "private_data": "none",
            "claim_status": "pipeline fixture coverage only; not real-world wake-word accuracy or clinical evidence",
        },
        "results": results,
    }


def _write_reports(payload: dict[str, Any], reports_dir: Path = DEFAULT_REPORTS_DIR) -> None:
    reports_dir.mkdir(parents=True, exist_ok=True)
    dated_json = reports_dir / f"wake_context_audio_eval_{payload['date']}.json"
    latest_json = reports_dir / "wake_context_audio_eval_latest.json"
    dated_md = reports_dir / f"wake_context_audio_eval_{payload['date']}.md"
    latest_md = reports_dir / "wake_context_audio_eval_latest.md"
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    dated_json.write_text(text)
    latest_json.write_text(text)

    lines = [
        "# Parker wake-context audio eval v0",
        "",
        f"- Date: {payload['date']}",
        "- Provenance: metadata-only public/synthetic audio-derived ASR hypotheses; raw audio not committed.",
        "- Purpose: verify explicit addressed-to-Parker context before repair/capture/answer routing.",
        "- Caveat: pipeline fixture coverage only; not wake-word accuracy, clinical evidence, or real-world deployment proof.",
        "",
        "## Metrics",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
    ]
    for key, value in payload["metrics"].items():
        lines.append(f"| {key} | {value} |")
    lines.extend(["", "## Gate", "", f"- Passed: `{payload['gate']['passed']}`", ""])
    for check in payload["gate"]["checks"]:
        status = "PASS" if check["passed"] else "FAIL"
        lines.append(f"- {status} `{check['name']}`")
    lines.extend(["", "## Case breakdown", ""])
    for result in payload["results"]:
        selection = (
            f"; selected={result['selected_kind']}; resolved_query={result['resolved_query']!r}"
            if result["selected_kind"]
            else ""
        )
        lines.append(
            "- `{case_id}`: context={context}; ASR={asr!r}; expected={expected}; "
            "observed={observed}; choices={choices}; captured={captured}{selection}; passed={passed}".format(
                case_id=result["case_id"],
                context="addressed" if result["context"].get("addressed_to_parker") else "ambient",
                asr=result["primary_asr"],
                expected=result["expected_kind"],
                observed=result["observed_kind"],
                choices=result["choice_count"],
                captured=result["captured_intents"],
                selection=selection,
                passed=result["passed"],
            )
        )
    markdown = "\n".join(lines) + "\n"
    dated_md.write_text(markdown)
    latest_md.write_text(markdown)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--write-report", action="store_true")
    args = parser.parse_args()

    payload = evaluate(load_cases(args.cases))
    if args.write_report:
        _write_reports(payload)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        metrics = payload["metrics"]
        print(
            "wake_context_audio_v0: "
            f"{metrics['passed_cases']}/{metrics['total_cases']} passed; "
            f"ambient_noop={metrics['ambient_noop_cases']}; "
            f"wake_answers={metrics['wake_answer_cases']}; "
            f"wake_choices={metrics['wake_repair_choice_cases']}; "
            f"wake_research_handoffs={metrics['wake_research_handoff_created_cases']}; "
            f"wake_context_required={metrics['wake_context_required_cases']}; "
            f"wake_refusals={metrics['wake_refusal_cases']}; "
            f"wake_captures={metrics['wake_local_capture_cases']}; "
            f"wake_item_search={metrics['wake_item_search_cases']}; "
            f"wake_human_approval={metrics['wake_human_approval_cases']}; "
            f"unsafe={metrics['unsafe_cases']}; gate={payload['gate']['passed']}"
        )
    raise SystemExit(0 if payload["gate"]["passed"] else 1)


if __name__ == "__main__":
    main()
