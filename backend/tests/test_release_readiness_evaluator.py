"""Tests for Parker's release-readiness rollup evaluator."""

import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

import benchmark.evaluate_release_readiness_v0 as release_readiness  # type: ignore[import-not-found] # noqa: E402
from benchmark.evaluate_release_readiness_v0 import (  # type: ignore[import-not-found] # noqa: E402
    REQUIRED_REPORTS,
    evaluate_release_readiness,
)

REPO = Path(__file__).resolve().parents[2]
EVALUATOR = REPO / "benchmark/evaluate_release_readiness_v0.py"
MAKEFILE = REPO / "Makefile"


def _reports_as_of() -> date:
    """The checked-in reports' own newest date.

    The unit tests pin the rollup LOGIC against the committed evidence; the
    calendar-relative freshness gate belongs to the release lane
    (`make eval-release-readiness`, which regenerates every source report
    first). Judging the committed reports against wall-clock today made the
    whole suite go red every second midnight UTC (PR #42 CI, 2026-09-02).
    """

    dates = []
    for path in REQUIRED_REPORTS.values():
        raw = json.loads(path.read_text()).get("date")
        if isinstance(raw, str):
            dates.append(date.fromisoformat(raw))
    return max(dates)


def test_release_readiness_rollup_summarizes_actionable_public_evidence() -> None:
    """One command should tell Pras exactly what the README/launch post can safely claim."""

    payload = evaluate_release_readiness(as_of=_reports_as_of()).as_dict()

    assert payload["eval"] == "release_readiness_v0"
    assert payload["readiness_gate"]["passed"] is True
    assert payload["readiness_gate"]["blocking_failures"] == []
    assert payload["provenance"] == {
        "private_data": "none",
        "fixture_policy": "public synthetic/local reports only",
        "model_or_api_dependency": "none",
    }

    assert payload["metrics"]["claim_metric_map"] == {
        "total_claims": 4,
        "passing_claims": 4,
        "assertions_checked": 19,
        "assertions_failed": 0,
        "overclaim_gate_passed": True,
    }
    assert payload["metrics"]["construct_validity"] == {
        "total_constructs": 6,
        "citable_constructs": 4,
        "research_gap_constructs": 2,
        "passing_citable_constructs": 4,
        "assertions_checked": 16,
        "assertions_failed": 0,
        "construct_validity_gate_passed": True,
    }
    assert payload["metrics"]["degraded_input_replay"]["synthetic_cases"] == 3
    assert payload["metrics"]["degraded_input_replay"]["parker_recovered"] == 3
    assert payload["metrics"]["degraded_input_replay"]["no_repair_recovered"] == 0
    assert payload["metrics"]["degraded_input_replay"]["one_shot_keyword_baseline_recovered"] == 2
    assert payload["metrics"]["degraded_input_replay"]["unsafe_miss_count"] == 0
    assert payload["metrics"]["task_taxonomy"] == {
        "synthetic_cases": 24,
        "route_accuracy": 1.0,
        "action_type_accuracy": 1.0,
        "unsafe_miss_count": 0,
        "refusal_recall": 1.0,
        "escalation_recall": 1.0,
    }
    assert payload["metrics"]["demo_interactivity"] == {
        "synthetic_scenarios": 9,
        "overall_pass_rate": 1.0,
        "unsafe_miss_count": 0,
        "confirmation_before_action": 1.0,
        "confirmation_restatement_binding": 1.0,
        "confirmation_interruption_repair": 1.0,
        "local_outbox_reversibility": 1.0,
        "caregiver_ui_clarity": 1.0,
    }
    assert payload["metrics"]["repair_quality_rubric"] == {
        "total_cases": 5,
        "reference_passing_cases": 5,
        "generic_fallback_passing_cases": 0,
        "rubric_detects_generic_fallback": True,
        "quality_proof_claim_allowed": False,
    }
    assert payload["metrics"]["caregiver_state_legibility"] == {
        "total_tasks": 10,
        "parker_review_ui_correct_tasks": 10,
        "raw_chat_only_correct_tasks": 0,
        "delta_vs_raw_chat": 1.0,
        "unsafe_miss_count": 0,
        "legibility_gate_passed": True,
    }
    assert payload["release_summary"]["repair_quality_caveat"] == "Repair-choice specificity is proxy-rubric checked only; human-graded repair quality remains an open research gap."
    assert payload["release_summary"]["caregiver_legibility_caveat"] == "Caregiver state legibility is synthetic proxy checked only; human caregiver task-completion time/error rate remains an open research gap."

    freshness = payload["source_report_freshness"]
    as_of = _reports_as_of()
    assert freshness["expected_date"] == as_of.isoformat()
    assert freshness["accepted_dates"] == [
        (as_of - timedelta(days=1)).isoformat(),
        as_of.isoformat(),
    ]
    assert freshness["all_current"] is True
    assert freshness["stale_reports"] == []
    assert set(freshness["report_dates"]) == set(REQUIRED_REPORTS)
    assert all(report_date in freshness["accepted_dates"] for report_date in freshness["report_dates"].values())

    assert len(payload["claim_cards"]) == 4
    assert all(card["status"] == "pass" for card in payload["claim_cards"])
    assert {card["claim_id"] for card in payload["claim_cards"]} == {
        "claim-001-real-audio-repair-recovery",
        "claim-002-brain-lane-keyless-safety",
        "claim-003-audio-autodata-pipeline",
        "claim-004-caregiver-state-legibility",
    }
    assert {
        "benchmark/reports/audio_real_eval_latest.json",
        "benchmark/reports/brain_lane_eval_latest.json",
        "benchmark/reports/audio_repair_autodata_eval_latest.json",
        "benchmark/reports/degraded_input_replay_eval_latest.json",
        "benchmark/reports/task_taxonomy_eval_latest.json",
        "benchmark/reports/parker_demo_interactivity_eval_latest.json",
        "benchmark/reports/claim_metric_map_eval_latest.json",
        "benchmark/reports/construct_validity_matrix_eval_latest.json",
        "benchmark/reports/repair_quality_rubric_eval_latest.json",
        "benchmark/reports/caregiver_state_legibility_eval_latest.json",
    }.issubset(set(payload["evidence_paths_checked"]))

    safe_claim = payload["release_summary"]["safe_claim_line"]
    caveat = payload["release_summary"]["required_caveat"]
    assert "3 synthetic held-out transcript fixtures" in safe_claim
    assert "one-shot keyword" in safe_claim
    assert "0 unsafe misses" in safe_claim
    assert "not real" in caveat.lower()
    assert "no private" in caveat.lower()


def test_release_readiness_fails_closed_when_required_report_is_missing(tmp_path: Path) -> None:
    report_paths = dict(REQUIRED_REPORTS)
    report_paths["demo_interactivity"] = tmp_path / "missing-demo-report.json"

    payload = evaluate_release_readiness(report_paths=report_paths).as_dict()

    assert payload["readiness_gate"]["passed"] is False
    assert any(
        failure["check"] == "demo_interactivity_report"
        and "missing-demo-report.json" in failure["message"]
        for failure in payload["readiness_gate"]["blocking_failures"]
    )


def test_release_readiness_accepts_previous_day_reports_across_timezone_boundary(tmp_path: Path) -> None:
    """A UTC runner must not reject reports generated late in the prior local day."""

    report_paths: dict[str, Path] = {}
    previous_day = (date.today() - timedelta(days=1)).isoformat()
    for report_name, source_path in REQUIRED_REPORTS.items():
        copied_path = tmp_path / f"{report_name}.json"
        report = json.loads(source_path.read_text())
        report["date"] = previous_day
        copied_path.write_text(json.dumps(report))
        report_paths[report_name] = copied_path

    payload = evaluate_release_readiness(report_paths=report_paths).as_dict()

    assert payload["readiness_gate"]["passed"] is True
    assert payload["source_report_freshness"]["all_current"] is True
    assert payload["source_report_freshness"]["stale_reports"] == []
    assert previous_day in payload["source_report_freshness"]["accepted_dates"]


def test_release_readiness_fails_closed_when_source_report_date_is_stale(tmp_path: Path) -> None:
    report_paths: dict[str, Path] = {}
    for report_name, source_path in REQUIRED_REPORTS.items():
        copied_path = tmp_path / f"{report_name}.json"
        copied_path.write_text(source_path.read_text())
        report_paths[report_name] = copied_path

    stale_report = json.loads(report_paths["task_taxonomy"].read_text())
    stale_report["date"] = "1999-01-01"
    report_paths["task_taxonomy"].write_text(json.dumps(stale_report))

    as_of = _reports_as_of()
    payload = evaluate_release_readiness(report_paths=report_paths, as_of=as_of).as_dict()

    assert payload["readiness_gate"]["passed"] is False
    assert payload["source_report_freshness"]["all_current"] is False
    assert payload["source_report_freshness"]["stale_reports"] == [
        {
            "report": "task_taxonomy",
            "path": str(report_paths["task_taxonomy"]),
            "date": "1999-01-01",
            "expected_date": as_of.isoformat(),
        }
    ]
    assert any(
        failure["check"] == "source_report_freshness"
        and "task_taxonomy" in failure["message"]
        for failure in payload["readiness_gate"]["blocking_failures"]
    )


def test_release_readiness_does_not_require_retired_grant_lane_reports() -> None:
    """The retired citations lane must not be a rollup dependency going forward."""

    assert all("citation" not in name for name in REQUIRED_REPORTS)
    assert all("citation" not in str(path) for path in REQUIRED_REPORTS.values())


def test_release_readiness_cli_json_outputs_briefing_fields() -> None:
    completed = subprocess.run(
        [sys.executable, str(EVALUATOR), "--json", "--as-of", _reports_as_of().isoformat()],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["readiness_gate"]["passed"] is True
    assert payload["release_summary"]["primary_decision"] == "Safe to cite as synthetic/local evidence in public release claims (README, launch post); not safe to present as real-world or clinical proof."


def test_as_of_cannot_mint_a_current_dated_report(monkeypatch, capsys) -> None:
    """``--as-of`` is a test-only freshness override; with ``--write-report``
    it would mint ``release_readiness_eval_<today>.*`` (and overwrite
    ``latest``) with gate PASS from evidence that fails the gate today
    (reproduced 2026-09-02 against the 2026-08-31 reports). The CLI must
    refuse the pair through argparse — exit 2, never a gate verdict —
    before any evaluation or file write. In-process with spies so a
    pre-fix run cannot dirty benchmark/reports.
    """

    writes: list[dict] = []
    monkeypatch.setattr(
        release_readiness,
        "evaluate_release_readiness",
        lambda **kwargs: pytest.fail("evaluated before refusing the flag pair"),
    )
    monkeypatch.setattr(
        release_readiness,
        "write_reports",
        lambda *args, **kwargs: writes.append({"args": args, "kwargs": kwargs}) or {},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluate_release_readiness_v0.py",
            "--as-of",
            _reports_as_of().isoformat(),
            "--write-report",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        release_readiness.main()

    assert exc.value.code == 2
    assert writes == []
    captured = capsys.readouterr()
    assert "--as-of" in captured.err
    assert "--write-report" in captured.err
    assert captured.out == ""


def test_makefile_exposes_one_command_release_readiness_rollup() -> None:
    makefile = MAKEFILE.read_text()

    assert "eval-release-readiness" in makefile
    assert "benchmark/evaluate_tasks_v0.py --write-report" in makefile
    assert "benchmark/evaluate_caregiver_state_legibility_v0.py --write-report" in makefile
    assert "benchmark/evaluate_construct_validity_matrix_v0.py --write-report" in makefile
    assert "benchmark/evaluate_repair_quality_rubric_v0.py --write-report" in makefile
    assert "benchmark/evaluate_release_readiness_v0.py --write-report" in makefile
    assert "grant" not in makefile.lower()


def test_makefile_release_readiness_refreshes_every_source_report_before_rollup() -> None:
    makefile = MAKEFILE.read_text()
    target_line = next(line for line in makefile.splitlines() if line.startswith("eval-release-readiness:"))

    for dependency in [
        "eval-tasks",
        "eval-demo-interactivity",
        "eval-degraded-input-replay",
        "eval-caregiver-state-legibility",
        "eval-claim-metric-map",
        "eval-construct-validity",
        "eval-repair-quality-rubric",
    ]:
        assert dependency in target_line
