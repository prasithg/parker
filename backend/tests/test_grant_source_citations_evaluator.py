"""Tests for Parker's grant public-source citation evaluator."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from benchmark.evaluate_grant_source_citations_v0 import (  # type: ignore[import-not-found] # noqa: E402
    DEFAULT_CITATION_PATH,
    REQUIRED_FACT_IDS,
    evaluate_citations,
    load_citations,
)

REPO = Path(__file__).resolve().parents[2]
EVALUATOR = REPO / "benchmark/evaluate_grant_source_citations_v0.py"
MAKEFILE = REPO / "Makefile"
WORKFLOW = REPO / ".github/workflows/parker-ci.yml"


def test_grant_source_citations_cover_public_program_facts_only() -> None:
    """The grant packet should cite public sources for program facts, not memory."""

    sources = load_citations(DEFAULT_CITATION_PATH)

    assert len(sources) == 4
    assert {source.source_id for source in sources} == {
        "src-001-announcement",
        "src-002-apply",
        "src-003-terms",
        "src-004-interaction-models",
    }
    assert {source.source_type for source in sources} == {"public_web"}
    assert all(source.url.startswith("https://thinkingmachines.ai/") for source in sources)

    facts = {fact.fact_id for source in sources for fact in source.facts}
    assert REQUIRED_FACT_IDS <= facts
    assert "award_amount_and_tinker_credits" in facts
    assert "submission_deadline" in facts
    assert "selection_criteria" in facts
    assert "proposal_may_be_public" in facts
    assert "interaction_models_micro_turns" in facts


def test_grant_source_citation_evaluator_reports_gate_and_materials() -> None:
    payload = evaluate_citations(load_citations(DEFAULT_CITATION_PATH)).as_dict()

    assert payload["eval"] == "grant_source_citations_v0"
    assert payload["provenance"] == {
        "source_policy": "public Thinking Machines pages only",
        "private_data": "none",
        "model_or_api_dependency": "none",
    }
    assert payload["citation_gate"]["passed"] is True
    assert payload["citation_gate"]["blocking_failures"] == []
    assert payload["metrics"] == {
        "total_sources": 4,
        "public_web_sources": 4,
        "total_facts": 11,
        "required_facts": 11,
        "required_facts_covered": 11,
        "required_fact_coverage": 1.0,
        "proposal_requirements_count": 5,
        "selection_criteria_count": 4,
        "terms_risk_facts": 3,
    }
    assert payload["proposal_ready_facts"]["deadline"] == "June 19, 2026 at 11:59 PM PDT"
    assert payload["proposal_ready_facts"]["award"] == "$100,000 cash grant plus $25,000 in Tinker credits"
    assert "construct validity" in payload["proposal_ready_facts"]["selection_criteria"]
    assert "may become public" in payload["proposal_ready_facts"]["confidentiality_warning"]


def test_grant_source_citations_reject_missing_or_placeholder_evidence(tmp_path: Path) -> None:
    weak_path = tmp_path / "weak_citations.json"
    weak_path.write_text(
        json.dumps(
            [
                {
                    "source_id": "src-bad",
                    "title": "Bad source",
                    "url": "https://thinkingmachines.ai/news/interactivity-research-grants",
                    "source_type": "public_web",
                    "checked_at": "2026-06-18",
                    "facts": [
                        {
                            "fact_id": "award_amount_and_tinker_credits",
                            "claim": "TBD",
                            "verbatim_excerpt": "Multiple grants of $100,000.",
                            "proposal_usage": "grant packet",
                        }
                    ],
                }
            ]
        )
    )

    with pytest.raises(ValueError, match="claim"):
        load_citations(weak_path)

    payload = json.loads(weak_path.read_text())
    payload[0]["facts"][0]["claim"] = "Grant amount is sourced."
    payload[0]["facts"][0]["verbatim_excerpt"] = ""
    weak_path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="verbatim_excerpt"):
        load_citations(weak_path)


def test_grant_source_citation_cli_json_outputs_current_gate() -> None:
    completed = subprocess.run(
        [sys.executable, str(EVALUATOR), "--json"],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["citation_gate"]["passed"] is True
    assert payload["metrics"]["required_fact_coverage"] == 1.0


def test_makefile_and_ci_expose_grant_source_citation_eval() -> None:
    makefile = MAKEFILE.read_text()
    workflow = WORKFLOW.read_text()

    assert "eval-grant-source-citations" in makefile
    assert "benchmark/evaluate_grant_source_citations_v0.py --write-report" in makefile
    assert "make eval-grant-source-citations" in workflow


def test_grant_readiness_refreshes_source_citations_before_rollup() -> None:
    makefile = MAKEFILE.read_text()
    target_line = next(line for line in makefile.splitlines() if line.startswith("eval-grant-readiness:"))

    assert "eval-grant-source-citations" in target_line
