"""Audio-derived Parker Autodata fixture evaluator.

This evaluator is intentionally deterministic and metadata-only. It does not
read raw audio; the nightly Operations lane runs local ASR and stores selected
public-safe hypotheses here as fixtures. The gate checks whether the data unit
has the shape Parker needs for audio -> ASR -> repair/confirm -> safe action or
safe no-action learning.

Usage:
    python3 benchmark/evaluate_audio_repair_autodata_v0.py
    python3 benchmark/evaluate_audio_repair_autodata_v0.py --json
    python3 benchmark/evaluate_audio_repair_autodata_v0.py --write-report
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES_PATH = REPO_ROOT / "benchmark" / "data" / "audio_repair_autodata_v0.json"
DEFAULT_REPORTS_DIR = REPO_ROOT / "benchmark" / "reports"

SOURCE_TYPES = {"synthetic_audio_derived", "public_corpus_audio_derived"}
SAFE_FINAL_ACTIONS = {None, "reminder", "family_message", "exercise_start", "media_playlist"}
SIDE_EFFECT_ACTIONS = {"reminder", "family_message", "exercise_start", "media_playlist"}
PROHIBITED_ACTIONS = {"medication_change", "medical_advice", "emergency_response", "privacy_disclosure", "purchase"}


@dataclass(frozen=True)
class AudioAutodataCase:
    """One audio-derived repair/autodata fixture."""

    case_id: str
    source_type: str
    clean_phrase: str
    asr_hypotheses: list[str]
    clean_intent: dict[str, Any]
    weak_current: dict[str, Any]
    strong_oracle: dict[str, Any]
    repair_target: dict[str, Any]
    final_confirmed_action: dict[str, Any]
    safety: dict[str, Any]
    judge: dict[str, Any]
    confusion_pairs: list[str]
    provenance: dict[str, Any]
    scenario: dict[str, Any]
    rubric: dict[str, float]
    source_oracle: dict[str, Any]

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "AudioAutodataCase":
        required = {
            "case_id",
            "source_type",
            "provenance",
            "scenario",
            "clean_phrase",
            "clean_intent",
            "asr_hypotheses",
            "weak_current",
            "strong_oracle",
            "repair_target",
            "final_confirmed_action",
            "safety",
            "rubric",
            "judge",
            "confusion_pairs",
        }
        missing = required - set(row)
        case_id = str(row.get("case_id", "<unknown>"))
        if missing:
            raise ValueError(f"audio-autodata case {case_id} missing fields: {sorted(missing)}")
        source_type = str(row["source_type"])
        if source_type not in SOURCE_TYPES:
            raise ValueError(f"audio-autodata case {case_id} invalid source_type: {source_type}")
        if not isinstance(row["asr_hypotheses"], list):
            raise ValueError(f"audio-autodata case {case_id} asr_hypotheses must be a list")
        if not row["asr_hypotheses"] and row["weak_current"].get("result") != "empty_asr":
            raise ValueError(f"audio-autodata case {case_id} empty ASR must be labelled result=empty_asr")
        for object_field in (
            "provenance",
            "scenario",
            "clean_intent",
            "weak_current",
            "strong_oracle",
            "repair_target",
            "final_confirmed_action",
            "safety",
            "judge",
            "rubric",
        ):
            if not isinstance(row[object_field], dict):
                raise ValueError(f"audio-autodata case {case_id} {object_field} must be an object")
        source_oracle = row.get("source_oracle", {})
        if not isinstance(source_oracle, dict):
            raise ValueError(f"audio-autodata case {case_id} source_oracle must be an object when present")
        if not isinstance(row["confusion_pairs"], list) or not row["confusion_pairs"]:
            raise ValueError(f"audio-autodata case {case_id} needs confusion_pairs")
        return cls(
            case_id=case_id,
            source_type=source_type,
            clean_phrase=str(row["clean_phrase"]),
            asr_hypotheses=[str(item) for item in row["asr_hypotheses"]],
            clean_intent=row["clean_intent"],
            weak_current=row["weak_current"],
            strong_oracle=row["strong_oracle"],
            repair_target=row["repair_target"],
            final_confirmed_action=row["final_confirmed_action"],
            safety=row["safety"],
            judge=row["judge"],
            confusion_pairs=[str(item) for item in row["confusion_pairs"]],
            provenance=row["provenance"],
            scenario=row["scenario"],
            rubric={str(key): float(value) for key, value in row["rubric"].items()},
            source_oracle=source_oracle,
        )

    @property
    def accepted(self) -> bool:
        return bool(self.judge.get("accepted"))

    @property
    def final_action_type(self) -> str | None:
        value = self.final_confirmed_action.get("action_type")
        return None if value is None else str(value)

    @property
    def safety_label(self) -> str:
        return str(self.safety.get("label", ""))


@dataclass(frozen=True)
class HeldAudioAutodataCandidate:
    """A useful audio-derived row that is intentionally not an accepted fixture yet."""

    candidate_id: str
    source_type: str
    provenance: dict[str, Any]
    source_transcript: str
    source_intent_class: str
    oracle_target: str
    asr_hypotheses: list[str]
    observed_weak_current: dict[str, Any]
    hold_reason: str
    promotion_blocker: str
    safety_label: str
    confusion_pairs: list[str]

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "HeldAudioAutodataCandidate":
        required = {
            "candidate_id",
            "source_type",
            "provenance",
            "source_transcript",
            "source_intent_class",
            "oracle_target",
            "asr_hypotheses",
            "observed_weak_current",
            "hold_reason",
            "promotion_blocker",
            "safety_label",
            "confusion_pairs",
        }
        candidate_id = str(row.get("candidate_id", "<unknown>"))
        missing = required - set(row)
        if missing:
            raise ValueError(f"held audio-autodata candidate {candidate_id} missing fields: {sorted(missing)}")
        source_type = str(row["source_type"])
        if source_type not in SOURCE_TYPES:
            raise ValueError(f"held audio-autodata candidate {candidate_id} invalid source_type: {source_type}")
        if not isinstance(row["provenance"], dict):
            raise ValueError(f"held audio-autodata candidate {candidate_id} provenance must be an object")
        if source_type == "public_corpus_audio_derived" and not row["provenance"].get("source_url"):
            raise ValueError(f"held audio-autodata candidate {candidate_id} needs a source_url")
        if not isinstance(row["asr_hypotheses"], list) or not row["asr_hypotheses"]:
            raise ValueError(f"held audio-autodata candidate {candidate_id} needs ASR hypotheses")
        if not isinstance(row["observed_weak_current"], dict):
            raise ValueError(f"held audio-autodata candidate {candidate_id} observed_weak_current must be an object")
        if not isinstance(row["confusion_pairs"], list) or not row["confusion_pairs"]:
            raise ValueError(f"held audio-autodata candidate {candidate_id} needs confusion_pairs")
        if row.get("private_data", "none") != "none":
            raise ValueError(f"held audio-autodata candidate {candidate_id} must contain no private data")
        return cls(
            candidate_id=candidate_id,
            source_type=source_type,
            provenance=row["provenance"],
            source_transcript=str(row["source_transcript"]),
            source_intent_class=str(row["source_intent_class"]),
            oracle_target=str(row["oracle_target"]),
            asr_hypotheses=[str(item) for item in row["asr_hypotheses"]],
            observed_weak_current=row["observed_weak_current"],
            hold_reason=str(row["hold_reason"]),
            promotion_blocker=str(row["promotion_blocker"]),
            safety_label=str(row["safety_label"]),
            confusion_pairs=[str(item) for item in row["confusion_pairs"]],
        )


@dataclass(frozen=True)
class AudioAutodataEvalResult:
    """Aggregate audio-autodata fixture gate."""

    cases: list[AudioAutodataCase]
    validation_failures: list[dict[str, str]]
    held_candidates: list[HeldAudioAutodataCandidate] = field(default_factory=list)

    def metrics(self) -> dict[str, Any]:
        total = len(self.cases)
        accepted = [case for case in self.cases if case.accepted]
        public = [case for case in self.cases if case.source_type == "public_corpus_audio_derived"]
        synthetic = [case for case in self.cases if case.source_type == "synthetic_audio_derived"]
        hard_negative = [
            case
            for case in self.cases
            if "hard_negative" in case.safety_label or case.final_action_type is None
        ]
        safety_critical = [
            case
            for case in self.cases
            if "safety_critical" in case.safety_label or "health_adjacent" in case.safety_label
        ]
        weak_useful_failures = [case for case in self.cases if case.weak_current.get("useful_failure")]
        strong_recovered = [
            case
            for case in self.cases
            if case.strong_oracle.get("result") in {"recovered", "safe_no_action"}
        ]
        source_oracle_cases = [case for case in self.cases if case.source_oracle]
        runtime_vs_source_oracle_disagreements = [
            case
            for case in source_oracle_cases
            if case.weak_current.get("result") not in {"safe_no_action", "noop", "refused", "context_required"}
            and case.source_oracle.get("oracle_target") in {"safe_no_action", "safe_no_action_alternate_input"}
        ]
        unsafe_accepted = [
            case
            for case in accepted
            if case.final_action_type in PROHIBITED_ACTIONS or case.safety.get("medical_claim") is True
        ]
        side_effects_confirmed = [
            case
            for case in self.cases
            if case.final_action_type in SIDE_EFFECT_ACTIONS
            and case.final_confirmed_action.get("requires_confirmation") is True
        ]
        return {
            "total_cases": total,
            "accepted_cases": len(accepted),
            "held_candidates": len(self.held_candidates),
            "synthetic_audio_derived_cases": len(synthetic),
            "public_corpus_audio_derived_cases": len(public),
            "hard_negative_or_no_action_cases": len(hard_negative),
            "safety_critical_or_health_adjacent_cases": len(safety_critical),
            "source_oracle_cases": len(source_oracle_cases),
            "runtime_vs_source_oracle_disagreements": len(runtime_vs_source_oracle_disagreements),
            "weak_current_useful_failures": len(weak_useful_failures),
            "strong_oracle_recovered_or_safe_no_action": len(strong_recovered),
            "side_effect_cases_with_confirmation": len(side_effects_confirmed),
            "unsafe_accepted_cases": len(unsafe_accepted),
            "validation_failures": len(self.validation_failures),
        }

    def gate(self) -> dict[str, Any]:
        metrics = self.metrics()
        checks = {
            "has_minimum_case_count": metrics["total_cases"] >= 8,
            "has_synthetic_audio_lane": metrics["synthetic_audio_derived_cases"] >= 5,
            "has_public_audio_lane": metrics["public_corpus_audio_derived_cases"] >= 3,
            "has_hard_negatives": metrics["hard_negative_or_no_action_cases"] >= 3,
            "has_useful_weak_failures": metrics["weak_current_useful_failures"] >= 6,
            "strong_oracle_labels_all_cases": metrics["strong_oracle_recovered_or_safe_no_action"] == metrics["total_cases"],
            "no_unsafe_accepted_cases": metrics["unsafe_accepted_cases"] == 0,
            "schema_validation_clean": metrics["validation_failures"] == 0,
        }
        return {
            "passed": all(checks.values()),
            "checks": checks,
            "blocking_failures": [name for name, passed in checks.items() if not passed],
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "eval": "audio_repair_autodata_v0",
            "provenance": {
                "private_data": "none",
                "fixture_policy": "metadata-only public/synthetic audio-derived cases; raw audio not committed",
                "model_or_api_dependency": "none for evaluator; nightly ASR was local faster-whisper",
                "claim_status": "pipeline/autodata fixture coverage only; not real-world, clinical, or patient-performance evidence",
            },
            "metrics": self.metrics(),
            "gate": self.gate(),
            "validation_failures": self.validation_failures,
            "held_candidates": [
                {
                    "candidate_id": candidate.candidate_id,
                    "source_type": candidate.source_type,
                    "source_transcript": candidate.source_transcript,
                    "source_intent_class": candidate.source_intent_class,
                    "oracle_target": candidate.oracle_target,
                    "asr_hypotheses": candidate.asr_hypotheses,
                    "observed_weak_current_result": candidate.observed_weak_current.get("result"),
                    "hold_reason": candidate.hold_reason,
                    "promotion_blocker": candidate.promotion_blocker,
                    "safety_label": candidate.safety_label,
                    "confusion_pairs": candidate.confusion_pairs,
                }
                for candidate in self.held_candidates
            ],
            "cases": [
                {
                    "case_id": case.case_id,
                    "source_type": case.source_type,
                    "clean_phrase": case.clean_phrase,
                    "asr_hypotheses": case.asr_hypotheses,
                    "weak_current_result": case.weak_current.get("result"),
                    "strong_oracle_result": case.strong_oracle.get("result"),
                    "final_action_type": case.final_action_type,
                    "safety_label": case.safety_label,
                    "source_oracle_target": case.source_oracle.get("oracle_target") if case.source_oracle else None,
                    "accepted": case.accepted,
                    "judge_label": case.judge.get("label"),
                    "confusion_pairs": case.confusion_pairs,
                }
                for case in self.cases
            ],
        }


def _read_payload(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("cases"), list):
        raise ValueError(f"{path}: expected an object with a cases array")
    if parsed.get("private_data") != "none":
        raise ValueError(f"{path}: private_data must be 'none'")
    _reject_local_paths(parsed)
    return parsed


def load_cases(path: Path = DEFAULT_CASES_PATH) -> list[AudioAutodataCase]:
    parsed = _read_payload(path)
    cases = [AudioAutodataCase.from_dict(row) for row in parsed["cases"]]
    seen: set[str] = set()
    for case in cases:
        if case.case_id in seen:
            raise ValueError(f"duplicate audio-autodata case_id: {case.case_id}")
        seen.add(case.case_id)
    return cases


def load_held_candidates(path: Path = DEFAULT_CASES_PATH) -> list[HeldAudioAutodataCandidate]:
    parsed = _read_payload(path)
    raw = parsed.get("held_candidates", [])
    if not isinstance(raw, list):
        raise ValueError(f"{path}: held_candidates must be a list when present")
    candidates = [HeldAudioAutodataCandidate.from_dict(row) for row in raw]
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.candidate_id in seen:
            raise ValueError(f"duplicate held audio-autodata candidate_id: {candidate.candidate_id}")
        seen.add(candidate.candidate_id)
    return candidates


def evaluate(
    cases: list[AudioAutodataCase],
    held_candidates: list[HeldAudioAutodataCandidate] | None = None,
) -> AudioAutodataEvalResult:
    failures: list[dict[str, str]] = []
    for case in cases:
        failures.extend(_case_failures(case))
    return AudioAutodataEvalResult(cases=cases, validation_failures=failures, held_candidates=held_candidates or [])


def _case_failures(case: AudioAutodataCase) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    final_action = case.final_action_type
    if final_action not in SAFE_FINAL_ACTIONS:
        failures.append({"case_id": case.case_id, "check": "known_safe_action", "message": f"unsupported final action {final_action!r}"})
    if final_action in SIDE_EFFECT_ACTIONS and case.final_confirmed_action.get("requires_confirmation") is not True:
        failures.append({"case_id": case.case_id, "check": "confirmation_required", "message": "side-effect action lacks confirmation gate"})
    if final_action == "family_message" and case.final_confirmed_action.get("external_send") is not False:
        failures.append({"case_id": case.case_id, "check": "no_external_send", "message": "family message must stay local-only"})
    if case.safety.get("private_data") != "none":
        failures.append({"case_id": case.case_id, "check": "private_data", "message": "fixture must contain no private data"})
    if case.safety.get("medical_claim") is not False:
        failures.append({"case_id": case.case_id, "check": "medical_claim", "message": "fixture may not make medical claims"})
    if case.accepted and final_action in PROHIBITED_ACTIONS:
        failures.append({"case_id": case.case_id, "check": "prohibited_action", "message": "accepted fixture has prohibited final action"})
    choices = case.repair_target.get("choices")
    if not isinstance(choices, list) or len(choices) < 2 or not any("none of these" == str(choice).lower() for choice in choices):
        failures.append({"case_id": case.case_id, "check": "repair_choices", "message": "repair target must include choices plus none of these"})
    if abs(sum(case.rubric.values()) - 1.0) > 0.001:
        failures.append({"case_id": case.case_id, "check": "rubric_sum", "message": "rubric weights must sum to 1.0"})
    if case.source_type == "public_corpus_audio_derived" and not case.provenance.get("source_url"):
        failures.append({"case_id": case.case_id, "check": "source_url", "message": "public audio-derived cases need a source URL"})
    if case.source_oracle:
        required_oracle = {"source_transcript", "source_intent_class", "oracle_target", "runtime_text_guard_allowed", "promotion_policy"}
        missing_oracle = required_oracle - set(case.source_oracle)
        if missing_oracle:
            failures.append({"case_id": case.case_id, "check": "source_oracle_fields", "message": f"source_oracle missing {sorted(missing_oracle)}"})
        if case.source_type != "public_corpus_audio_derived":
            failures.append({"case_id": case.case_id, "check": "source_oracle_public_only", "message": "source-oracle cases should come from public corpus audio"})
        if final_action is not None:
            failures.append({"case_id": case.case_id, "check": "source_oracle_no_action", "message": "source-oracle safety holds must not commit a final action"})
        if case.source_oracle.get("runtime_text_guard_allowed") is not False:
            failures.append({"case_id": case.case_id, "check": "source_oracle_no_broad_guard", "message": "source-oracle cases must explicitly avoid broad runtime text rules"})
    return failures


def _reject_local_paths(value: Any) -> None:
    """Keep public repo fixtures metadata-only, without local absolute paths."""

    if isinstance(value, dict):
        for item in value.values():
            _reject_local_paths(item)
    elif isinstance(value, list):
        for item in value:
            _reject_local_paths(item)
    elif isinstance(value, str) and (value.startswith("/Users/") or value.startswith("file:///")):
        raise ValueError("audio-autodata fixtures must not include local absolute paths")


def format_summary(result: AudioAutodataEvalResult) -> str:
    metrics = result.metrics()
    gate = result.gate()
    lines = [
        "Parker audio repair Autodata eval v0",
        "",
        f"Cases: {metrics['total_cases']} metadata-only audio-derived fixtures",
        f"Accepted fixtures: {metrics['accepted_cases']}/{metrics['total_cases']}",
        f"Held candidates (not counted as accepted): {metrics['held_candidates']}",
        f"Synthetic audio-derived: {metrics['synthetic_audio_derived_cases']}",
        f"Public corpus audio-derived: {metrics['public_corpus_audio_derived_cases']}",
        f"Hard negative/no-action: {metrics['hard_negative_or_no_action_cases']}",
        f"Source-oracle holds: {metrics['source_oracle_cases']}",
        f"Runtime/source-oracle disagreements: {metrics['runtime_vs_source_oracle_disagreements']}",
        f"Weak/current useful failures: {metrics['weak_current_useful_failures']}",
        f"Unsafe accepted cases: {metrics['unsafe_accepted_cases']}",
        f"Gate passed: {gate['passed']}",
        "",
        "Caveat: metadata-only pipeline fixture coverage; not real-world clinical, patient, or ASR-performance proof.",
    ]
    if gate["blocking_failures"]:
        lines.append(f"Blocking failures: {', '.join(gate['blocking_failures'])}")
    return "\n".join(lines)


def format_markdown_report(result: AudioAutodataEvalResult, run_date: str) -> str:
    payload = result.as_dict()
    metrics = payload["metrics"]
    gate = payload["gate"]
    lines = [
        "# Parker audio repair Autodata eval v0",
        "",
        f"- Date: {run_date}",
        "- Provenance: metadata-only public/synthetic audio-derived fixtures; no private family/patient data; raw audio not committed.",
        "- Purpose: keep Parker's Autodata lane tied to audio -> ASR -> repair/confirm -> safe action/no-action data units.",
        "- Caveat: pipeline fixture coverage only; not clinical, patient, real-world, or population evidence.",
        "",
        "## Metrics",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
    ]
    for key, value in metrics.items():
        lines.append(f"| {key} | {value} |")
    lines.extend(["", "## Gate", "", f"- Passed: `{gate['passed']}`", ""])
    for name, passed in gate["checks"].items():
        lines.append(f"- {'PASS' if passed else 'FAIL'} `{name}`")
    lines.extend(["", "## Case breakdown", ""])
    for case in payload["cases"]:
        hypotheses = "; ".join(case["asr_hypotheses"]) if case["asr_hypotheses"] else "<empty ASR>"
        source_note = f"; source_oracle={case['source_oracle_target']}" if case["source_oracle_target"] else ""
        lines.append(
            f"- `{case['case_id']}` ({case['source_type']}): ASR={hypotheses!r}; "
            f"weak={case['weak_current_result']}; oracle={case['strong_oracle_result']}; "
            f"final={case['final_action_type']}; safety={case['safety_label']}{source_note}; accepted={case['accepted']}"
        )
    if payload["held_candidates"]:
        lines.extend(["", "## Held candidate notes", ""])
        lines.append(
            "These audio-derived rows are useful learnings but are intentionally not counted as accepted fixtures until their promotion blocker is resolved."
        )
        lines.append("")
        for candidate in payload["held_candidates"]:
            hypotheses = "; ".join(candidate["asr_hypotheses"])
            lines.append(
                f"- `{candidate['candidate_id']}` ({candidate['source_type']}): "
                f"source={candidate['source_transcript']!r}; ASR={hypotheses!r}; "
                f"weak={candidate['observed_weak_current_result']}; safety={candidate['safety_label']}; "
                f"hold={candidate['hold_reason']}; blocker={candidate['promotion_blocker']}"
            )
    lines.append("")
    return "\n".join(lines)


def write_report(result: AudioAutodataEvalResult, reports_dir: Path = DEFAULT_REPORTS_DIR) -> list[Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    run_date = date.today().isoformat()
    markdown = format_markdown_report(result, run_date)
    payload = {"date": run_date, **result.as_dict()}
    written: list[Path] = []
    for stem in ("audio_repair_autodata_eval_latest", f"audio_repair_autodata_eval_{run_date}"):
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
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--json", action="store_true", help="Print JSON instead of text summary")
    parser.add_argument("--write-report", action="store_true", help="Write markdown+JSON reports to benchmark/reports")
    parser.add_argument("--reports-dir", type=Path, default=DEFAULT_REPORTS_DIR)
    args = parser.parse_args()

    result = evaluate(load_cases(args.cases), held_candidates=load_held_candidates(args.cases))
    if args.json:
        print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    else:
        print(format_summary(result))
    if args.write_report:
        for path in write_report(result, args.reports_dir):
            print(f"wrote {_display_path(path)}")


if __name__ == "__main__":
    main()
