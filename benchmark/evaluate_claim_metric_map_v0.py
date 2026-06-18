"""Grant-facing claim→metric map evaluator for Parker v0.

This harness keeps the Thinking Machines grant packet honest: every major
proposal-facing claim must point at an emitted metric, a baseline/evidence
report, a safety gate, and a caveat. It is not another performance benchmark;
it is an overclaim guard that prevents prose from drifting beyond the current
synthetic/local evidence.

Usage:
    python3 benchmark/evaluate_claim_metric_map_v0.py
    python3 benchmark/evaluate_claim_metric_map_v0.py --json
    python3 benchmark/evaluate_claim_metric_map_v0.py --write-report
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
DEFAULT_CLAIM_MAP_PATH = REPO_ROOT / "benchmark" / "data" / "parker_claim_metric_map_v0.json"
DEFAULT_REPORTS_DIR = REPO_ROOT / "benchmark" / "reports"

_REQUIRED_FIELDS = {
    "claim_id",
    "capability",
    "proposal_claim",
    "grant_criterion",
    "metric_ids",
    "report_paths",
    "required_assertions",
    "baseline",
    "safety_gate",
    "caveat",
    "public_private_scope",
}
_OPERATORS: dict[str, Callable[[Any, Any], bool]] = {
    "eq": operator.eq,
    "gte": operator.ge,
    "lte": operator.le,
    "contains": lambda actual, expected: expected in actual,
}


@dataclass(frozen=True)
class RequiredAssertion:
    """One report-backed metric assertion for a proposal claim."""

    report_path: str
    json_path: str
    operator: str
    expected: Any

    @classmethod
    def from_dict(cls, row: dict[str, Any], claim_id: str) -> "RequiredAssertion":
        missing = {"report_path", "json_path", "operator", "expected"} - set(row)
        if missing:
            raise ValueError(f"claim {claim_id} assertion missing fields: {sorted(missing)}")
        if row["operator"] not in _OPERATORS:
            raise ValueError(f"claim {claim_id} uses unsupported operator {row['operator']!r}")
        return cls(
            report_path=str(row["report_path"]),
            json_path=str(row["json_path"]),
            operator=str(row["operator"]),
            expected=row["expected"],
        )


@dataclass(frozen=True)
class ClaimMetricRow:
    """One grant claim bound to current repo metric evidence."""

    claim_id: str
    capability: str
    proposal_claim: str
    grant_criterion: str
    metric_ids: list[str]
    report_paths: list[str]
    required_assertions: list[RequiredAssertion]
    baseline: str
    safety_gate: str
    caveat: str
    public_private_scope: str

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "ClaimMetricRow":
        missing = _REQUIRED_FIELDS - set(row)
        claim_id = str(row.get("claim_id", "<unknown>"))
        if missing:
            raise ValueError(f"claim {claim_id} missing fields: {sorted(missing)}")
        if not claim_id.strip():
            raise ValueError("claim_id must be non-empty")
        metric_ids = _non_empty_string_list(row["metric_ids"], claim_id, "metric_ids")
        report_paths = _non_empty_string_list(row["report_paths"], claim_id, "report_paths")
        caveat = str(row["caveat"]).strip()
        if not caveat:
            raise ValueError(f"claim {claim_id} needs a caveat")
        if "not real" not in caveat.lower() and "no private" not in caveat.lower():
            raise ValueError(f"claim {claim_id} caveat must explicitly limit real-world/private-data scope")
        assertions_raw = row["required_assertions"]
        if not isinstance(assertions_raw, list) or not assertions_raw:
            raise ValueError(f"claim {claim_id} requires at least one report-backed assertion")
        public_private_scope = str(row["public_private_scope"])
        if public_private_scope != "public_synthetic_only":
            raise ValueError(f"claim {claim_id} public_private_scope must be public_synthetic_only")
        return cls(
            claim_id=claim_id,
            capability=str(row["capability"]),
            proposal_claim=str(row["proposal_claim"]),
            grant_criterion=str(row["grant_criterion"]),
            metric_ids=metric_ids,
            report_paths=report_paths,
            required_assertions=[RequiredAssertion.from_dict(item, claim_id) for item in assertions_raw],
            baseline=str(row["baseline"]),
            safety_gate=str(row["safety_gate"]),
            caveat=caveat,
            public_private_scope=public_private_scope,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "capability": self.capability,
            "proposal_claim": self.proposal_claim,
            "grant_criterion": self.grant_criterion,
            "metric_ids": self.metric_ids,
            "report_paths": self.report_paths,
            "required_assertions": [assertion.__dict__ for assertion in self.required_assertions],
            "baseline": self.baseline,
            "safety_gate": self.safety_gate,
            "caveat": self.caveat,
            "public_private_scope": self.public_private_scope,
        }


@dataclass(frozen=True)
class AssertionResult:
    """Result of checking one metric assertion."""

    claim_id: str
    report_path: str
    json_path: str
    operator: str
    expected: Any
    actual: Any
    passed: bool
    message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "report_path": self.report_path,
            "json_path": self.json_path,
            "operator": self.operator,
            "expected": self.expected,
            "actual": self.actual,
            "passed": self.passed,
            "message": self.message,
        }


@dataclass(frozen=True)
class ClaimMetricEvalResult:
    """Aggregate overclaim-guard result for all mapped claims."""

    claims: list[ClaimMetricRow]
    assertion_results: list[AssertionResult]
    evidence_paths_checked: list[str]
    report_load_errors: list[dict[str, str]] = field(default_factory=list)

    @property
    def failing_assertions(self) -> list[AssertionResult]:
        return [result for result in self.assertion_results if not result.passed]

    @property
    def passing_claim_ids(self) -> set[str]:
        failing = {result.claim_id for result in self.failing_assertions}
        errored = {error["claim_id"] for error in self.report_load_errors}
        return {claim.claim_id for claim in self.claims} - failing - errored

    def as_dict(self) -> dict[str, Any]:
        total_claims = len(self.claims)
        metric_bound_claims = sum(1 for claim in self.claims if claim.metric_ids and claim.required_assertions)
        caveated_claims = sum(1 for claim in self.claims if claim.caveat)
        failing_claims = total_claims - len(self.passing_claim_ids)
        gate_passed = (
            total_claims > 0
            and metric_bound_claims == total_claims
            and caveated_claims == total_claims
            and failing_claims == 0
            and not self.report_load_errors
            and {claim.public_private_scope for claim in self.claims} == {"public_synthetic_only"}
        )
        return {
            "eval": "claim_metric_map_v0",
            "provenance": {
                "purpose": "proposal overclaim guard: each grant-facing claim must map to emitted metric evidence, a baseline, safety gate, and caveat",
                "private_data": "none",
                "fixture_policy": "public synthetic/local reports only",
                "model_or_api_dependency": "none",
            },
            "metrics": {
                "total_claims": total_claims,
                "metric_bound_claims": metric_bound_claims,
                "caveated_claims": caveated_claims,
                "passing_claims": len(self.passing_claim_ids),
                "failing_claims": failing_claims,
                "assertions_checked": len(self.assertion_results),
                "assertions_failed": len(self.failing_assertions),
            },
            "overclaim_gate": {
                "passed": gate_passed,
                "metric_bound_claims": metric_bound_claims,
                "caveated_claims": caveated_claims,
                "private_data": "none",
                "blocking_failures": failing_claims + len(self.report_load_errors),
            },
            "evidence_paths_checked": self.evidence_paths_checked,
            "claims": [claim.as_dict() for claim in self.claims],
            "assertion_results": [result.as_dict() for result in self.assertion_results],
            "failing_assertions": [result.as_dict() for result in self.failing_assertions],
            "report_load_errors": self.report_load_errors,
        }


def load_claims(path: Path = DEFAULT_CLAIM_MAP_PATH) -> list[ClaimMetricRow]:
    """Load and validate claim→metric rows."""

    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected a JSON array")
    claims = [ClaimMetricRow.from_dict(row) for row in raw]
    if not claims:
        raise ValueError("claim metric map is empty")
    seen: set[str] = set()
    for claim in claims:
        if claim.claim_id in seen:
            raise ValueError(f"duplicate claim_id: {claim.claim_id}")
        seen.add(claim.claim_id)
    return claims


def evaluate_claims(claims: list[ClaimMetricRow], repo_root: Path = REPO_ROOT) -> ClaimMetricEvalResult:
    """Evaluate all report-backed assertions in the claim map."""

    report_cache: dict[str, dict[str, Any]] = {}
    evidence_paths: set[str] = set()
    assertion_results: list[AssertionResult] = []
    report_load_errors: list[dict[str, str]] = []

    for claim in claims:
        for report_path in claim.report_paths:
            evidence_paths.add(report_path)
            try:
                report_cache[report_path] = _read_report_json(repo_root / report_path)
            except Exception as exc:  # noqa: BLE001 - surfaced as structured eval failure
                report_load_errors.append({"claim_id": claim.claim_id, "report_path": report_path, "error": str(exc)})

        for assertion in claim.required_assertions:
            evidence_paths.add(assertion.report_path)
            report = report_cache.get(assertion.report_path)
            if report is None:
                assertion_results.append(
                    AssertionResult(
                        claim_id=claim.claim_id,
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
                    claim_id=claim.claim_id,
                    report_path=assertion.report_path,
                    json_path=assertion.json_path,
                    operator=assertion.operator,
                    expected=assertion.expected,
                    actual=actual,
                    passed=passed,
                    message=message,
                )
            )

    return ClaimMetricEvalResult(
        claims=claims,
        assertion_results=assertion_results,
        evidence_paths_checked=sorted(evidence_paths),
        report_load_errors=report_load_errors,
    )


def _non_empty_string_list(value: Any, claim_id: str, field_name: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"claim {claim_id} {field_name} must be a non-empty list of strings")
    return [str(item) for item in value]


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


def format_summary(result: ClaimMetricEvalResult) -> str:
    payload = result.as_dict()
    metrics = payload["metrics"]
    gate = payload["overclaim_gate"]
    lines = [
        "Parker claim→metric map eval v0",
        "",
        f"Claims: {metrics['total_claims']} total, {metrics['passing_claims']} passing, {metrics['failing_claims']} failing",
        f"Assertions: {metrics['assertions_checked']} checked, {metrics['assertions_failed']} failed",
        f"Overclaim gate passed: {gate['passed']}",
        "Evidence paths checked:",
    ]
    lines.extend(f"  - {path}" for path in payload["evidence_paths_checked"])
    lines.extend(
        [
            "",
            "Caveat: this guard validates mapping to current synthetic/local evidence only; it is not real-world clinical or audio proof.",
        ]
    )
    if payload["failing_assertions"] or payload["report_load_errors"]:
        lines.append("")
        lines.append("Blocking failures:")
        for failure in payload["failing_assertions"]:
            lines.append(f"  - {failure['claim_id']} {failure['json_path']}: {failure['message']}")
        for error in payload["report_load_errors"]:
            lines.append(f"  - {error['claim_id']} {error['report_path']}: {error['error']}")
    return "\n".join(lines)


def format_markdown_report(result: ClaimMetricEvalResult, run_date: str) -> str:
    payload = result.as_dict()
    metrics = payload["metrics"]
    gate = payload["overclaim_gate"]
    lines = [
        "# Parker claim→metric map eval v0",
        "",
        f"- Date: {run_date}",
        "- Purpose: make each grant-facing claim traceable to emitted metric evidence, a baseline, a safety gate, and a caveat.",
        "- Provenance: public synthetic/local reports only; no private data; no model/API dependency.",
        "",
        "## Overclaim gate",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Total claims | {metrics['total_claims']} |",
        f"| Metric-bound claims | {metrics['metric_bound_claims']} |",
        f"| Caveated claims | {metrics['caveated_claims']} |",
        f"| Passing claims | {metrics['passing_claims']} |",
        f"| Failing claims | {metrics['failing_claims']} |",
        f"| Assertions checked | {metrics['assertions_checked']} |",
        f"| Assertions failed | {metrics['assertions_failed']} |",
        f"| Gate passed | {gate['passed']} |",
        "",
        "## Claim matrix",
        "",
        "| Claim | Capability | Criterion | Metrics | Baseline | Caveat |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for claim in result.claims:
        lines.append(
            "| "
            + " | ".join(
                [
                    claim.claim_id,
                    claim.capability,
                    claim.grant_criterion,
                    ", ".join(claim.metric_ids),
                    claim.baseline,
                    claim.caveat,
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
            f"- **{status}** `{assertion['claim_id']}` `{assertion['report_path']}` "
            f"`{assertion['json_path']}` {assertion['operator']} `{assertion['expected']}` "
            f"(actual `{assertion['actual']}`)"
        )
    lines.extend(
        [
            "",
            "## Scope caveat",
            "",
            "Passing this guard means the proposal's current claims are tied to current synthetic/local evidence. It does not establish clinical efficacy, real Parkinson's audio performance, emergency readiness, or private-data safety in production.",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(result: ClaimMetricEvalResult, reports_dir: Path = DEFAULT_REPORTS_DIR) -> list[Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    run_date = date.today().isoformat()
    markdown = format_markdown_report(result, run_date)
    payload = {"date": run_date, **result.as_dict()}
    written: list[Path] = []
    for stem in ("claim_metric_map_eval_latest", f"claim_metric_map_eval_{run_date}"):
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
    parser.add_argument("--claims", type=Path, default=DEFAULT_CLAIM_MAP_PATH)
    parser.add_argument("--json", action="store_true", help="Print JSON instead of text summary")
    parser.add_argument("--write-report", action="store_true", help="Write markdown+JSON reports to benchmark/reports")
    parser.add_argument("--reports-dir", type=Path, default=DEFAULT_REPORTS_DIR)
    args = parser.parse_args()

    result = evaluate_claims(load_claims(args.claims))
    if args.json:
        print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    else:
        print(format_summary(result))
    if args.write_report:
        for path in write_report(result, args.reports_dir):
            print(f"wrote {_display_path(path)}")


if __name__ == "__main__":
    main()
