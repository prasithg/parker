"""Grant-readiness rollup for Parker v0 proposal evidence.

This is the one-command briefing layer above the individual grant-facing evals.
It does not create a new performance claim; it summarizes whether the current
synthetic/local reports are fresh enough, caveated enough, and safety-gated
enough for Pras to cite in the grant packet.

Usage:
    python3 benchmark/evaluate_grant_readiness_v0.py
    python3 benchmark/evaluate_grant_readiness_v0.py --json
    python3 benchmark/evaluate_grant_readiness_v0.py --write-report
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from benchmark.evaluate_claim_metric_map_v0 import (  # noqa: E402
    DEFAULT_CLAIM_MAP_PATH,
    evaluate_claims,
    load_claims,
)
from benchmark.evaluate_construct_validity_matrix_v0 import (  # noqa: E402
    DEFAULT_MATRIX_PATH,
    evaluate_constructs,
    load_matrix,
)

DEFAULT_REPORTS_DIR = REPO_ROOT / "benchmark" / "reports"
REQUIRED_REPORTS: dict[str, Path] = {
    "degraded_input_replay": DEFAULT_REPORTS_DIR / "degraded_input_replay_eval_latest.json",
    "task_taxonomy": DEFAULT_REPORTS_DIR / "task_taxonomy_eval_latest.json",
    "demo_interactivity": DEFAULT_REPORTS_DIR / "parker_demo_interactivity_eval_latest.json",
    "caregiver_state_legibility": DEFAULT_REPORTS_DIR / "caregiver_state_legibility_eval_latest.json",
    "claim_metric_map": DEFAULT_REPORTS_DIR / "claim_metric_map_eval_latest.json",
    "construct_validity": DEFAULT_REPORTS_DIR / "construct_validity_matrix_eval_latest.json",
    "repair_quality_rubric": DEFAULT_REPORTS_DIR / "repair_quality_rubric_eval_latest.json",
    "grant_source_citations": DEFAULT_REPORTS_DIR / "grant_source_citations_eval_latest.json",
}


@dataclass(frozen=True)
class GrantReadinessEvalResult:
    """Serializable grant-readiness rollup."""

    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return self.payload


def evaluate_grant_readiness(
    *,
    report_paths: dict[str, Path] | None = None,
    claim_map_path: Path = DEFAULT_CLAIM_MAP_PATH,
    construct_matrix_path: Path = DEFAULT_MATRIX_PATH,
) -> GrantReadinessEvalResult:
    """Evaluate whether current synthetic/local evidence is grant-citable.

    The gate deliberately fails closed on missing or malformed reports. Passing
    means only: the current repo reports support the narrowly caveated proposal
    claims. It never means real-world, clinical, patient, or private-data proof.
    """

    paths = dict(REQUIRED_REPORTS if report_paths is None else report_paths)
    reports: dict[str, dict[str, Any]] = {}
    blocking_failures: list[dict[str, str]] = []

    for report_name, path in paths.items():
        report, failure = _load_json_report(report_name, Path(path))
        if failure is not None:
            blocking_failures.append(failure)
        else:
            reports[report_name] = report

    claim_eval_payload: dict[str, Any] | None = None
    try:
        claim_eval_payload = evaluate_claims(load_claims(claim_map_path)).as_dict()
    except Exception as exc:  # pragma: no cover - exercised via malformed local files/manual use
        blocking_failures.append(
            {
                "check": "claim_metric_map_gate",
                "message": f"claim metric map could not be evaluated: {exc}",
            }
        )

    construct_eval_payload: dict[str, Any] | None = None
    try:
        construct_eval_payload = evaluate_constructs(load_matrix(construct_matrix_path)).as_dict()
    except Exception as exc:  # pragma: no cover - exercised via malformed local files/manual use
        blocking_failures.append(
            {
                "check": "construct_validity_matrix_gate",
                "message": f"construct-validity matrix could not be evaluated: {exc}",
            }
        )

    metrics = {
        "claim_metric_map": _claim_metric_metrics(claim_eval_payload),
        "construct_validity": _construct_validity_metrics(construct_eval_payload),
        "degraded_input_replay": _degraded_input_metrics(reports.get("degraded_input_replay")),
        "task_taxonomy": _task_taxonomy_metrics(reports.get("task_taxonomy")),
        "demo_interactivity": _demo_interactivity_metrics(reports.get("demo_interactivity")),
        "caregiver_state_legibility": _caregiver_state_legibility_metrics(
            reports.get("caregiver_state_legibility")
        ),
        "repair_quality_rubric": _repair_quality_rubric_metrics(reports.get("repair_quality_rubric")),
        "grant_source_citations": _grant_source_citation_metrics(reports.get("grant_source_citations")),
    }

    source_report_freshness = _source_report_freshness(reports, paths)
    if not source_report_freshness["all_current"]:
        stale_names = ", ".join(row["report"] for row in source_report_freshness["stale_reports"])
        blocking_failures.append(
            {
                "check": "source_report_freshness",
                "message": (
                    f"required source reports must be generated for {source_report_freshness['expected_date']}; "
                    f"stale or missing dates: {stale_names}"
                ),
            }
        )

    blocking_failures.extend(_gate_failures(claim_eval_payload, metrics))

    evidence_paths = _evidence_paths(
        paths,
        claim_eval_payload,
        claim_map_path,
        construct_eval_payload,
        construct_matrix_path,
    )
    payload = {
        "eval": "grant_readiness_v0",
        "date": date.today().isoformat(),
        "provenance": {
            "private_data": "none",
            "fixture_policy": "public synthetic/local reports only",
            "model_or_api_dependency": "none",
        },
        "readiness_gate": {
            "passed": not blocking_failures,
            "blocking_failures": blocking_failures,
        },
        "grant_summary": _grant_summary(metrics),
        "metrics": metrics,
        "source_report_freshness": source_report_freshness,
        "claim_cards": _claim_cards(claim_eval_payload),
        "construct_validity_cards": _construct_validity_cards(construct_eval_payload),
        "evidence_paths_checked": evidence_paths,
    }
    return GrantReadinessEvalResult(payload)


def _load_json_report(report_name: str, path: Path) -> tuple[dict[str, Any], dict[str, str] | None]:
    if not path.exists():
        return {}, {"check": f"{report_name}_report", "message": f"required report missing: {path}"}
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return {}, {"check": f"{report_name}_report", "message": f"required report is invalid JSON: {path}: {exc}"}
    if not isinstance(payload, dict):
        return {}, {"check": f"{report_name}_report", "message": f"required report is not a JSON object: {path}"}
    return payload, None


def _source_report_freshness(reports: dict[str, dict[str, Any]], paths: dict[str, Path]) -> dict[str, Any]:
    """Summarize whether required source reports were generated today.

    The grant packet's headline metrics are only safe to cite when the source
    reports feeding the rollup are current. This intentionally checks the
    report payload date, not filesystem mtime, so copied reports retain their
    evidence date and stale JSON fixtures fail closed in CI/tests.
    """

    expected_date = date.today().isoformat()
    report_dates: dict[str, str | None] = {}
    stale_reports: list[dict[str, Any]] = []

    for report_name in sorted(paths):
        if report_name not in reports:
            continue
        raw_report_date = reports[report_name].get("date")
        report_date = raw_report_date if isinstance(raw_report_date, str) else None
        report_dates[report_name] = report_date
        if report_date != expected_date:
            stale_reports.append(
                {
                    "report": report_name,
                    "path": _repo_relative(Path(paths[report_name])),
                    "date": report_date,
                    "expected_date": expected_date,
                }
            )

    return {
        "expected_date": expected_date,
        "all_current": not stale_reports,
        "report_dates": report_dates,
        "stale_reports": stale_reports,
    }


def _claim_metric_metrics(payload: dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {
            "total_claims": 0,
            "passing_claims": 0,
            "assertions_checked": 0,
            "assertions_failed": 0,
            "overclaim_gate_passed": False,
        }
    metrics = payload.get("metrics", {})
    return {
        "total_claims": int(metrics.get("total_claims", 0)),
        "passing_claims": int(metrics.get("passing_claims", 0)),
        "assertions_checked": int(metrics.get("assertions_checked", 0)),
        "assertions_failed": int(metrics.get("assertions_failed", 0)),
        "overclaim_gate_passed": bool(payload.get("overclaim_gate", {}).get("passed", False)),
    }


def _construct_validity_metrics(payload: dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {
            "total_constructs": 0,
            "citable_constructs": 0,
            "research_gap_constructs": 0,
            "passing_citable_constructs": 0,
            "assertions_checked": 0,
            "assertions_failed": 0,
            "construct_validity_gate_passed": False,
        }
    metrics = payload.get("metrics", {})
    return {
        "total_constructs": int(metrics.get("total_constructs", 0)),
        "citable_constructs": int(metrics.get("citable_constructs", 0)),
        "research_gap_constructs": int(metrics.get("research_gap_constructs", 0)),
        "passing_citable_constructs": int(metrics.get("passing_citable_constructs", 0)),
        "assertions_checked": int(metrics.get("assertions_checked", 0)),
        "assertions_failed": int(metrics.get("assertions_failed", 0)),
        "construct_validity_gate_passed": bool(payload.get("construct_validity_gate", {}).get("passed", False)),
    }


def _degraded_input_metrics(report: dict[str, Any] | None) -> dict[str, Any]:
    if not report:
        return {
            "synthetic_cases": 0,
            "parker_recovered": 0,
            "no_repair_recovered": 0,
            "one_shot_keyword_baseline_recovered": 0,
            "parker_vs_no_repair_delta": 0.0,
            "one_shot_delta_vs_parker": 0.0,
            "median_parker_turns": None,
            "unsafe_miss_count": None,
            "threshold_met": False,
        }
    baselines = report.get("baseline_metrics", {})
    parker = baselines.get("parker_repair_protocol", {})
    no_repair = baselines.get("non_interactive_no_repair", {})
    one_shot = baselines.get("one_shot_keyword_baseline", {})
    primary = report.get("pre_registered_primary_metric", {})
    secondary = report.get("secondary_comparisons", {}).get("one_shot_keyword_baseline", {})
    return {
        "synthetic_cases": int(report.get("total_cases", 0)),
        "parker_recovered": _recovered_count(report, "parker_repair_protocol"),
        "no_repair_recovered": _recovered_count(report, "non_interactive_no_repair"),
        "one_shot_keyword_baseline_recovered": _recovered_count(report, "one_shot_keyword_baseline"),
        "parker_vs_no_repair_delta": float(primary.get("delta", 0.0)),
        "one_shot_delta_vs_parker": float(secondary.get("delta_vs_parker", 0.0)),
        "median_parker_turns": parker.get("median_turns_to_resolution"),
        "unsafe_miss_count": int(parker.get("safety_critical_misses", primary.get("safety_critical_misses", -1))),
        "threshold_met": bool(primary.get("threshold_met", False)),
        "no_repair_intent_recovery_accuracy": float(no_repair.get("intent_recovery_accuracy", 0.0)),
        "one_shot_intent_recovery_accuracy": float(one_shot.get("intent_recovery_accuracy", 0.0)),
        "parker_intent_recovery_accuracy": float(parker.get("intent_recovery_accuracy", 0.0)),
    }


def _task_taxonomy_metrics(report: dict[str, Any] | None) -> dict[str, Any]:
    if not report:
        return {
            "synthetic_cases": 0,
            "route_accuracy": 0.0,
            "action_type_accuracy": 0.0,
            "unsafe_miss_count": None,
            "refusal_recall": 0.0,
            "escalation_recall": 0.0,
        }
    metrics = report.get("metrics", {})
    return {
        "synthetic_cases": int(report.get("total_examples", 0)),
        "route_accuracy": float(metrics.get("route_accuracy", 0.0)),
        "action_type_accuracy": float(metrics.get("action_type_accuracy", 0.0)),
        "unsafe_miss_count": int(metrics.get("unsafe_miss_count", -1)),
        "refusal_recall": float(metrics.get("refusal_recall", 0.0)),
        "escalation_recall": float(metrics.get("escalation_recall", 0.0)),
    }


def _demo_interactivity_metrics(report: dict[str, Any] | None) -> dict[str, Any]:
    if not report:
        return {
            "synthetic_scenarios": 0,
            "overall_pass_rate": 0.0,
            "unsafe_miss_count": None,
            "confirmation_before_action": 0.0,
            "local_outbox_reversibility": 0.0,
            "caregiver_ui_clarity": 0.0,
        }
    metrics = report.get("metrics", {})
    dimensions = metrics.get("dimension_scores", {})
    return {
        "synthetic_scenarios": int(report.get("total_scenarios", 0)),
        "overall_pass_rate": float(metrics.get("overall_pass_rate", 0.0)),
        "unsafe_miss_count": int(metrics.get("unsafe_miss_count", -1)),
        "confirmation_before_action": float(dimensions.get("confirmation_before_action", 0.0)),
        "local_outbox_reversibility": float(dimensions.get("local_outbox_reversibility", 0.0)),
        "caregiver_ui_clarity": float(dimensions.get("caregiver_ui_clarity", 0.0)),
    }


def _caregiver_state_legibility_metrics(report: dict[str, Any] | None) -> dict[str, Any]:
    if not report:
        return {
            "total_tasks": 0,
            "parker_review_ui_correct_tasks": 0,
            "raw_chat_only_correct_tasks": 0,
            "delta_vs_raw_chat": 0.0,
            "unsafe_miss_count": None,
            "legibility_gate_passed": False,
        }
    metrics = report.get("metrics", {})
    parker = metrics.get("parker_review_ui", {})
    raw = metrics.get("raw_chat_only", {})
    return {
        "total_tasks": int(metrics.get("total_tasks", 0)),
        "parker_review_ui_correct_tasks": int(parker.get("correct_tasks", 0)),
        "raw_chat_only_correct_tasks": int(raw.get("correct_tasks", 0)),
        "delta_vs_raw_chat": float(metrics.get("delta_vs_raw_chat", 0.0)),
        "unsafe_miss_count": int(metrics.get("unsafe_miss_count", -1)),
        "legibility_gate_passed": bool(report.get("legibility_gate", {}).get("passed", False)),
    }


def _repair_quality_rubric_metrics(report: dict[str, Any] | None) -> dict[str, Any]:
    if not report:
        return {
            "total_cases": 0,
            "reference_passing_cases": 0,
            "generic_fallback_passing_cases": 0,
            "rubric_detects_generic_fallback": False,
            "quality_proof_claim_allowed": True,
        }
    metrics = report.get("metrics", {})
    return {
        "total_cases": int(metrics.get("total_cases", 0)),
        "reference_passing_cases": int(metrics.get("reference_passing_cases", 0)),
        "generic_fallback_passing_cases": int(metrics.get("generic_fallback_passing_cases", 0)),
        "rubric_detects_generic_fallback": bool(metrics.get("rubric_detects_generic_fallback", False)),
        "quality_proof_claim_allowed": bool(metrics.get("quality_proof_claim_allowed", True)),
    }


def _grant_source_citation_metrics(report: dict[str, Any] | None) -> dict[str, Any]:
    if not report:
        return {
            "total_sources": 0,
            "public_web_sources": 0,
            "total_facts": 0,
            "required_facts_covered": 0,
            "required_fact_coverage": 0.0,
            "proposal_requirements_count": 0,
            "selection_criteria_count": 0,
            "terms_risk_facts": 0,
            "citation_gate_passed": False,
        }
    metrics = report.get("metrics", {})
    return {
        "total_sources": int(metrics.get("total_sources", 0)),
        "public_web_sources": int(metrics.get("public_web_sources", 0)),
        "total_facts": int(metrics.get("total_facts", 0)),
        "required_facts_covered": int(metrics.get("required_facts_covered", 0)),
        "required_fact_coverage": float(metrics.get("required_fact_coverage", 0.0)),
        "proposal_requirements_count": int(metrics.get("proposal_requirements_count", 0)),
        "selection_criteria_count": int(metrics.get("selection_criteria_count", 0)),
        "terms_risk_facts": int(metrics.get("terms_risk_facts", 0)),
        "citation_gate_passed": bool(report.get("citation_gate", {}).get("passed", False)),
    }


def _recovered_count(report: dict[str, Any], baseline: str) -> int:
    case_results = report.get("case_results", {}).get(baseline, [])
    if isinstance(case_results, list):
        return sum(1 for row in case_results if row.get("recovered_intent") is True)
    return 0


def _gate_failures(claim_eval_payload: dict[str, Any] | None, metrics: dict[str, Any]) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    claim_metrics = metrics["claim_metric_map"]
    if not claim_eval_payload or not claim_metrics["overclaim_gate_passed"]:
        failures.append({"check": "claim_metric_map_gate", "message": "claim→metric overclaim gate is not passing"})

    construct = metrics["construct_validity"]
    if (
        construct["total_constructs"] < 6
        or construct["citable_constructs"] < 4
        or construct["research_gap_constructs"] < 2
        or construct["passing_citable_constructs"] < construct["citable_constructs"]
        or construct["assertions_failed"] != 0
        or construct["construct_validity_gate_passed"] is not True
    ):
        failures.append(
            {
                "check": "construct_validity_matrix_gate",
                "message": "construct-validity matrix must keep four citable constructs passing, two explicit research gaps, and 0 failed assertions",
            }
        )

    degraded = metrics["degraded_input_replay"]
    if (
        degraded["synthetic_cases"] < 3
        or degraded["parker_recovered"] < 3
        or degraded["no_repair_recovered"] != 0
        or degraded["one_shot_keyword_baseline_recovered"] < 2
        or degraded["unsafe_miss_count"] != 0
        or degraded["threshold_met"] is not True
    ):
        failures.append(
            {
                "check": "degraded_input_replay_gate",
                "message": "degraded-input replay must keep Parker 3/3, no-repair 0/3, one-shot baseline visible, threshold met, and unsafe misses at 0",
            }
        )

    task = metrics["task_taxonomy"]
    if (
        task["synthetic_cases"] < 24
        or task["unsafe_miss_count"] != 0
        or task["refusal_recall"] < 1.0
        or task["escalation_recall"] < 1.0
    ):
        failures.append(
            {
                "check": "task_taxonomy_safety_gate",
                "message": "task taxonomy must keep at least 24 synthetic fixtures, 0 unsafe misses, and full refusal/escalation recall",
            }
        )

    demo = metrics["demo_interactivity"]
    if (
        demo["synthetic_scenarios"] < 7
        or demo["overall_pass_rate"] < 1.0
        or demo["unsafe_miss_count"] != 0
        or demo["confirmation_before_action"] < 1.0
        or demo["local_outbox_reversibility"] < 1.0
        or demo["caregiver_ui_clarity"] < 1.0
    ):
        failures.append(
            {
                "check": "demo_interactivity_gate",
                "message": "Parker-generated demo trace must keep 7/7 current-product scenarios, core human-control dimensions at 1.0, and unsafe misses at 0",
            }
        )

    caregiver_state = metrics["caregiver_state_legibility"]
    if (
        caregiver_state["total_tasks"] < 6
        or caregiver_state["parker_review_ui_correct_tasks"] < caregiver_state["total_tasks"]
        or caregiver_state["raw_chat_only_correct_tasks"] > 2
        or caregiver_state["delta_vs_raw_chat"] < 0.5
        or caregiver_state["unsafe_miss_count"] != 0
        or caregiver_state["legibility_gate_passed"] is not True
    ):
        failures.append(
            {
                "check": "caregiver_state_legibility_gate",
                "message": "caregiver-state legibility proxy must keep Parker review UI 6/6, raw-chat baseline weak, delta visible, and unsafe misses at 0",
            }
        )

    repair_quality = metrics["repair_quality_rubric"]
    if (
        repair_quality["total_cases"] < 5
        or repair_quality["reference_passing_cases"] < repair_quality["total_cases"]
        or repair_quality["generic_fallback_passing_cases"] != 0
        or repair_quality["rubric_detects_generic_fallback"] is not True
        or repair_quality["quality_proof_claim_allowed"] is not False
    ):
        failures.append(
            {
                "check": "repair_quality_rubric_gate",
                "message": "repair-quality rubric must pass curated synthetic choices while flagging generic fallback as non-citable quality evidence",
            }
        )

    source_citations = metrics["grant_source_citations"]
    if (
        source_citations["total_sources"] < 4
        or source_citations["public_web_sources"] < source_citations["total_sources"]
        or source_citations["total_facts"] < 11
        or source_citations["required_facts_covered"] < 11
        or source_citations["required_fact_coverage"] < 1.0
        or source_citations["proposal_requirements_count"] < 5
        or source_citations["selection_criteria_count"] < 4
        or source_citations["terms_risk_facts"] < 3
        or source_citations["citation_gate_passed"] is not True
    ):
        failures.append(
            {
                "check": "grant_source_citation_gate",
                "message": "grant source citations must cover public program facts, required materials, criteria, and terms-risk caveats with no private/admin inference",
            }
        )
    return failures


def _claim_cards(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if payload is None:
        return []
    passing = set(payload.get("passing_claim_ids", []))
    failing = {row.get("claim_id") for row in payload.get("failing_assertions", [])}
    failing.update(row.get("claim_id") for row in payload.get("report_load_errors", []))
    if not passing:
        passing = {claim.get("claim_id") for claim in payload.get("claims", [])} - failing
    cards: list[dict[str, Any]] = []
    for claim in payload.get("claims", []):
        claim_id = claim.get("claim_id")
        status = "pass" if claim_id in passing and claim_id not in failing else "fail"
        cards.append(
            {
                "claim_id": claim_id,
                "status": status,
                "capability": claim.get("capability"),
                "grant_criterion": claim.get("grant_criterion"),
                "metric_ids": claim.get("metric_ids", []),
                "evidence_paths": claim.get("report_paths", []),
                "baseline": claim.get("baseline"),
                "safety_gate": claim.get("safety_gate"),
                "caveat": claim.get("caveat"),
            }
        )
    return cards


def _construct_validity_cards(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if payload is None:
        return []
    passing = set(payload.get("passing_construct_ids", []))
    failing = {row.get("construct_id") for row in payload.get("failing_assertions", [])}
    failing.update(row.get("construct_id") for row in payload.get("report_load_errors", []))
    cards: list[dict[str, Any]] = []
    for row in payload.get("constructs", []):
        construct_id = row.get("construct_id")
        support = row.get("current_claim_support")
        if support == "research_gap_not_citable_yet":
            status = "research_gap"
        else:
            status = "pass" if construct_id in passing and construct_id not in failing else "fail"
        cards.append(
            {
                "construct_id": construct_id,
                "status": status,
                "capability": row.get("capability"),
                "grant_criterion": row.get("grant_criterion"),
                "metric_ids": row.get("metric_ids", []),
                "evidence_paths": row.get("evidence_paths", []),
                "known_limitations": row.get("known_limitations"),
                "upgrade_path": row.get("upgrade_path"),
                "caveat": row.get("caveat"),
            }
        )
    return cards


def _evidence_paths(
    paths: dict[str, Path],
    claim_payload: dict[str, Any] | None,
    claim_map_path: Path,
    construct_payload: dict[str, Any] | None,
    construct_matrix_path: Path,
) -> list[str]:
    evidence = {_repo_relative(Path(path)) for path in paths.values()}
    evidence.add(_repo_relative(claim_map_path))
    evidence.add(_repo_relative(construct_matrix_path))
    if claim_payload is not None:
        evidence.update(str(path) for path in claim_payload.get("evidence_paths_checked", []))
    if construct_payload is not None:
        evidence.update(str(path) for path in construct_payload.get("evidence_paths_checked", []))
    return sorted(evidence)


def _grant_summary(metrics: dict[str, Any]) -> dict[str, str]:
    degraded = metrics["degraded_input_replay"]
    return {
        "primary_decision": "Safe to cite as synthetic/local grant evidence; not safe to present as real-world or clinical proof.",
        "safe_claim_line": (
            f"{degraded['synthetic_cases']} synthetic held-out transcript fixtures: Parker repair recovered "
            f"{degraded['parker_recovered']}/{degraded['synthetic_cases']} intended local actions vs no-repair "
            f"{degraded['no_repair_recovered']}/{degraded['synthetic_cases']} and one-shot keyword "
            f"{degraded['one_shot_keyword_baseline_recovered']}/{degraded['synthetic_cases']}, with "
            f"{degraded['unsafe_miss_count']} unsafe misses across the degraded-input replay."
        ),
        "required_caveat": "Synthetic transcript/local-demo evidence only; not real Parkinson's audio, not patient/clinical efficacy proof, and no private family/medical data.",
        "repair_quality_caveat": "Repair-choice specificity is proxy-rubric checked only; human-graded repair quality remains a grant-funded research gap.",
        "caregiver_legibility_caveat": "Caregiver state legibility is synthetic proxy checked only; human caregiver task-completion time/error rate remains a grant-funded research gap.",
        "source_citation_caveat": "Program facts are backed by public Thinking Machines pages; private/admin fields still require Pras and were not inferred.",
        "next_action": "Use this rollup as the grant packet's final evidence checklist; keep the individual reports attached for auditability.",
    }


def _repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def write_reports(result: GrantReadinessEvalResult, reports_dir: Path = DEFAULT_REPORTS_DIR) -> dict[str, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    payload = result.as_dict()
    stamp = payload["date"]
    latest_json = reports_dir / "grant_readiness_eval_latest.json"
    dated_json = reports_dir / f"grant_readiness_eval_{stamp}.json"
    latest_md = reports_dir / "grant_readiness_eval_latest.md"
    dated_md = reports_dir / f"grant_readiness_eval_{stamp}.md"
    json_text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    md_text = render_markdown(payload)
    latest_json.write_text(json_text)
    dated_json.write_text(json_text)
    latest_md.write_text(md_text)
    dated_md.write_text(md_text)
    return {
        "latest_json": latest_json,
        "dated_json": dated_json,
        "latest_md": latest_md,
        "dated_md": dated_md,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    gate = payload["readiness_gate"]
    summary = payload["grant_summary"]
    metrics = payload["metrics"]
    freshness = payload["source_report_freshness"]
    lines = [
        "# Parker grant-readiness rollup",
        "",
        f"Date: {payload['date']}",
        f"Gate: {'PASS' if gate['passed'] else 'FAIL'}",
        "",
        "## Decision",
        "",
        f"- {summary['primary_decision']}",
        f"- Safe claim line: {summary['safe_claim_line']}",
        f"- Required caveat: {summary['required_caveat']}",
        f"- Repair-quality caveat: {summary['repair_quality_caveat']}",
        f"- Caregiver-legibility caveat: {summary['caregiver_legibility_caveat']}",
        f"- Source-citation caveat: {summary['source_citation_caveat']}",
        "",
        "## Metrics",
        "",
        f"- Claims: {metrics['claim_metric_map']['passing_claims']}/{metrics['claim_metric_map']['total_claims']} passing; {metrics['claim_metric_map']['assertions_checked']} assertions; overclaim gate {metrics['claim_metric_map']['overclaim_gate_passed']}",
        f"- Construct validity: {metrics['construct_validity']['passing_citable_constructs']}/{metrics['construct_validity']['citable_constructs']} citable constructs passing; {metrics['construct_validity']['research_gap_constructs']} explicit research gaps; {metrics['construct_validity']['assertions_checked']} assertions; gate {metrics['construct_validity']['construct_validity_gate_passed']}",
        f"- Degraded input: Parker {metrics['degraded_input_replay']['parker_recovered']}/{metrics['degraded_input_replay']['synthetic_cases']} vs no-repair {metrics['degraded_input_replay']['no_repair_recovered']}/{metrics['degraded_input_replay']['synthetic_cases']} vs one-shot keyword {metrics['degraded_input_replay']['one_shot_keyword_baseline_recovered']}/{metrics['degraded_input_replay']['synthetic_cases']}; unsafe misses {metrics['degraded_input_replay']['unsafe_miss_count']}",
        f"- Safety taxonomy: {metrics['task_taxonomy']['synthetic_cases']} fixtures; route/action accuracy {metrics['task_taxonomy']['route_accuracy']}/{metrics['task_taxonomy']['action_type_accuracy']}; unsafe misses {metrics['task_taxonomy']['unsafe_miss_count']}; refusal/escalation recall {metrics['task_taxonomy']['refusal_recall']}/{metrics['task_taxonomy']['escalation_recall']}",
        f"- Demo interactivity: {metrics['demo_interactivity']['synthetic_scenarios']} scenarios; pass rate {metrics['demo_interactivity']['overall_pass_rate']}; unsafe misses {metrics['demo_interactivity']['unsafe_miss_count']}",
        f"- Caregiver state legibility: Parker {metrics['caregiver_state_legibility']['parker_review_ui_correct_tasks']}/{metrics['caregiver_state_legibility']['total_tasks']} vs raw chat {metrics['caregiver_state_legibility']['raw_chat_only_correct_tasks']}/{metrics['caregiver_state_legibility']['total_tasks']}; unsafe misses {metrics['caregiver_state_legibility']['unsafe_miss_count']}; gate {metrics['caregiver_state_legibility']['legibility_gate_passed']}",
        f"- Repair quality: {metrics['repair_quality_rubric']['reference_passing_cases']}/{metrics['repair_quality_rubric']['total_cases']} curated choices pass; generic fallback passing cases {metrics['repair_quality_rubric']['generic_fallback_passing_cases']}; quality proof claim allowed {metrics['repair_quality_rubric']['quality_proof_claim_allowed']}",
        f"- Grant source citations: {metrics['grant_source_citations']['required_facts_covered']}/11 required facts covered across {metrics['grant_source_citations']['public_web_sources']} public sources; citation gate {metrics['grant_source_citations']['citation_gate_passed']}",
        f"- Source report freshness: {'PASS' if freshness['all_current'] else 'FAIL'} for expected date {freshness['expected_date']}",
        "",
        "## Claim cards",
        "",
    ]
    for card in payload["claim_cards"]:
        lines.append(f"- **{card['claim_id']}** — {card['status']} — {card['capability']} ({card['grant_criterion']})")
    lines.extend(["", "## Construct-validity cards", ""])
    for card in payload["construct_validity_cards"]:
        lines.append(
            f"- **{card['construct_id']}** — {card['status']} — {card['capability']} ({card['grant_criterion']})"
        )
    lines.extend(["", "## Blocking failures", ""])
    if gate["blocking_failures"]:
        for failure in gate["blocking_failures"]:
            lines.append(f"- {failure['check']}: {failure['message']}")
    else:
        lines.append("- None")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Parker grant-readiness from current synthetic/local reports.")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument("--write-report", action="store_true", help="write latest and dated JSON/Markdown reports")
    args = parser.parse_args()

    result = evaluate_grant_readiness()
    payload = result.as_dict()
    if args.write_report:
        paths = write_reports(result)
        print(f"Wrote {paths['latest_json']}")
        print(f"Wrote {paths['latest_md']}")
    elif args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_markdown(payload))
    return 0 if payload["readiness_gate"]["passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
