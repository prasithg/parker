"""Plan safe promotion of nightly audio-Autodata candidates into repo fixtures.

The nightly Operations loop is allowed to touch raw/public audio and local cache
paths. The repo is not: it should only receive metadata, ASR hypotheses, source
labels, safety labels, and rubrics. This module is the reviewable seam between
those worlds. It reads a nightly ``promotion_candidates.json`` payload, validates
any embedded repo-ready fixture/candidate objects, checks for duplicates and raw
artifact leakage, and emits a deterministic promotion plan.

It deliberately does **not** mutate the repo. Applying the plan is still a small,
reviewable JSON patch followed by the normal eval/test gates.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmark.evaluate_audio_repair_autodata_v0 import (
    AudioAutodataCase,
    HeldAudioAutodataCandidate,
    DEFAULT_CASES_PATH,
    evaluate,
    load_cases,
    load_held_candidates,
)

# Repo fixtures must never carry Operations-local raw audio paths or private
# family paths. A public URL is fine; a local path or raw audio filename is not.
_FORBIDDEN_STRING_PATTERNS = (
    re.compile(r"/Users/"),
    re.compile(r"\b(?:file://)?(?:/var/|/tmp/|/private/var/|/Volumes/)"),
    re.compile(r"\b[a-zA-Z]:\\"),
)
_RAW_AUDIO_FIELD_NAMES = {
    "audio_path",
    "audio_asset_url_ephemeral",
    "download",
    "metadata",
    "path",
    "local_path",
    "transcript_path",
}
_RAW_AUDIO_EXTENSIONS = (".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".webm")


@dataclass(frozen=True)
class CandidateValidation:
    """Validation result for a single accepted or held candidate."""

    kind: str
    candidate_id: str
    ready: bool
    status: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    dedupe_keys: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "candidate_id": self.candidate_id,
            "ready": self.ready,
            "status": self.status,
            "errors": self.errors,
            "warnings": self.warnings,
            "dedupe_keys": self.dedupe_keys,
        }


@dataclass(frozen=True)
class PromotionPlan:
    """Structured plan emitted by the promoter."""

    source_candidates_path: str
    target_cases_path: str
    source_run_hint: str | None
    raw_audio_not_committed: bool
    accepted: list[CandidateValidation]
    held: list[CandidateValidation]
    skipped: list[CandidateValidation]
    before_metrics: dict[str, Any]
    after_metrics: dict[str, Any]
    patch_suggestions: dict[str, Any]
    verification: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_candidates_path": self.source_candidates_path,
            "target_cases_path": self.target_cases_path,
            "source_run_hint": self.source_run_hint,
            "raw_audio_not_committed": self.raw_audio_not_committed,
            "counts": {
                "accepted_ready": sum(1 for item in self.accepted if item.ready),
                "held_ready": sum(1 for item in self.held if item.ready),
                "blocked_or_duplicate": sum(1 for item in [*self.accepted, *self.held, *self.skipped] if not item.ready),
                "skipped": len(self.skipped),
            },
            "accepted": [item.as_dict() for item in self.accepted],
            "held": [item.as_dict() for item in self.held],
            "skipped": [item.as_dict() for item in self.skipped],
            "before_metrics": self.before_metrics,
            "after_metrics": self.after_metrics,
            "patch_suggestions": self.patch_suggestions,
            "verification": self.verification,
        }


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return payload


def _walk_values(value: Any, *, path: str = "$", parent_key: str | None = None) -> Iterable[tuple[str, str, str | None]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            yield from _walk_values(child, path=child_path, parent_key=str(key))
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            yield from _walk_values(child, path=f"{path}[{idx}]", parent_key=parent_key)
    elif isinstance(value, str):
        yield path, value, parent_key


def _raw_or_local_leaks(value: Any) -> list[str]:
    leaks: list[str] = []
    for path, text, key in _walk_values(value):
        if key in _RAW_AUDIO_FIELD_NAMES:
            leaks.append(f"{path}: raw/local artifact field '{key}' is not repo-safe")
            continue
        lowered = text.lower()
        for pattern in _FORBIDDEN_STRING_PATTERNS:
            if pattern.search(text):
                leaks.append(f"{path}: contains local/private path-like text")
                break
        if lowered.endswith(_RAW_AUDIO_EXTENSIONS):
            leaks.append(f"{path}: points at a raw audio file ({Path(text).suffix})")
    return leaks


def _source_row_key_from_candidate(row: dict[str, Any]) -> str:
    dataset = str(row.get("dataset") or row.get("source_type") or "")
    row_idx = row.get("row_idx")
    transcript = str(row.get("source_transcript") or row.get("clean_phrase") or "")
    return "|".join([dataset, str(row_idx), transcript.lower().strip()])


def _source_row_key_from_case(case: AudioAutodataCase) -> str:
    source = str(case.provenance.get("source_url") or case.source_type)
    return "|".join([source, "", case.clean_phrase.lower().strip()])


def _command_family_for_fixture(case: AudioAutodataCase) -> str:
    action = case.final_action_type or case.clean_intent.get("action_type") or case.safety_label
    label = str(case.judge.get("label") or case.safety_label)
    # Keep this intentionally coarse: it is a dedupe warning, not a blocker.
    if "media" in str(action) or "music" in label or "media" in label:
        return "media"
    if "medical" in label or "medication" in label or "medical" in case.safety_label:
        return "medical"
    if "finance" in label or "account" in label or "financial" in case.safety_label:
        return "finance"
    if "control" in label or "context" in label:
        return "context_control"
    return str(action)


def _command_family_for_held(candidate: HeldAudioAutodataCandidate) -> str:
    text = " ".join([
        candidate.source_intent_class,
        candidate.oracle_target,
        candidate.safety_label,
        candidate.hold_reason,
    ]).lower()
    if "medical" in text or "medication" in text:
        return "medical"
    if "finance" in text or "account" in text:
        return "finance"
    if "media" in text or "music" in text or "playlist" in text:
        return "media"
    if "ambient" in text or "wake" in text or "noncommand" in text:
        return "addressed_to_me"
    if "control" in text or "context" in text or "app" in text or "device" in text:
        return "context_control"
    return candidate.source_intent_class


def _candidate_id(raw: dict[str, Any], fallback: str) -> str:
    fixture = raw.get("repo_fixture_case")
    if isinstance(fixture, dict) and fixture.get("case_id"):
        return str(fixture["case_id"])
    held = raw.get("repo_held_candidate")
    if isinstance(held, dict) and held.get("candidate_id"):
        return str(held["candidate_id"])
    return str(raw.get("candidate_id") or raw.get("decision") or fallback)


def _validate_fixture_candidate(
    raw: dict[str, Any],
    *,
    existing_case_ids: set[str],
    existing_case_source_keys: set[str],
    ordinal: int,
) -> tuple[CandidateValidation, AudioAutodataCase | None]:
    candidate_id = _candidate_id(raw, f"accepted[{ordinal}]")
    fixture = raw.get("repo_fixture_case")
    if not isinstance(fixture, dict):
        return CandidateValidation(
            kind="accepted_fixture",
            candidate_id=candidate_id,
            ready=False,
            status="missing_repo_fixture_case",
            errors=["accepted candidate has no repo_fixture_case object"],
            dedupe_keys={"source_row": _source_row_key_from_candidate(raw)},
        ), None

    leaks = _raw_or_local_leaks(fixture)
    errors = list(leaks)
    warnings: list[str] = []
    case: AudioAutodataCase | None = None
    try:
        case = AudioAutodataCase.from_dict(fixture)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"schema: {type(exc).__name__}: {exc}")

    case_id = str(fixture.get("case_id") or candidate_id)
    source_key = ""
    family = ""
    if case is not None:
        source_key = _source_row_key_from_case(case)
        family = _command_family_for_fixture(case)
        if case.case_id in existing_case_ids:
            errors.append("duplicate case_id already present in repo fixture data")
        if source_key in existing_case_source_keys:
            warnings.append("source transcript/source URL resembles an existing case; verify this is not duplicate coverage")
        if case.accepted is not True:
            errors.append("repo_fixture_case judge.accepted must be true for accepted promotion")
        if case.safety.get("private_data") != "none":
            errors.append("safety.private_data must be 'none'")
    ready = not errors
    return CandidateValidation(
        kind="accepted_fixture",
        candidate_id=case_id,
        ready=ready,
        status="ready" if ready else "blocked",
        errors=errors,
        warnings=warnings,
        dedupe_keys={"source_row": source_key or _source_row_key_from_candidate(raw), "command_family": family},
    ), case if ready else None


def _validate_held_candidate(
    raw: dict[str, Any],
    *,
    existing_held_ids: set[str],
    ordinal: int,
) -> tuple[CandidateValidation, HeldAudioAutodataCandidate | None]:
    candidate_id = _candidate_id(raw, f"held[{ordinal}]")
    held = raw.get("repo_held_candidate")
    if not isinstance(held, dict):
        return CandidateValidation(
            kind="held_candidate",
            candidate_id=candidate_id,
            ready=False,
            status="held_without_repo_payload",
            warnings=["held candidate is useful Operations evidence but has no repo_held_candidate object to append"],
            dedupe_keys={"source_row": _source_row_key_from_candidate(raw)},
        ), None

    leaks = _raw_or_local_leaks(held)
    errors = list(leaks)
    warnings: list[str] = []
    candidate: HeldAudioAutodataCandidate | None = None
    try:
        candidate = HeldAudioAutodataCandidate.from_dict(held)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"schema: {type(exc).__name__}: {exc}")
    held_id = str(held.get("candidate_id") or candidate_id)
    family = ""
    if candidate is not None:
        family = _command_family_for_held(candidate)
        if candidate.candidate_id in existing_held_ids:
            errors.append("duplicate held candidate_id already present in repo fixture data")
    ready = not errors
    return CandidateValidation(
        kind="held_candidate",
        candidate_id=held_id,
        ready=ready,
        status="ready" if ready else "blocked",
        errors=errors,
        warnings=warnings,
        dedupe_keys={"source_row": _source_row_key_from_candidate(raw), "command_family": family},
    ), candidate if ready else None


def build_promotion_plan(candidates_path: Path, *, cases_path: Path = DEFAULT_CASES_PATH) -> PromotionPlan:
    payload = _read_json(candidates_path)
    existing_cases = load_cases(cases_path)
    existing_held = load_held_candidates(cases_path)
    existing_case_ids = {case.case_id for case in existing_cases}
    existing_held_ids = {candidate.candidate_id for candidate in existing_held}
    existing_case_source_keys = {_source_row_key_from_case(case) for case in existing_cases}

    accepted_validations: list[CandidateValidation] = []
    held_validations: list[CandidateValidation] = []
    skipped: list[CandidateValidation] = []
    ready_cases: list[AudioAutodataCase] = []
    ready_held: list[HeldAudioAutodataCandidate] = []

    for ordinal, raw in enumerate(payload.get("accepted", [])):
        if not isinstance(raw, dict):
            skipped.append(CandidateValidation("accepted_fixture", f"accepted[{ordinal}]", False, "malformed", ["entry is not an object"]))
            continue
        validation, case = _validate_fixture_candidate(
            raw,
            existing_case_ids=existing_case_ids,
            existing_case_source_keys=existing_case_source_keys,
            ordinal=ordinal,
        )
        accepted_validations.append(validation)
        if case is not None:
            ready_cases.append(case)

    for ordinal, raw in enumerate(payload.get("held", [])):
        if not isinstance(raw, dict):
            skipped.append(CandidateValidation("held_candidate", f"held[{ordinal}]", False, "malformed", ["entry is not an object"]))
            continue
        validation, candidate = _validate_held_candidate(raw, existing_held_ids=existing_held_ids, ordinal=ordinal)
        held_validations.append(validation)
        if candidate is not None:
            ready_held.append(candidate)

    for section in ("rejected",):
        for ordinal, raw in enumerate(payload.get(section, [])):
            if isinstance(raw, dict):
                skipped.append(
                    CandidateValidation(
                        kind=section,
                        candidate_id=_candidate_id(raw, f"{section}[{ordinal}]"),
                        ready=False,
                        status="not_for_repo_promotion",
                        warnings=[str(raw.get("reason") or raw.get("decision") or "rejected by nightly loop")],
                        dedupe_keys={"source_row": _source_row_key_from_candidate(raw)},
                    )
                )

    before = evaluate(existing_cases, held_candidates=existing_held).metrics()
    after = evaluate([*existing_cases, *ready_cases], held_candidates=[*existing_held, *ready_held]).metrics()
    raw_audio_not_committed = not any(
        item.errors
        for item in [*accepted_validations, *held_validations]
        if any("raw/local" in error or "audio" in error or "path" in error for error in item.errors)
    )
    run_hint = _infer_run_hint(payload, candidates_path)
    ready_case_ids = [case.case_id for case in ready_cases]
    ready_held_ids = [candidate.candidate_id for candidate in ready_held]
    patch_suggestions = {
        "target_data_file": str(cases_path),
        "update_generated_from_run_to": run_hint,
        "append_cases": ready_case_ids,
        "append_held_candidates": ready_held_ids,
        "count_delta": {
            key: after.get(key, 0) - before.get(key, 0)
            for key in sorted(set(before) | set(after))
            if isinstance(before.get(key, 0), int) and isinstance(after.get(key, 0), int) and after.get(key, 0) != before.get(key, 0)
        },
        "claim_map_expected_total_cases": after.get("total_cases") if ready_cases else before.get("total_cases"),
        "docs_to_check": [
            "README.md (only if accepted fixture counts changed)",
            "benchmark/README.md (accepted/held coverage counts and caveats)",
            "docs/next-slices.md (implementation/eval log if this becomes a shipped slice)",
            "benchmark/data/parker_claim_metric_map_v0.json (only if accepted fixture count changed)",
        ],
    }
    verification = [
        "python3 benchmark/evaluate_audio_repair_autodata_v0.py --write-report",
        "backend/.venv/bin/python -m pytest backend/tests/test_audio_autodata_evaluator.py -q",
        "git diff --check",
    ]
    if ready_cases:
        verification.insert(2, "TZ=UTC make eval-release-readiness")

    return PromotionPlan(
        source_candidates_path=str(candidates_path),
        target_cases_path=str(cases_path),
        source_run_hint=run_hint,
        raw_audio_not_committed=raw_audio_not_committed,
        accepted=accepted_validations,
        held=held_validations,
        skipped=skipped,
        before_metrics=before,
        after_metrics=after,
        patch_suggestions=patch_suggestions,
        verification=verification,
    )


def _infer_run_hint(payload: dict[str, Any], candidates_path: Path) -> str | None:
    for row in payload.get("accepted", []):
        if not isinstance(row, dict):
            continue
        fixture = row.get("repo_fixture_case")
        if isinstance(fixture, dict):
            run_artifact = ((fixture.get("provenance") or {}).get("run_artifact"))
            if isinstance(run_artifact, str) and "parker-autodata-nightly/runs/" in run_artifact:
                return str(Path(run_artifact).parent)
    text = str(candidates_path)
    marker = "parker-autodata-nightly/runs/"
    if marker in text:
        suffix = text.split(marker, 1)[1]
        parts = suffix.split("/")
        if len(parts) >= 2:
            return f"{marker}{parts[0]}/{parts[1]}"
    return None


def _format_text(plan: PromotionPlan) -> str:
    payload = plan.as_dict()
    lines = [
        "Audio Autodata promotion plan",
        f"- candidates: {payload['source_candidates_path']}",
        f"- target: {payload['target_cases_path']}",
        f"- raw audio not committed: {payload['raw_audio_not_committed']}",
        f"- accepted ready: {payload['counts']['accepted_ready']}",
        f"- held ready: {payload['counts']['held_ready']}",
        f"- blocked/duplicate: {payload['counts']['blocked_or_duplicate']}",
        f"- metric deltas: {payload['patch_suggestions']['count_delta']}",
        "- verification:",
    ]
    lines.extend(f"  - {cmd}" for cmd in plan.verification)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan safe repo promotion for nightly audio-Autodata candidates.")
    parser.add_argument("promotion_candidates", type=Path, help="Path to Operations promotion_candidates.json")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH, help="Repo audio_repair_autodata_v0.json path")
    parser.add_argument("--json", action="store_true", help="Print the full machine-readable plan")
    parser.add_argument("--write-plan", type=Path, help="Optional path to write the full JSON plan")
    args = parser.parse_args(argv)

    plan = build_promotion_plan(args.promotion_candidates, cases_path=args.cases)
    plan_payload = plan.as_dict()
    if args.write_plan:
        args.write_plan.parent.mkdir(parents=True, exist_ok=True)
        args.write_plan.write_text(json.dumps(plan_payload, indent=2, sort_keys=True) + "\n")
    if args.json:
        print(json.dumps(plan_payload, indent=2, sort_keys=True))
    else:
        print(_format_text(plan))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
