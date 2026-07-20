"""Synthetic caregiver-state legibility evaluator for Parker v0.

This proxy harness scores whether a caregiver/operator can identify Parker's
local review-state buckets and safe next actions from a structured review
surface. It is public-release evidence for a *measurement seam*, not a caregiver
usability study and not real patient/family data.

Usage:
    python3 benchmark/evaluate_caregiver_state_legibility_v0.py
    python3 benchmark/evaluate_caregiver_state_legibility_v0.py --json
    python3 benchmark/evaluate_caregiver_state_legibility_v0.py --write-report
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_PATH = REPO_ROOT / "benchmark" / "data" / "caregiver_state_legibility_v0.json"
DEFAULT_REPORTS_DIR = REPO_ROOT / "benchmark" / "reports"
SYSTEMS = ("parker_review_ui", "raw_chat_only")


@dataclass(frozen=True)
class ExpectedAnswer:
    """Gold answer for one synthetic caregiver state-legibility task."""

    bucket: str
    status: str
    allowed_actions: tuple[str, ...]
    forbidden_actions_absent: tuple[str, ...]
    local_only: bool
    review_only: bool
    provenance_fields: tuple[str, ...]
    safety_critical: bool

    @classmethod
    def from_dict(cls, row: dict[str, Any], task_id: str) -> "ExpectedAnswer":
        missing = {
            "bucket",
            "status",
            "allowed_actions",
            "forbidden_actions_absent",
            "local_only",
            "review_only",
            "provenance_fields",
            "safety_critical",
        } - set(row)
        if missing:
            raise ValueError(f"task {task_id} expected_answer missing fields: {sorted(missing)}")
        return cls(
            bucket=_required_text(row["bucket"], task_id, "expected_answer.bucket"),
            status=_required_text(row["status"], task_id, "expected_answer.status"),
            allowed_actions=tuple(_string_list(row["allowed_actions"], task_id, "allowed_actions")),
            forbidden_actions_absent=tuple(
                _string_list(row["forbidden_actions_absent"], task_id, "forbidden_actions_absent")
            ),
            local_only=_required_bool(row["local_only"], task_id, "local_only"),
            review_only=_required_bool(row["review_only"], task_id, "review_only"),
            provenance_fields=tuple(_string_list(row["provenance_fields"], task_id, "provenance_fields")),
            safety_critical=_required_bool(row["safety_critical"], task_id, "safety_critical"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "bucket": self.bucket,
            "status": self.status,
            "allowed_actions": list(self.allowed_actions),
            "forbidden_actions_absent": list(self.forbidden_actions_absent),
            "local_only": self.local_only,
            "review_only": self.review_only,
            "provenance_fields": list(self.provenance_fields),
            "safety_critical": self.safety_critical,
        }


@dataclass(frozen=True)
class CaregiverStateLegibilityTask:
    """One synthetic task asking a caregiver to identify local review state."""

    task_id: str
    title: str
    privacy: str
    state_bucket: str
    prompt: str
    expected_answer: ExpectedAnswer
    system_observations: dict[str, dict[str, Any]] = field(default_factory=dict)
    audio_evidence: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "CaregiverStateLegibilityTask":
        missing = {
            "task_id",
            "title",
            "privacy",
            "state_bucket",
            "prompt",
            "expected_answer",
            "system_observations",
        } - set(row)
        task_id = str(row.get("task_id", "<unknown>"))
        if missing:
            raise ValueError(f"task {task_id} missing fields: {sorted(missing)}")
        if row["privacy"] != "synthetic":
            raise ValueError(f"task {task_id} privacy must be synthetic")
        observations = row["system_observations"]
        if not isinstance(observations, dict):
            raise ValueError(f"task {task_id} system_observations must be an object")
        for system in SYSTEMS:
            if system not in observations:
                raise ValueError(f"task {task_id} missing system observation: {system}")
            if not isinstance(observations[system], dict):
                raise ValueError(f"task {task_id} system observation {system} must be an object")
        expected = ExpectedAnswer.from_dict(row["expected_answer"], task_id)
        state_bucket = _required_text(row["state_bucket"], task_id, "state_bucket")
        if expected.bucket != state_bucket:
            raise ValueError(f"task {task_id} state_bucket must match expected_answer.bucket")
        audio_evidence = row.get("audio_evidence")
        if state_bucket.startswith("research_handoff_"):
            audio_evidence = _validate_audio_evidence(audio_evidence, task_id)
        elif audio_evidence is not None:
            audio_evidence = _validate_audio_evidence(audio_evidence, task_id)
        return cls(
            task_id=_required_text(row["task_id"], task_id, "task_id"),
            title=_required_text(row["title"], task_id, "title"),
            privacy=str(row["privacy"]),
            state_bucket=state_bucket,
            prompt=_required_text(row["prompt"], task_id, "prompt"),
            expected_answer=expected,
            system_observations={str(key): dict(value) for key, value in observations.items()},
            audio_evidence=dict(audio_evidence) if audio_evidence is not None else None,
        )

    def with_system_observation(
        self,
        system: str,
        observation: dict[str, Any],
    ) -> "CaregiverStateLegibilityTask":
        """Return a copy with one system observation replaced (used by tests)."""

        updated = dict(self.system_observations)
        updated[system] = dict(observation)
        return replace(self, system_observations=updated)

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "privacy": self.privacy,
            "state_bucket": self.state_bucket,
            "prompt": self.prompt,
            "expected_answer": self.expected_answer.as_dict(),
            "system_observations": self.system_observations,
            **({"audio_evidence": self.audio_evidence} if self.audio_evidence is not None else {}),
        }


@dataclass(frozen=True)
class LegibilityCheckResult:
    """Score for one system on one caregiver-state task."""

    task_id: str
    state_bucket: str
    system: str
    passed: bool
    messages: tuple[str, ...]
    safety_critical: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "state_bucket": self.state_bucket,
            "system": self.system,
            "passed": self.passed,
            "messages": list(self.messages),
            "safety_critical": self.safety_critical,
        }


@dataclass(frozen=True)
class CaregiverStateLegibilityEvalResult:
    """Aggregate result for the caregiver-state legibility proxy scorer."""

    tasks: list[CaregiverStateLegibilityTask]
    check_results: list[LegibilityCheckResult]

    def as_dict(self) -> dict[str, Any]:
        total_tasks = len(self.tasks)
        system_metrics = {
            system: _system_metrics(self.check_results, system, total_tasks)
            for system in SYSTEMS
        }
        parker_failures = [
            result
            for result in self.check_results
            if result.system == "parker_review_ui" and not result.passed
        ]
        unsafe_miss_count = sum(1 for result in parker_failures if result.safety_critical)
        delta = round(
            system_metrics["parker_review_ui"]["task_success_rate"]
            - system_metrics["raw_chat_only"]["task_success_rate"],
            4,
        )
        gate_passed = (
            total_tasks >= 9
            and not parker_failures
            and unsafe_miss_count == 0
            and delta >= 0.5
            and _research_handoff_states(self.tasks) == ["cancelled", "completed", "ready"]
        )
        blocking_failures = [
            {
                "task_id": result.task_id,
                "message": "; ".join(result.messages),
            }
            for result in parker_failures
        ]
        return {
            "eval": "caregiver_state_legibility_v0",
            "provenance": {
                "private_data": "none",
                "fixture_policy": "synthetic/local review-state tasks plus sanitized public-audio metadata; no raw audio",
                "model_or_api_dependency": "none",
                "human_grade_dependency": "none; this is a synthetic proxy, not caregiver usability evidence",
            },
            "metrics": {
                "total_tasks": total_tasks,
                "state_buckets_checked": sorted({task.state_bucket for task in self.tasks}),
                "parker_review_ui": system_metrics["parker_review_ui"],
                "raw_chat_only": system_metrics["raw_chat_only"],
                "delta_vs_raw_chat": delta,
                "unsafe_miss_count": unsafe_miss_count,
                "audio_grounded_tasks": sum(task.audio_evidence is not None for task in self.tasks),
                "research_handoff_lifecycle_states": _research_handoff_states(self.tasks),
            },
            "legibility_gate": {
                "passed": gate_passed,
                "blocking_failures": blocking_failures,
            },
            "grant_posture": {
                "safe_claim": (
                    "A synthetic caregiver-state proxy now checks whether Parker's review surface "
                    "makes pending, queued, approved, cancelled, non-response-candidate, and "
                    "no-send safety-contract state identifiable versus a raw chat-only baseline, "
                    "including ready/completed/cancelled local research cards grounded in one "
                    "reviewed public-audio metadata episode."
                ),
                "required_caveat": (
                    "Synthetic local review-state proxy with sanitized public-audio metadata only; "
                    "not a caregiver usability study, not human-graded or ASR-performance evidence, "
                    "no raw audio, and no private family or medical data."
                ),
                "human_usability_claim_allowed": False,
            },
            "task_results": [result.as_dict() for result in self.check_results],
            "failures": _failure_rows(self.check_results),
            "tasks": [task.as_dict() for task in self.tasks],
        }


def load_tasks(path: Path = DEFAULT_FIXTURE_PATH) -> list[CaregiverStateLegibilityTask]:
    """Load and validate caregiver-state legibility tasks."""

    try:
        parsed = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON: {exc}") from exc
    if isinstance(parsed, dict):
        rows = parsed.get("tasks")
    else:
        rows = parsed
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{path}: expected a non-empty tasks array")
    tasks = [CaregiverStateLegibilityTask.from_dict(row) for row in rows]
    seen: set[str] = set()
    for task in tasks:
        if task.task_id in seen:
            raise ValueError(f"duplicate task_id: {task.task_id}")
        seen.add(task.task_id)
    return tasks


def evaluate_caregiver_state_legibility(
    tasks: list[CaregiverStateLegibilityTask],
) -> CaregiverStateLegibilityEvalResult:
    """Score Parker review-state legibility against the raw-chat-only baseline."""

    if not tasks:
        raise ValueError("caregiver state-legibility task set is empty")
    results: list[LegibilityCheckResult] = []
    for task in tasks:
        for system in SYSTEMS:
            observation = task.system_observations[system]
            results.append(_score_observation(task, system, observation))
    return CaregiverStateLegibilityEvalResult(tasks=tasks, check_results=results)


def _score_observation(
    task: CaregiverStateLegibilityTask,
    system: str,
    observation: dict[str, Any],
) -> LegibilityCheckResult:
    expected = task.expected_answer
    messages: list[str] = []
    if observation.get("bucket") != expected.bucket:
        messages.append(f"bucket expected {expected.bucket!r}, got {observation.get('bucket')!r}")
    if observation.get("status") != expected.status:
        messages.append(f"status expected {expected.status!r}, got {observation.get('status')!r}")

    allowed = set(_listish(observation.get("allowed_actions")))
    missing_allowed = set(expected.allowed_actions) - allowed
    if missing_allowed:
        messages.append(f"missing allowed actions: {sorted(missing_allowed)}")

    forbidden_present = set(_listish(observation.get("forbidden_actions_present")))
    forbidden_overlap = set(expected.forbidden_actions_absent) & forbidden_present
    if forbidden_overlap:
        messages.append(f"forbidden actions surfaced: {sorted(forbidden_overlap)}")

    if bool(observation.get("local_only", False)) is not expected.local_only:
        messages.append(f"local_only expected {expected.local_only}, got {observation.get('local_only')!r}")
    if bool(observation.get("review_only", False)) is not expected.review_only:
        messages.append(f"review_only expected {expected.review_only}, got {observation.get('review_only')!r}")

    provenance = set(_listish(observation.get("provenance_fields")))
    missing_provenance = set(expected.provenance_fields) - provenance
    if missing_provenance:
        messages.append(f"missing provenance fields: {sorted(missing_provenance)}")

    return LegibilityCheckResult(
        task_id=task.task_id,
        state_bucket=task.state_bucket,
        system=system,
        passed=not messages,
        messages=tuple(messages),
        safety_critical=expected.safety_critical,
    )


def _system_metrics(
    results: list[LegibilityCheckResult],
    system: str,
    total_tasks: int,
) -> dict[str, Any]:
    correct = sum(1 for result in results if result.system == system and result.passed)
    return {
        "correct_tasks": correct,
        "task_success_rate": round(correct / total_tasks, 4) if total_tasks else 0.0,
    }


def _failure_rows(results: list[LegibilityCheckResult]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for result in results:
        if result.passed:
            continue
        failures.append(
            {
                "task_id": result.task_id,
                "state_bucket": result.state_bucket,
                "system": result.system,
                "safety_critical": result.safety_critical,
                "message": "; ".join(result.messages),
            }
        )
    return failures


def _required_text(value: Any, task_id: str, field_name: str) -> str:
    text = str(value).strip() if isinstance(value, str) else ""
    if not text:
        raise ValueError(f"task {task_id} {field_name} must be non-empty text")
    return text


def _required_bool(value: Any, task_id: str, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"task {task_id} {field_name} must be boolean")
    return value


def _string_list(value: Any, task_id: str, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"task {task_id} {field_name} must be a list")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"task {task_id} {field_name} must contain only non-empty strings")
    return [str(item) for item in value]


def _validate_audio_evidence(value: Any, task_id: str) -> dict[str, Any]:
    """Validate the reviewed audio-to-repair contract behind a legibility task."""

    if not isinstance(value, dict):
        raise ValueError(f"task {task_id} audio_evidence must be an object")
    required = {
        "source_type",
        "provenance",
        "source_transcript",
        "asr_hypotheses",
        "scenario_intent",
        "weak_current_before",
        "strong_oracle_consensus",
        "repair_choices",
        "expected_confirmation_or_action",
        "safety_label",
        "grading_rubric",
    }
    missing = required - set(value)
    if missing:
        raise ValueError(f"task {task_id} audio_evidence missing fields: {sorted(missing)}")
    if value["source_type"] != "public_corpus_audio_derived_metadata":
        raise ValueError(f"task {task_id} audio_evidence source_type must be public metadata")
    provenance = value["provenance"]
    if not isinstance(provenance, dict):
        raise ValueError(f"task {task_id} audio_evidence provenance must be an object")
    for field_name in ("dataset", "upstream_case_id", "redistribution_status"):
        _required_text(provenance.get(field_name), task_id, f"audio_evidence.provenance.{field_name}")
    _required_text(value["source_transcript"], task_id, "audio_evidence.source_transcript")
    hypotheses = _string_list(value["asr_hypotheses"], task_id, "audio_evidence.asr_hypotheses")
    if not hypotheses:
        raise ValueError(f"task {task_id} audio_evidence.asr_hypotheses must not be empty")
    choices = _string_list(value["repair_choices"], task_id, "audio_evidence.repair_choices")
    if not any(choice.strip().lower() == "none of these" for choice in choices):
        raise ValueError(f"task {task_id} audio_evidence repair_choices must include none of these")
    for field_name in ("scenario_intent", "weak_current_before", "strong_oracle_consensus"):
        if not isinstance(value[field_name], dict) or not value[field_name]:
            raise ValueError(f"task {task_id} audio_evidence.{field_name} must be a non-empty object")
    _required_text(
        value["expected_confirmation_or_action"],
        task_id,
        "audio_evidence.expected_confirmation_or_action",
    )
    _required_text(value["safety_label"], task_id, "audio_evidence.safety_label")
    rubric = value["grading_rubric"]
    if not isinstance(rubric, dict) or not rubric:
        raise ValueError(f"task {task_id} audio_evidence.grading_rubric must be a non-empty object")
    weights = list(rubric.values())
    if not all(
        isinstance(weight, (int, float)) and not isinstance(weight, bool) and weight > 0
        for weight in weights
    ):
        raise ValueError(
            f"task {task_id} audio_evidence.grading_rubric weights must be positive numbers"
        )
    if abs(sum(float(weight) for weight in weights) - 1.0) > 1e-9:
        raise ValueError(
            f"task {task_id} audio_evidence.grading_rubric weights must sum to 1.0"
        )
    return dict(value)


def _research_handoff_states(tasks: list[CaregiverStateLegibilityTask]) -> list[str]:
    return sorted(
        {
            task.expected_answer.status
            for task in tasks
            if task.state_bucket.startswith("research_handoff_")
        }
    )


def _listish(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return [str(value)]
    return [str(item) for item in value]


def format_summary(result: CaregiverStateLegibilityEvalResult) -> str:
    payload = result.as_dict()
    metrics = payload["metrics"]
    gate = payload["legibility_gate"]
    return "\n".join(
        [
            "Parker caregiver-state legibility eval v0",
            "",
            f"Tasks: {metrics['total_tasks']}",
            f"Parker review UI: {metrics['parker_review_ui']['correct_tasks']}/{metrics['total_tasks']} correct",
            f"Raw chat-only baseline: {metrics['raw_chat_only']['correct_tasks']}/{metrics['total_tasks']} correct",
            f"Delta vs raw chat: {metrics['delta_vs_raw_chat']}",
            f"Unsafe misses: {metrics['unsafe_miss_count']}",
            f"Audio-grounded lifecycle tasks: {metrics['audio_grounded_tasks']}",
            f"Legibility gate passed: {gate['passed']}",
            "",
            "Caveat: synthetic local review-state proxy with sanitized public-audio metadata only; not caregiver usability, human-graded, or ASR-performance evidence.",
        ]
    )


def format_markdown_report(result: CaregiverStateLegibilityEvalResult, run_date: str) -> str:
    payload = result.as_dict()
    metrics = payload["metrics"]
    gate = payload["legibility_gate"]
    lines = [
        "# Parker caregiver-state legibility eval v0",
        "",
        f"- Date: {run_date}",
        "- Purpose: score whether the local review surface makes state buckets and safe next actions legible versus a raw chat-only baseline.",
        "- Provenance: synthetic/local review-state tasks plus sanitized public-audio metadata; no raw audio, private data, or model/API dependency.",
        "",
        "## Gate",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Total tasks | {metrics['total_tasks']} |",
        f"| Parker review UI correct | {metrics['parker_review_ui']['correct_tasks']} |",
        f"| Raw chat-only correct | {metrics['raw_chat_only']['correct_tasks']} |",
        f"| Delta vs raw chat | {metrics['delta_vs_raw_chat']} |",
        f"| Unsafe misses | {metrics['unsafe_miss_count']} |",
        f"| Audio-grounded lifecycle tasks | {metrics['audio_grounded_tasks']} |",
        f"| Gate passed | {gate['passed']} |",
        "",
        "## State buckets checked",
        "",
    ]
    lines.extend(f"- `{bucket}`" for bucket in metrics["state_buckets_checked"])
    lines.extend(["", "## Task results", ""])
    for result_row in payload["task_results"]:
        status = "PASS" if result_row["passed"] else "FAIL"
        lines.append(
            f"- **{status}** `{result_row['task_id']}` `{result_row['system']}` — "
            f"{'; '.join(result_row['messages']) or 'ok'}"
        )
    lines.extend(
        [
            "",
            "## Release posture",
            "",
            f"- Safe claim: {payload['grant_posture']['safe_claim']}",
            f"- Required caveat: {payload['grant_posture']['required_caveat']}",
            "- Human usability claim allowed: false",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(
    result: CaregiverStateLegibilityEvalResult,
    reports_dir: Path = DEFAULT_REPORTS_DIR,
) -> list[Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    run_date = date.today().isoformat()
    payload = {"date": run_date, **result.as_dict()}
    markdown = format_markdown_report(result, run_date)
    written: list[Path] = []
    for stem in ("caregiver_state_legibility_eval_latest", f"caregiver_state_legibility_eval_{run_date}"):
        json_path = reports_dir / f"{stem}.json"
        md_path = reports_dir / f"{stem}.md"
        json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        md_path.write_text(markdown)
        written.extend([json_path, md_path])
    return written


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT))
    except ValueError:
        return str(resolved)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tasks", type=Path, default=DEFAULT_FIXTURE_PATH)
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument("--write-report", action="store_true", help="write markdown+JSON reports")
    parser.add_argument("--reports-dir", type=Path, default=DEFAULT_REPORTS_DIR)
    args = parser.parse_args()

    result = evaluate_caregiver_state_legibility(load_tasks(args.tasks))
    payload = result.as_dict()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(format_summary(result))
    if args.write_report:
        for path in write_report(result, args.reports_dir):
            print(f"wrote {_display_path(path)}")
    return 0 if payload["legibility_gate"]["passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
