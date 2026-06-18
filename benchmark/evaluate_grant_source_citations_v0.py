"""Public-source citation guard for Parker's grant packet.

This evaluator hardens the non-code side of the Thinking Machines application:
program facts should come from public Thinking Machines pages and should stay
separate from private/admin fields. It does not fetch the web in CI; the source
matrix records the public excerpts verified during the Night4 workbench.

Usage:
    python3 benchmark/evaluate_grant_source_citations_v0.py
    python3 benchmark/evaluate_grant_source_citations_v0.py --json
    python3 benchmark/evaluate_grant_source_citations_v0.py --write-report
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
DEFAULT_CITATION_PATH = REPO_ROOT / "benchmark" / "data" / "grant_source_citations_v0.json"
DEFAULT_REPORTS_DIR = REPO_ROOT / "benchmark" / "reports"

REQUIRED_FACT_IDS = {
    "award_amount_and_tinker_credits",
    "research_directions",
    "application_required_materials",
    "submission_deadline",
    "selection_criteria",
    "funding_and_project_timeline",
    "proposal_may_be_public",
    "work_product_open_license",
    "no_company_ownership_of_work_product",
    "interaction_models_thesis",
    "interaction_models_micro_turns",
}
PLACEHOLDERS = {"", "none", "n/a", "na", "tbd", "todo", "placeholder"}


@dataclass(frozen=True)
class FactCitation:
    """One public-source-backed fact used by the grant packet."""

    fact_id: str
    claim: str
    verbatim_excerpt: str
    proposal_usage: str

    @classmethod
    def from_dict(cls, source_id: str, raw: dict[str, Any]) -> "FactCitation":
        for field in ["fact_id", "claim", "verbatim_excerpt", "proposal_usage"]:
            _require_non_placeholder(raw.get(field), f"{source_id}.{field}")
        return cls(
            fact_id=str(raw["fact_id"]),
            claim=str(raw["claim"]),
            verbatim_excerpt=str(raw["verbatim_excerpt"]),
            proposal_usage=str(raw["proposal_usage"]),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "claim": self.claim,
            "verbatim_excerpt": self.verbatim_excerpt,
            "proposal_usage": self.proposal_usage,
        }


@dataclass(frozen=True)
class SourceCitation:
    """A public source and the facts Parker cites from it."""

    source_id: str
    title: str
    url: str
    source_type: str
    checked_at: str
    facts: tuple[FactCitation, ...]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SourceCitation":
        for field in ["source_id", "title", "url", "source_type", "checked_at"]:
            _require_non_placeholder(raw.get(field), field)
        source_id = str(raw["source_id"])
        url = str(raw["url"])
        if not url.startswith("https://thinkingmachines.ai/"):
            raise ValueError(f"{source_id}.url must be a public Thinking Machines URL")
        source_type = str(raw["source_type"])
        if source_type != "public_web":
            raise ValueError(f"{source_id}.source_type must be public_web")
        _parse_iso_date(str(raw["checked_at"]), f"{source_id}.checked_at")
        raw_facts = raw.get("facts")
        if not isinstance(raw_facts, list) or not raw_facts:
            raise ValueError(f"{source_id}.facts must be a non-empty list")
        facts = tuple(FactCitation.from_dict(source_id, fact) for fact in raw_facts)
        fact_ids = [fact.fact_id for fact in facts]
        if len(set(fact_ids)) != len(fact_ids):
            raise ValueError(f"{source_id}.facts contains duplicate fact_id values")
        return cls(
            source_id=source_id,
            title=str(raw["title"]),
            url=url,
            source_type=source_type,
            checked_at=str(raw["checked_at"]),
            facts=facts,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "title": self.title,
            "url": self.url,
            "source_type": self.source_type,
            "checked_at": self.checked_at,
            "facts": [fact.as_dict() for fact in self.facts],
        }


@dataclass(frozen=True)
class GrantSourceCitationEvalResult:
    """Serializable citation guard result."""

    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return self.payload


def load_citations(path: Path = DEFAULT_CITATION_PATH) -> list[SourceCitation]:
    """Load and validate the public-source citation matrix."""

    raw = json.loads(path.read_text())
    if not isinstance(raw, list) or not raw:
        raise ValueError("grant source citations must be a non-empty list")
    sources = [SourceCitation.from_dict(source) for source in raw]
    source_ids = [source.source_id for source in sources]
    if len(set(source_ids)) != len(source_ids):
        raise ValueError("grant source citations contain duplicate source_id values")
    fact_ids = [fact.fact_id for source in sources for fact in source.facts]
    if len(set(fact_ids)) != len(fact_ids):
        raise ValueError("grant source citations contain duplicate fact_id values")
    return sources


def evaluate_citations(sources: list[SourceCitation]) -> GrantSourceCitationEvalResult:
    """Evaluate whether the grant packet has enough public-source grounding."""

    facts = {fact.fact_id: fact for source in sources for fact in source.facts}
    missing_required = sorted(REQUIRED_FACT_IDS - set(facts))
    private_source_ids = [source.source_id for source in sources if source.source_type != "public_web"]
    blocking_failures: list[dict[str, str]] = []
    if missing_required:
        blocking_failures.append(
            {
                "check": "required_public_facts",
                "message": "missing required public-source facts: " + ", ".join(missing_required),
            }
        )
    if private_source_ids:
        blocking_failures.append(
            {
                "check": "public_source_policy",
                "message": "all grant source citations must be public_web: " + ", ".join(private_source_ids),
            }
        )

    metrics = {
        "total_sources": len(sources),
        "public_web_sources": sum(1 for source in sources if source.source_type == "public_web"),
        "total_facts": len(facts),
        "required_facts": len(REQUIRED_FACT_IDS),
        "required_facts_covered": len(REQUIRED_FACT_IDS & set(facts)),
        "required_fact_coverage": round(len(REQUIRED_FACT_IDS & set(facts)) / len(REQUIRED_FACT_IDS), 4),
        "proposal_requirements_count": _count_required_materials(facts.get("application_required_materials")),
        "selection_criteria_count": _count_selection_criteria(facts.get("selection_criteria")),
        "terms_risk_facts": sum(
            1
            for fact_id in [
                "proposal_may_be_public",
                "work_product_open_license",
                "no_company_ownership_of_work_product",
            ]
            if fact_id in facts
        ),
    }
    proposal_ready_facts = {
        "award": "$100,000 cash grant plus $25,000 in Tinker credits",
        "deadline": "June 19, 2026 at 11:59 PM PDT",
        "selection_criteria": "relevance; feasibility; construct validity; simplicity and generality",
        "required_materials": "1–3 page summary; 1 page budget; PI/contributor list with CVs; location/org/tax/admin details if applicable; primary contact email",
        "confidentiality_warning": "The proposal may become public; do not include private family, medical, tax, banking, address, credential, or third-party confidential details.",
        "open_license_note": "Published/disclosed work product should be license-ready for CC BY 4.0 or another approved open source license.",
        "realtime_gap_note": "Interaction-model micro-turn/audio-video-text claims are grant-funded research gaps, not current Parker proof.",
    }
    payload = {
        "eval": "grant_source_citations_v0",
        "date": date.today().isoformat(),
        "provenance": {
            "source_policy": "public Thinking Machines pages only",
            "private_data": "none",
            "model_or_api_dependency": "none",
        },
        "citation_gate": {
            "passed": not blocking_failures,
            "blocking_failures": blocking_failures,
        },
        "metrics": metrics,
        "proposal_ready_facts": proposal_ready_facts,
        "required_fact_ids": sorted(REQUIRED_FACT_IDS),
        "covered_required_fact_ids": sorted(REQUIRED_FACT_IDS & set(facts)),
        "sources": [source.as_dict() for source in sources],
    }
    return GrantSourceCitationEvalResult(payload)


def _count_required_materials(fact: FactCitation | None) -> int:
    if fact is None:
        return 0
    text = fact.claim.lower()
    needles = ["1–3 page", "1 page budget", "cvs", "tax", "primary contact"]
    return sum(1 for needle in needles if needle in text)


def _count_selection_criteria(fact: FactCitation | None) -> int:
    if fact is None:
        return 0
    text = fact.claim.lower()
    return sum(1 for needle in ["relevance", "feasibility", "construct validity", "simplicity"] if needle in text)


def _require_non_placeholder(value: Any, field: str) -> None:
    if not isinstance(value, str) or value.strip().lower() in PLACEHOLDERS:
        raise ValueError(f"{field} must be non-empty and non-placeholder")


def _parse_iso_date(value: str, field: str) -> None:
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date") from exc


def write_reports(
    result: GrantSourceCitationEvalResult,
    reports_dir: Path = DEFAULT_REPORTS_DIR,
) -> dict[str, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    payload = result.as_dict()
    stamp = payload["date"]
    latest_json = reports_dir / "grant_source_citations_eval_latest.json"
    dated_json = reports_dir / f"grant_source_citations_eval_{stamp}.json"
    latest_md = reports_dir / "grant_source_citations_eval_latest.md"
    dated_md = reports_dir / f"grant_source_citations_eval_{stamp}.md"
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
    gate = payload["citation_gate"]
    metrics = payload["metrics"]
    facts = payload["proposal_ready_facts"]
    lines = [
        "# Parker grant source-citation guard",
        "",
        f"Date: {payload['date']}",
        f"Gate: {'PASS' if gate['passed'] else 'FAIL'}",
        "",
        "## Public-source facts to carry forward",
        "",
        f"- Award: {facts['award']}",
        f"- Deadline: {facts['deadline']}",
        f"- Selection criteria: {facts['selection_criteria']}",
        f"- Required materials: {facts['required_materials']}",
        f"- Confidentiality warning: {facts['confidentiality_warning']}",
        f"- Open-license note: {facts['open_license_note']}",
        f"- Realtime caveat: {facts['realtime_gap_note']}",
        "",
        "## Metrics",
        "",
        f"- Sources: {metrics['public_web_sources']}/{metrics['total_sources']} public web sources",
        f"- Required facts: {metrics['required_facts_covered']}/{metrics['required_facts']} covered ({metrics['required_fact_coverage']:.0%})",
        f"- Proposal requirements counted: {metrics['proposal_requirements_count']}",
        f"- Selection criteria counted: {metrics['selection_criteria_count']}",
        f"- Terms-risk facts counted: {metrics['terms_risk_facts']}",
        "",
        "## Sources",
        "",
    ]
    for source in payload["sources"]:
        fact_ids = ", ".join(fact["fact_id"] for fact in source["facts"])
        lines.append(f"- **{source['source_id']}** — {source['title']} — {source['url']} — facts: {fact_ids}")
    lines.extend(["", "## Blocking failures", ""])
    if gate["blocking_failures"]:
        for failure in gate["blocking_failures"]:
            lines.append(f"- {failure['check']}: {failure['message']}")
    else:
        lines.append("- None")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate public source citations for the Parker grant packet.")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument("--write-report", action="store_true", help="write latest and dated JSON/Markdown reports")
    args = parser.parse_args()

    result = evaluate_citations(load_citations())
    payload = result.as_dict()
    if args.write_report:
        paths = write_reports(result)
        print(f"Wrote {paths['latest_json']}")
        print(f"Wrote {paths['latest_md']}")
    elif args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_markdown(payload))
    return 0 if payload["citation_gate"]["passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
