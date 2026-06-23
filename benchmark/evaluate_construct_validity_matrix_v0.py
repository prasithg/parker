"""Construct-validity matrix evaluator for Parker grant evidence.

This guard sits between the claim→metric map and the prose grant packet. It
keeps two things explicit:

1. which constructs are currently citable from synthetic/local reports; and
2. which attractive constructs are research gaps that the grant should fund,
   not claims the current demo has already proven.

Usage:
    python3 benchmark/evaluate_construct_validity_matrix_v0.py
    python3 benchmark/evaluate_construct_validity_matrix_v0.py --json
    python3 benchmark/evaluate_construct_validity_matrix_v0.py --write-report
"""

from __future__ import annotations

import argparse
import json
import operator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX_PATH = REPO_ROOT / "benchmark" / "data" / "parker_construct_validity_matrix_v0.json"
DEFAULT_REPORTS_DIR = REPO_ROOT / "benchmark" / "reports"

CURRENTLY_CITABLE = "citable_with_caveats"
RESEARCH_GAP = "research_gap_not_citable_yet"
_VALID_SUPPORT_LEVELS = {CURRENTLY_CITABLE, RESEARCH_GAP}
_REQUIRED_FIELDS = {
    "construct_id",
    "capability",
    "grant_criterion",
    "construct_question",
    "operationalization",
    "current_claim_support",
    "metric_ids",
    "evidence_paths",
    "required_assertions",
    "baseline",
    "safety_gate",
    "caveat",
    "known_limitations",
    "upgrade_path",
    "public_private_scope",
}
_OPERATORS: dict[str, Callable[[Any, Any], bool]] = {
    "eq": operator.eq,
    "gte": operator.ge,
    "lte": operator.le,
    "contains": lambda actual, expected: expected in actual,
}
_PLACEHOLDERS = {"", "none", "n/a", "na", "tbd", "todo"}


@dataclass(frozen=True)
class RequiredAssertion:
    """One report-backed metric assertion for a construct row."""

    report_path: str
    json_path: str
    operator: str
    expected: Any

    @classmethod
    def from_dict(cls, row: dict[str, Any], construct_id: str) -> "RequiredAssertion":
        missing = {"report_path", "json_path", "operator", "expected"} - set(row)
        if missing:
            raise ValueError(f"construct {construct_id} assertion missing fields: {sorted(missing)}")
        op_name = str(row["operator"])
        if op_name not in _OPERATORS:
            raise ValueError(f"construct {construct_id} uses unsupported operator {op_name!r}")
        return cls(
            report_path=str(row["report_path"]),
            json_path=str(row["json_path"]),
            operator=op_name,
            expected=row["expected"],
        )


@dataclass(frozen=True)
class ConstructValidityRow:
    """One construct-validity row for grant evidence or a marked research gap."""

    construct_id: str
    capability: str
    grant_criterion: str
    construct_question: str
    operationalization: str
    current_claim_support: str
    metric_ids: list[str]
    evidence_paths: list[str]
    required_assertions: list[RequiredAssertion]
    baseline: str
    safety_gate: str
    caveat: str
    known_limitations: str
    upgrade_path: str
    public_private_scope: str

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "ConstructValidityRow":
        missing = _REQUIRED_FIELDS - set(row)
        construct_id = str(row.get("construct_id", "<unknown>"))
        if missing:
            raise ValueError(f"construct {construct_id} missing fields: {sorted(missing)}")
        if not construct_id.strip():
            raise ValueError("construct_id must be non-empty")

        support = _required_text(row["current_claim_support"], construct_id, "current_claim_support")
        if support not in _VALID_SUPPORT_LEVELS:
            raise ValueError(
                f"construct {construct_id} current_claim_support must be one of {sorted(_VALID_SUPPORT_LEVELS)}"
            )
        public_private_scope = str(row["public_private_scope"])
        if public_private_scope != "public_synthetic_only":
            raise ValueError(f"construct {construct_id} public_private_scope must be public_synthetic_only")

        metric_ids = _string_list(row["metric_ids"], construct_id, "metric_ids", allow_empty=False)
        evidence_paths = _string_list(
            row["evidence_paths"], construct_id, "evidence_paths", allow_empty=(support == RESEARCH_GAP)
        )
        assertions_raw = row["required_assertions"]
        if not isinstance(assertions_raw, list):
            raise ValueError(f"construct {construct_id} required_assertions must be a list")
        required_assertions = [RequiredAssertion.from_dict(item, construct_id) for item in assertions_raw]

        caveat = _required_text(row["caveat"], construct_id, "caveat")
        baseline = _required_text(row["baseline"], construct_id, "baseline")
        safety_gate = _required_text(row["safety_gate"], construct_id, "safety_gate")
        known_limitations = _required_text(row["known_limitations"], construct_id, "known_limitations")
        upgrade_path = _required_text(row["upgrade_path"], construct_id, "upgrade_path")

        if support == CURRENTLY_CITABLE:
            if not evidence_paths:
                raise ValueError(f"construct {construct_id} needs evidence_paths to be citable")
            if not required_assertions:
                raise ValueError(f"construct {construct_id} needs report-backed assertions to be citable")
            if "not real" not in caveat.lower() and "no private" not in caveat.lower():
                raise ValueError(f"construct {construct_id} caveat must limit real-world/private-data scope")
        else:
            if required_assertions:
                raise ValueError(f"construct {construct_id} research gaps must not include passing assertions")
            if "research gap" not in caveat.lower() and "not real" not in caveat.lower():
                raise ValueError(f"construct {construct_id} research gap caveat must be explicit")

        return cls(
            construct_id=construct_id,
            capability=str(row["capability"]),
            grant_criterion=str(row["grant_criterion"]),
            construct_question=_required_text(row["construct_question"], construct_id, "construct_question"),
            operationalization=_required_text(row["operationalization"], construct_id, "operationalization"),
            current_claim_support=support,
            metric_ids=metric_ids,
            evidence_paths=evidence_paths,
            required_assertions=required_assertions,
            baseline=baseline,
            safety_gate=safety_gate,
            caveat=caveat,
            known_limitations=known_limitations,
            upgrade_path=upgrade_path,
            public_private_scope=public_private_scope,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "construct_id": self.construct_id,
            "capability": self.capability,
            "grant_criterion": self.grant_criterion,
            "construct_question": self.construct_question,
            "operationalization": self.operationalization,
            "current_claim_support": self.current_claim_support,
            "metric_ids": self.metric_ids,
            "evidence_paths": self.evidence_paths,
            "required_assertions": [assertion.__dict__ for assertion in self.required_assertions],
            "baseline": self.baseline,
            "safety_gate": self.safety_gate,
            "caveat": self.caveat,
            "known_limitations": self.known_limitations,
            "upgrade_path": self.upgrade_path,
            "public_private_scope": self.public_private_scope,
        }


@dataclass(frozen=True)
class AssertionResult:
    """Result of checking one construct assertion."""

    construct_id: str
    report_path: str
    json_path: str
    operator: str
    expected: Any
    actual: Any
    passed: bool
    message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "construct_id": self.construct_id,
            "report_path": self.report_path,
            "json_path": self.json_path,
            "operator": self.operator,
            "expected": self.expected,
            "actual": self.actual,
            "passed": self.passed,
            "message": self.message,
        }


@dataclass(frozen=True)
class ConstructValidityEvalResult:
    """Aggregate construct-validity guard result."""

    rows: list[ConstructValidityRow]
    assertion_results: list[AssertionResult]
    evidence_paths_checked: list[str]
    report_load_errors: list[dict[str, str]] = field(default_factory=list)

    @property
    def citable_rows(self) -> list[ConstructValidityRow]:
        return [row for row in self.rows if row.current_claim_support == CURRENTLY_CITABLE]

    @property
    def research_gap_rows(self) -> list[ConstructValidityRow]:
        return [row for row in self.rows if row.current_claim_support == RESEARCH_GAP]

    @property
    def failing_assertions(self) -> list[AssertionResult]:
        return [result for result in self.assertion_results if not result.passed]

    @property
    def passing_citable_ids(self) -> set[str]:
        failing = {result.construct_id for result in self.failing_assertions}
        errored = {error["construct_id"] for error in self.report_load_errors}
        return {row.construct_id for row in self.citable_rows} - failing - errored

    def as_dict(self) -> dict[str, Any]:
        citable_count = len(self.citable_rows)
        research_gap_count = len(self.research_gap_rows)
        passing_citable = len(self.passing_citable_ids)
        failing_citable = citable_count - passing_citable
        assertions_failed = len(self.failing_assertions)
        gate_passed = (
            citable_count >= 4
            and research_gap_count >= 1
            and passing_citable == citable_count
            and assertions_failed == 0
            and not self.report_load_errors
            and {row.public_private_scope for row in self.rows} == {"public_synthetic_only"}
        )
        return {
            "eval": "construct_validity_matrix_v0",
            "provenance": {
                "purpose": "proposal construct-validity guard: distinguish citable synthetic/local evidence from grant-funded research gaps",
                "private_data": "none",
                "fixture_policy": "public synthetic/local reports only",
                "model_or_api_dependency": "none",
            },
            "metrics": {
                "total_constructs": len(self.rows),
                "citable_constructs": citable_count,
                "research_gap_constructs": research_gap_count,
                "passing_citable_constructs": passing_citable,
                "failing_citable_constructs": failing_citable,
                "assertions_checked": len(self.assertion_results),
                "assertions_failed": assertions_failed,
            },
            "construct_validity_gate": {
                "passed": gate_passed,
                "blocking_failures": failing_citable + len(self.report_load_errors),
            },
            "passing_construct_ids": sorted(self.passing_citable_ids),
            "research_gap_cards": [
                {
                    "construct_id": row.construct_id,
                    "capability": row.capability,
                    "grant_criterion": row.grant_criterion,
                    "known_limitations": row.known_limitations,
                    "upgrade_path": row.upgrade_path,
                    "caveat": row.caveat,
                }
                for row in self.research_gap_rows
            ],
            "evidence_paths_checked": self.evidence_paths_checked,
            "constructs": [row.as_dict() for row in self.rows],
            "assertion_results": [result.as_dict() for result in self.assertion_results],
            "failing_assertions": [result.as_dict() for result in self.failing_assertions],
            "report_load_errors": self.report_load_errors,
        }


def load_matrix(path: Path = DEFAULT_MATRIX_PATH) -> list[ConstructValidityRow]:
    """Load and validate construct-validity rows."""

    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected a JSON array")
    rows = [ConstructValidityRow.from_dict(row) for row in raw]
    if not rows:
        raise ValueError("construct-validity matrix is empty")
    seen: set[str] = set()
    for row in rows:
        if row.construct_id in seen:
            raise ValueError(f"duplicate construct_id: {row.construct_id}")
        seen.add(row.construct_id)
    return rows


def evaluate_constructs(rows: list[ConstructValidityRow], repo_root: Path = REPO_ROOT) -> ConstructValidityEvalResult:
    """Evaluate report-backed assertions for currently citable construct rows."""

    report_cache: dict[str, dict[str, Any]] = {}
    evidence_paths: set[str] = set()
    assertion_results: list[AssertionResult] = []
    report_load_errors: list[dict[str, str]] = []

    for row in rows:
        for report_path in row.evidence_paths:
            evidence_paths.add(report_path)
            if report_path in report_cache:
                continue
            try:
                report_cache[report_path] = _read_report_json(repo_root / report_path)
            except Exception as exc:  # noqa: BLE001 - surfaced as structured eval failure
                report_load_errors.append({"construct_id": row.construct_id, "report_path": report_path, "error": str(exc)})

        for assertion in row.required_assertions:
            evidence_paths.add(assertion.report_path)
            report = report_cache.get(assertion.report_path)
            if report is None:
                assertion_results.append(
                    AssertionResult(
                        construct_id=row.construct_id,
                        report_path=assertion.report_path,
                        json_path=assertion.json_path,
                        operator=assertion.operator,
                        expected=assertion.expected,
                        actual=None,
                        passed=False,
                        message="report could not be loaded",
                    )
                )
                continue
            actual = _json_path(report, assertion.json_path)
            passed, message = _compare(actual, assertion.operator, assertion.expected)
            assertion_results.append(
                AssertionResult(
                    construct_id=row.construct_id,
                    report_path=assertion.report_path,
                    json_path=assertion.json_path,
                    operator=assertion.operator,
                    expected=assertion.expected,
                    actual=actual,
                    passed=passed,
                    message=message,
                )
            )

    return ConstructValidityEvalResult(
        rows=rows,
        assertion_results=assertion_results,
        evidence_paths_checked=sorted(evidence_paths),
        report_load_errors=report_load_errors,
    )


def _string_list(value: Any, construct_id: str, field_name: str, *, allow_empty: bool) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"construct {construct_id} {field_name} must be a list")
    if not allow_empty and not value:
        raise ValueError(f"construct {construct_id} {field_name} must be non-empty")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"construct {construct_id} {field_name} must contain only non-empty strings")
    return [str(item) for item in value]


def _required_text(value: Any, construct_id: str, field_name: str) -> str:
    text = str(value).strip() if isinstance(value, str) else ""
    if text.lower() in _PLACEHOLDERS:
        raise ValueError(f"construct {construct_id} {field_name} must name concrete {field_name.replace('_', ' ')}")
    return text


def _read_report_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    parsed = json.loads(path.read_text())
    if not isinstance(parsed, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return parsed


def _json_path(payload: dict[str, Any], dotted_path: str) -> Any:
    current: Any = payload
    for part in dotted_path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        raise KeyError(f"missing JSON path component {part!r} in {dotted_path!r}")
    return current


def _compare(actual: Any, op_name: str, expected: Any) -> tuple[bool, str | None]:
    try:
        passed = bool(_OPERATORS[op_name](actual, expected))
    except Exception as exc:  # noqa: BLE001 - typed into report
        return False, f"comparison raised {exc.__class__.__name__}: {exc}"
    if passed:
        return True, None
    return False, f"expected actual {op_name} {expected!r}, got {actual!r}"


def format_summary(result: ConstructValidityEvalResult) -> str:
    payload = result.as_dict()
    metrics = payload["metrics"]
    gate = payload["construct_validity_gate"]
    lines = [
        "Parker construct-validity matrix eval v0",
        "",
        f"Constructs: {metrics['total_constructs']} total; {metrics['citable_constructs']} citable; {metrics['research_gap_constructs']} research gaps",
        f"Citable gate: {metrics['passing_citable_constructs']}/{metrics['citable_constructs']} passing; assertions {metrics['assertions_checked']} checked / {metrics['assertions_failed']} failed",
        f"Construct-validity gate passed: {gate['passed']}",
        "Evidence paths checked:",
    ]
    lines.extend(f"  - {path}" for path in payload["evidence_paths_checked"])
    lines.extend(["", "Research gaps to keep out of current claims:"])
    for gap in payload["research_gap_cards"]:
        lines.append(f"  - {gap['construct_id']}: {gap['capability']} — {gap['known_limitations']}")
    lines.extend(
        [
            "",
            "Caveat: passing means current proposal constructs are tied to synthetic/local evidence and explicit gaps; it is not real-world clinical, audio, or patient proof.",
        ]
    )
    if payload["failing_assertions"] or payload["report_load_errors"]:
        lines.append("")
        lines.append("Blocking failures:")
        for failure in payload["failing_assertions"]:
            lines.append(f"  - {failure['construct_id']} {failure['json_path']}: {failure['message']}")
        for error in payload["report_load_errors"]:
            lines.append(f"  - {error['construct_id']} {error['report_path']}: {error['error']}")
    return "\n".join(lines)


def format_markdown_report(result: ConstructValidityEvalResult, run_date: str) -> str:
    payload = result.as_dict()
    metrics = payload["metrics"]
    gate = payload["construct_validity_gate"]
    lines = [
        "# Parker construct-validity matrix eval v0",
        "",
        f"- Date: {run_date}",
        "- Purpose: distinguish current citable synthetic/local evidence from grant-funded research gaps.",
        "- Provenance: public synthetic/local reports only; no private data; no model/API dependency.",
        "",
        "## Gate",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Total constructs | {metrics['total_constructs']} |",
        f"| Citable constructs | {metrics['citable_constructs']} |",
        f"| Research-gap constructs | {metrics['research_gap_constructs']} |",
        f"| Passing citable constructs | {metrics['passing_citable_constructs']} |",
        f"| Failing citable constructs | {metrics['failing_citable_constructs']} |",
        f"| Assertions checked | {metrics['assertions_checked']} |",
        f"| Assertions failed | {metrics['assertions_failed']} |",
        f"| Gate passed | {gate['passed']} |",
        "",
        "## Construct matrix",
        "",
        "| Construct | Capability | Criterion | Support | Metrics | Baseline | Known limits | Upgrade path |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in result.rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row.construct_id,
                    row.capability,
                    row.grant_criterion,
                    row.current_claim_support,
                    ", ".join(row.metric_ids),
                    row.baseline,
                    row.known_limitations,
                    row.upgrade_path,
                ]
            )
            + " |"
        )
    lines.extend(["", "## Evidence paths checked", ""])
    lines.extend(f"- `{path}`" for path in payload["evidence_paths_checked"])
    lines.extend(["", "## Assertion results", ""])
    for assertion in payload["assertion_results"]:
        status = "PASS" if assertion["passed"] else "FAIL"
        lines.append(
            f"- **{status}** `{assertion['construct_id']}` `{assertion['report_path']}` "
            f"`{assertion['json_path']}` {assertion['operator']} `{assertion['expected']}` "
            f"(actual `{assertion['actual']}`)"
        )
    lines.extend(["", "## Research gaps", ""])
    for gap in payload["research_gap_cards"]:
        lines.append(f"- **{gap['construct_id']}** — {gap['known_limitations']} Upgrade: {gap['upgrade_path']}")
    lines.extend(
        [
            "",
            "## Scope caveat",
            "",
            "Passing this guard means the grant packet distinguishes current synthetic/local evidence from research gaps. It does not establish clinical efficacy, real Parkinson's audio performance, emergency readiness, or production privacy safety.",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(result: ConstructValidityEvalResult, reports_dir: Path = DEFAULT_REPORTS_DIR) -> list[Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    run_date = date.today().isoformat()
    markdown = format_markdown_report(result, run_date)
    payload = {"date": run_date, **result.as_dict()}
    written: list[Path] = []
    for stem in ("construct_validity_matrix_eval_latest", f"construct_validity_matrix_eval_{run_date}"):
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX_PATH)
    parser.add_argument("--json", action="store_true", help="Print JSON instead of text summary")
    parser.add_argument("--write-report", action="store_true", help="Write markdown+JSON reports to benchmark/reports")
    parser.add_argument("--reports-dir", type=Path, default=DEFAULT_REPORTS_DIR)
    args = parser.parse_args()

    result = evaluate_constructs(load_matrix(args.matrix))
    payload = result.as_dict()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(format_summary(result))
    if args.write_report:
        for path in write_report(result, args.reports_dir):
            print(f"wrote {_display_path(path)}")
    return 0 if payload["construct_validity_gate"]["passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
