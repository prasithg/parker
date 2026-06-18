"""Synthetic repair-quality rubric for Parker grant evidence.

This evaluator is deliberately a *proxy rubric*, not human-graded quality
proof. It checks that repair choices are specific to the utterance, include a
"none of these" escape hatch, and avoid unsafe/prohibited action types. It also
scores Parker's generic no-key fallback as a named baseline so the grant packet
cannot cite fallback repair choices as semantic-quality evidence.

Usage:
    python3 benchmark/evaluate_repair_quality_rubric_v0.py
    python3 benchmark/evaluate_repair_quality_rubric_v0.py --json
    python3 benchmark/evaluate_repair_quality_rubric_v0.py --write-report
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUBRIC_PATH = REPO_ROOT / "benchmark" / "data" / "parker_repair_quality_rubric_v0.json"
DEFAULT_REPORTS_DIR = REPO_ROOT / "benchmark" / "reports"
NONE_OF_THESE = "none of these"
MAX_LABEL_LENGTH = 80
VALID_ACTION_TYPES = {"reminder", "family_message"}


@dataclass(frozen=True)
class RepairQualityCase:
    """One synthetic rubric case with reference and generic-fallback choices."""

    case_id: str
    utterance: str
    required_terms_any: list[str]
    generic_phrases: list[str]
    forbidden_terms: list[str]
    allowed_action_types: list[str]
    reference_choices: list[dict[str, Any]]
    generic_fallback_choices: list[dict[str, Any]]
    privacy: str = "synthetic"

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "RepairQualityCase":
        required = {
            "case_id",
            "utterance",
            "required_terms_any",
            "generic_phrases",
            "forbidden_terms",
            "allowed_action_types",
            "reference_choices",
            "generic_fallback_choices",
            "privacy",
        }
        missing = required - set(row)
        case_id = str(row.get("case_id", "<unknown>"))
        if missing:
            raise ValueError(f"repair-quality case {case_id} missing fields: {sorted(missing)}")
        if row["privacy"] != "synthetic":
            raise ValueError(f"repair-quality case {case_id} must be synthetic")
        return cls(
            case_id=_required_text(row["case_id"], "case_id"),
            utterance=_required_text(row["utterance"], f"{case_id}.utterance"),
            required_terms_any=_string_list(row["required_terms_any"], case_id, "required_terms_any"),
            generic_phrases=_string_list(row["generic_phrases"], case_id, "generic_phrases"),
            forbidden_terms=_string_list(row["forbidden_terms"], case_id, "forbidden_terms", allow_empty=True),
            allowed_action_types=_string_list(row["allowed_action_types"], case_id, "allowed_action_types"),
            reference_choices=_choice_list(row["reference_choices"], case_id, "reference_choices"),
            generic_fallback_choices=_choice_list(
                row["generic_fallback_choices"], case_id, "generic_fallback_choices"
            ),
            privacy="synthetic",
        )

    def with_predictions(self, *, reference_choices: list[dict[str, Any]]) -> "RepairQualityCase":
        """Return a copy with alternate reference predictions for focused tests."""

        return replace(self, reference_choices=reference_choices)


@dataclass(frozen=True)
class ChoiceFailure:
    """A single repair-quality rubric failure."""

    check: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"check": self.check, "message": self.message}


@dataclass(frozen=True)
class CaseScore:
    """Score for one system on one repair-quality case."""

    case_id: str
    passed: bool
    failures: list[ChoiceFailure]

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "failures": [failure.as_dict() for failure in self.failures],
        }


@dataclass(frozen=True)
class RepairQualityRubricEvalResult:
    """Aggregate synthetic repair-quality rubric result."""

    cases: list[RepairQualityCase]
    reference_scores: list[CaseScore]
    generic_fallback_scores: list[CaseScore]

    def as_dict(self) -> dict[str, Any]:
        total = len(self.cases)
        reference_passing = sum(score.passed for score in self.reference_scores)
        fallback_passing = sum(score.passed for score in self.generic_fallback_scores)
        reference_failures = total - reference_passing
        fallback_failures = total - fallback_passing
        rubric_detects_generic = fallback_passing < total
        gate_passed = (
            total >= 5
            and reference_passing == total
            and fallback_passing == 0
            and rubric_detects_generic
        )
        quality_proof_claim_allowed = False
        return {
            "eval": "repair_quality_rubric_v0",
            "provenance": {
                "private_data": "none",
                "fixture_policy": "public synthetic/local rubric cases only",
                "model_or_api_dependency": "none",
                "human_grade_dependency": "none; this is a proxy rubric, not human evidence",
            },
            "metrics": {
                "total_cases": total,
                "reference_passing_cases": reference_passing,
                "generic_fallback_passing_cases": fallback_passing,
                "reference_failures": reference_failures,
                "generic_fallback_failures": fallback_failures,
                "rubric_detects_generic_fallback": rubric_detects_generic,
                "quality_proof_claim_allowed": quality_proof_claim_allowed,
            },
            "rubric_gate": {
                "passed": gate_passed,
                "blocking_failures": 0 if gate_passed else 1,
            },
            "grant_posture": {
                "safe_claim": (
                    "A deterministic synthetic rubric now checks repair-choice specificity and safety; "
                    "the generic no-key fallback is explicitly flagged as non-citable quality evidence."
                ),
                "required_caveat": (
                    "Synthetic proxy rubric only; not human-graded repair quality, not real patient/audio evidence, "
                    "and not a claim that Parker's fallback choices are semantically good."
                ),
                "next_research_step": (
                    "Use this rubric as a seed for human/caregiver repair-choice grading and realtime audio slices."
                ),
            },
            "systems": {
                "reference": {
                    "description": "Curated synthetic repair choices that satisfy the proxy rubric.",
                    "case_results": [score.as_dict() for score in self.reference_scores],
                },
                "generic_fallback": {
                    "description": "Current no-key generic fallback; intentionally expected to fail specificity.",
                    "case_results": [score.as_dict() for score in self.generic_fallback_scores],
                },
            },
            "cases": [
                {
                    "case_id": case.case_id,
                    "utterance": case.utterance,
                    "required_terms_any": case.required_terms_any,
                    "privacy": case.privacy,
                }
                for case in self.cases
            ],
        }


def load_rubric_cases(path: Path = DEFAULT_RUBRIC_PATH) -> list[RepairQualityCase]:
    """Load and validate synthetic repair-quality rubric cases."""

    try:
        parsed = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("cases"), list):
        raise ValueError(f"{path}: expected an object with a cases array")
    cases = [RepairQualityCase.from_dict(row) for row in parsed["cases"]]
    if not cases:
        raise ValueError("repair-quality rubric has no cases")
    seen: set[str] = set()
    for case in cases:
        if case.case_id in seen:
            raise ValueError(f"duplicate repair-quality case_id: {case.case_id}")
        seen.add(case.case_id)
    return cases


def evaluate_repair_quality_rubric(cases: list[RepairQualityCase]) -> RepairQualityRubricEvalResult:
    """Score reference and generic-fallback choices against the proxy rubric."""

    if not cases:
        raise ValueError("repair-quality rubric has no cases")
    reference_scores = [score_choices(case, case.reference_choices) for case in cases]
    generic_scores = [score_choices(case, case.generic_fallback_choices) for case in cases]
    return RepairQualityRubricEvalResult(
        cases=cases,
        reference_scores=reference_scores,
        generic_fallback_scores=generic_scores,
    )


def score_choices(case: RepairQualityCase, choices: list[dict[str, Any]]) -> CaseScore:
    """Score a single system's choices for one case."""

    failures: list[ChoiceFailure] = []
    if len(choices) != 3:
        failures.append(ChoiceFailure("choice_count", "expected exactly 2 candidates plus none-of-these"))
    labels = [_label(choice) for choice in choices]
    candidate_choices = choices[:-1] if choices else []
    candidate_labels = labels[:-1] if labels else []

    if not choices or _label(choices[-1]) != NONE_OF_THESE or choices[-1].get("action_type") is not None:
        failures.append(ChoiceFailure("none_of_these_escape", "last choice must be a none-of-these escape hatch"))

    if any(not label for label in labels):
        failures.append(ChoiceFailure("label_text", "choice labels must be non-empty"))
    if any(len(label) > MAX_LABEL_LENGTH for label in labels):
        failures.append(ChoiceFailure("label_length", f"choice labels must be <= {MAX_LABEL_LENGTH} chars"))
    if len({label.lower() for label in labels}) != len(labels):
        failures.append(ChoiceFailure("label_uniqueness", "choice labels must be unique"))

    allowed = set(case.allowed_action_types) & VALID_ACTION_TYPES
    unsafe_actions = [
        str(choice.get("action_type"))
        for choice in candidate_choices
        if choice.get("action_type") not in allowed
    ]
    if unsafe_actions:
        failures.append(ChoiceFailure("safe_action_type", f"unsafe or unsupported action types: {unsafe_actions}"))

    candidate_text = " ".join(candidate_labels).lower()
    if not any(term.lower() in candidate_text for term in case.required_terms_any):
        failures.append(
            ChoiceFailure(
                "specificity",
                f"choices must include one of the case-specific terms: {case.required_terms_any}",
            )
        )
    generic_hits = [phrase for phrase in case.generic_phrases if phrase.lower() in candidate_text]
    if generic_hits:
        failures.append(ChoiceFailure("specificity", f"generic phrases are not quality evidence: {generic_hits}"))

    forbidden_hits = [term for term in case.forbidden_terms if term.lower() in candidate_text]
    if forbidden_hits:
        failures.append(ChoiceFailure("safe_content", f"forbidden content appeared in choices: {forbidden_hits}"))

    return CaseScore(case_id=case.case_id, passed=not failures, failures=failures)


def format_summary(result: RepairQualityRubricEvalResult) -> str:
    payload = result.as_dict()
    metrics = payload["metrics"]
    return "\n".join(
        [
            "Parker repair-quality rubric eval v0",
            "",
            f"Cases: {metrics['total_cases']}",
            f"Reference choices passing: {metrics['reference_passing_cases']}/{metrics['total_cases']}",
            f"Generic fallback passing: {metrics['generic_fallback_passing_cases']}/{metrics['total_cases']}",
            f"Rubric detects generic fallback: {metrics['rubric_detects_generic_fallback']}",
            f"Rubric gate passed: {payload['rubric_gate']['passed']}",
            "",
            f"Safe claim: {payload['grant_posture']['safe_claim']}",
            f"Caveat: {payload['grant_posture']['required_caveat']}",
        ]
    )


def format_markdown_report(result: RepairQualityRubricEvalResult, run_date: str) -> str:
    payload = result.as_dict()
    metrics = payload["metrics"]
    lines = [
        "# Parker repair-quality rubric eval v0",
        "",
        f"- Date: {run_date}",
        "- Purpose: proxy-check repair-choice specificity/safety and prevent generic fallback choices from being cited as quality evidence.",
        "- Provenance: public synthetic/local rubric cases only; no private data; no model/API dependency; not human-graded evidence.",
        "",
        "## Gate",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Total cases | {metrics['total_cases']} |",
        f"| Reference passing | {metrics['reference_passing_cases']} |",
        f"| Generic fallback passing | {metrics['generic_fallback_passing_cases']} |",
        f"| Rubric detects generic fallback | {metrics['rubric_detects_generic_fallback']} |",
        f"| Quality proof claim allowed | {metrics['quality_proof_claim_allowed']} |",
        f"| Gate passed | {payload['rubric_gate']['passed']} |",
        "",
        "## Grant posture",
        "",
        f"- Safe claim: {payload['grant_posture']['safe_claim']}",
        f"- Required caveat: {payload['grant_posture']['required_caveat']}",
        f"- Next research step: {payload['grant_posture']['next_research_step']}",
        "",
        "## Case results",
        "",
        "| Case | Reference | Generic fallback | Required terms |",
        "| --- | ---: | ---: | --- |",
    ]
    fallback_by_id = {score.case_id: score for score in result.generic_fallback_scores}
    for score in result.reference_scores:
        case = next(case for case in result.cases if case.case_id == score.case_id)
        fallback = fallback_by_id[score.case_id]
        lines.append(
            f"| {score.case_id} | {score.passed} | {fallback.passed} | {', '.join(case.required_terms_any)} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_report(result: RepairQualityRubricEvalResult, reports_dir: Path = DEFAULT_REPORTS_DIR) -> list[Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    run_date = date.today().isoformat()
    markdown = format_markdown_report(result, run_date)
    payload = {"date": run_date, **result.as_dict()}
    written: list[Path] = []
    for stem in ("repair_quality_rubric_eval_latest", f"repair_quality_rubric_eval_{run_date}"):
        md_path = reports_dir / f"{stem}.md"
        json_path = reports_dir / f"{stem}.json"
        md_path.write_text(markdown)
        json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        written.extend([md_path, json_path])
    return written


def _choice_list(value: Any, case_id: str, field_name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(f"repair-quality case {case_id} {field_name} must be a list")
    choices: list[dict[str, Any]] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"repair-quality case {case_id} {field_name}[{index}] must be an object")
        if "label" not in item or "action_type" not in item:
            raise ValueError(f"repair-quality case {case_id} {field_name}[{index}] needs label/action_type")
        choices.append({"label": item["label"], "action_type": item["action_type"]})
    return choices


def _string_list(value: Any, case_id: str, field_name: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError(f"repair-quality case {case_id} {field_name} must be a non-empty list")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"repair-quality case {case_id} {field_name} must contain non-empty strings")
    return [str(item).strip() for item in value]


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty text")
    return value.strip()


def _label(choice: dict[str, Any]) -> str:
    return str(choice.get("label", "")).strip().lower()


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT))
    except ValueError:
        return str(resolved)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rubric", type=Path, default=DEFAULT_RUBRIC_PATH)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument("--write-report", action="store_true", help="Write latest and dated JSON/Markdown reports")
    parser.add_argument("--reports-dir", type=Path, default=DEFAULT_REPORTS_DIR)
    args = parser.parse_args()

    result = evaluate_repair_quality_rubric(load_rubric_cases(args.rubric))
    payload = result.as_dict()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(format_summary(result))
    if args.write_report:
        for path in write_report(result, args.reports_dir):
            print(f"wrote {_display_path(path)}")
    return 0 if payload["rubric_gate"]["passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
