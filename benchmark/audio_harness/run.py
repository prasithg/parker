"""CLI: score real audio clips end-to-end and emit a dated report.

Usage (via make):
    make eval-audio-real                  # whisper tiny over the manifest
    make eval-audio-real MODELS=tiny,small

For every usable clip this runs: local ASR -> utterance splitting ->
TextSession routing, and scores the result against the clip's clean
oracle-transcript path. Reports are aggregate and public-safe (clip ids
are dataset + content hash; no filesystem paths).
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any

if __package__ in (None, ""):  # running as a script
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from audio_harness import REPO_ROOT  # type: ignore
    from audio_harness.asr import ASRConfig, CachedASR  # type: ignore
    from audio_harness.manifest import artifacts_dir, load_clips  # type: ignore
    from audio_harness.replay import route_lines  # type: ignore
    from audio_harness.score import classify, wer  # type: ignore
else:
    from . import REPO_ROOT
    from .asr import ASRConfig, CachedASR
    from .manifest import artifacts_dir, load_clips
    from .replay import route_lines
    from .score import classify, wer

from app.voice.transcribe import split_utterances  # noqa: E402

DEFAULT_REPORTS_DIR = REPO_ROOT / "benchmark" / "reports"
REPORT_STEM = "audio_real_eval"


def evaluate_model(
    model: str,
    clips: list[Any],
    cache_root: Path,
    *,
    beam_size: int,
    initial_prompt: str | None,
    nbest_with: str | None = None,
) -> dict[str, Any]:
    config = ASRConfig(model_size=model, beam_size=beam_size, initial_prompt=initial_prompt)
    asr = CachedASR(config, cache_root)
    # Alternate hypotheses come from a second, cheaper model's transcript of
    # the same audio — cross-model disagreement as a poor man's n-best.
    nbest_asr = (
        CachedASR(ASRConfig(model_size=nbest_with, beam_size=beam_size), cache_root)
        if nbest_with and nbest_with != model
        else None
    )
    rows: list[dict[str, Any]] = []
    clean_cache: dict[str, Any] = {}
    for clip in clips:
        clean = clean_cache.get(clip.sha256)
        if clean is None:
            clean = route_lines(split_utterances([clip.oracle]))
            clean_cache[clip.sha256] = clean
        result = asr.transcribe(clip)
        lines = split_utterances(result.segments)
        norepair = route_lines(lines)
        if clean.captured and norepair.effect in ("choices", "captured"):
            with_repair = route_lines(lines, targets=clean.captured)
        else:
            with_repair = norepair
        verdict = classify(clean, norepair, with_repair)
        verdict["repair_nbest"] = verdict["repair"]
        if nbest_asr is not None and clean.captured and verdict["repair"] not in ("exact",):
            alternates = split_utterances(nbest_asr.transcribe(clip).segments)
            if alternates and alternates != lines:
                with_nbest = route_lines(lines, targets=clean.captured, alternates=alternates)
                verdict["repair_nbest"] = classify(clean, norepair, with_nbest)["repair"]
        rows.append(
            {
                "clip_id": clip.clip_id,
                "dataset": clip.dataset,
                "language": clip.language,
                "condition": clip.speaker_condition,
                "provenance": clip.provenance,
                "lane": verdict["lane"],
                "norepair": verdict["norepair"],
                "repair": verdict["repair"],
                "repair_nbest": verdict["repair_nbest"],
                "wer": round(wer(clip.oracle, result.text), 4),
                "asr_runtime_sec": round(result.runtime_sec, 3),
                "asr_empty": not result.text,
            }
        )
    return {"model": model, "config_key": config.key, "rows": rows, "live_asr_runs": asr.live_runs}


def _recovery(rows: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    intent = [r for r in rows if r["lane"] == "intent"]
    recovered = [r for r in intent if r[mode] in ("exact", "repair_recovered")]
    return {
        "intent_lane_clips": len(intent),
        "recovered": len(recovered),
        "recovery_rate": round(len(recovered) / len(intent), 4) if intent else None,
    }


def summarize(model_result: dict[str, Any]) -> dict[str, Any]:
    rows = model_result["rows"]
    by_mode = {mode: _recovery(rows, mode) for mode in ("norepair", "repair", "repair_nbest")}
    unsafe = {
        mode: sum(1 for r in rows if r[mode] == "unsafe_capture")
        for mode in ("norepair", "repair", "repair_nbest")
    }
    breakdowns: dict[str, Any] = {}
    for dim in ("condition", "language", "dataset"):
        breakdowns[dim] = {}
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            groups[row[dim]].append(row)
        for key, group in sorted(groups.items()):
            breakdowns[dim][key] = {
                "clips": len(group),
                "mean_wer": round(mean(r["wer"] for r in group), 4),
                "recovery_norepair": _recovery(group, "norepair")["recovery_rate"],
                "recovery_repair": _recovery(group, "repair")["recovery_rate"],
                "unsafe_repair": sum(1 for r in group if r["repair"] == "unsafe_capture"),
            }
    return {
        "model": model_result["model"],
        "clips_scored": len(rows),
        "recovery": by_mode,
        "unsafe_capture": unsafe,
        "category_counts": {
            mode: dict(Counter(r[mode] for r in rows))
            for mode in ("norepair", "repair", "repair_nbest")
        },
        # Mean WER is dominated by Whisper hallucination loops on hard clips
        # (WER >> 1 when a one-word oracle gets a paragraph of noise); median
        # is the honest central tendency. Both are reported.
        "mean_wer": round(mean(r["wer"] for r in rows), 4) if rows else None,
        "median_wer": round(median(r["wer"] for r in rows), 4) if rows else None,
        "mean_asr_runtime_sec": round(mean(r["asr_runtime_sec"] for r in rows), 3) if rows else None,
        "breakdowns": breakdowns,
    }


def format_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Real-audio eval — ASR -> TextSession route equivalence",
        "",
        f"Date (UTC): {payload['date']}  ",
        f"Clips scored: {payload['clips_scored']} (excluded: {payload['excluded']})  ",
        "Oracle: self-referential — route(oracle transcript) vs route(ASR transcript).",
        "",
        "| model | intent clips | recovery (no repair) | recovery (repair) | recovery (repair+n-best) | unsafe (worst mode) | median WER | mean WER | s/clip |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for summary in payload["models"]:
        rec = summary["recovery"]
        lines.append(
            f"| {summary['model']} | {rec['repair']['intent_lane_clips']} "
            f"| {rec['norepair']['recovery_rate']} | {rec['repair']['recovery_rate']} "
            f"| {rec['repair_nbest']['recovery_rate']} "
            f"| {max(summary['unsafe_capture'].values())} | {summary['median_wer']} "
            f"| {summary['mean_wer']} | {summary['mean_asr_runtime_sec']} |"
        )
    lines += [
        "",
        "Recovery is measured only on the intent lane (clips whose clean-path",
        "routing captures an action). Refusal/no-op lanes gate on unsafe",
        "captures instead. Per-condition, per-language, and per-dataset",
        "breakdowns are in the JSON report. Synthetic and public-corpus clips",
        "are both included; treat dysarthric-English coverage caveats in the",
        "breakdowns as binding when citing numbers.",
        "",
        f"Gate (0 unsafe captures in every mode, all models): "
        f"{'PASS' if payload['gate']['passed'] else 'FAIL'}",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", default="tiny")
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument(
        "--extra-manifest",
        type=Path,
        action="append",
        default=[],
        help="additional manifest file(s) in the artifacts dir to merge in",
    )
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--initial-prompt", default=None)
    parser.add_argument(
        "--nbest-with",
        default=None,
        help="second model whose transcript supplies alternate hypotheses for repair",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--reports-dir", type=Path, default=DEFAULT_REPORTS_DIR)
    parser.add_argument(
        "--include-private",
        action="store_true",
        help=(
            "include private-provenance clips (web-private, pilot-consented); "
            "forces reports into the Operations artifacts dir, never the repo"
        ),
    )
    args = parser.parse_args()

    cache_root = artifacts_dir()
    if args.include_private:
        # A run that saw private data may not write into the repo. Ever.
        args.reports_dir = cache_root / "reports_private"
        print(f"[private run] reports redirected to {args.reports_dir}")

    clips, excluded = load_clips(args.manifest, include_private=args.include_private)
    seen_hashes = {clip.sha256 for clip in clips}
    for extra in args.extra_manifest:
        extra_path = extra if extra.is_absolute() else cache_root / extra
        extra_clips, extra_excluded = load_clips(extra_path, include_private=args.include_private)
        fresh = [c for c in extra_clips if c.sha256 not in seen_hashes]
        seen_hashes.update(c.sha256 for c in fresh)
        clips.extend(fresh)
        for key, count in extra_excluded.items():
            excluded[key] = excluded.get(key, 0) + count
    if args.limit:
        clips = clips[: args.limit]
    print(f"Scoring {len(clips)} clips (excluded: {excluded})")

    models_payload = []
    per_clip: dict[str, list[dict[str, Any]]] = {}
    for model in [m.strip() for m in args.models.split(",") if m.strip()]:
        result = evaluate_model(
            model,
            clips,
            cache_root,
            beam_size=args.beam_size,
            initial_prompt=args.initial_prompt,
            nbest_with=args.nbest_with,
        )
        summary = summarize(result)
        models_payload.append(summary)
        per_clip[model] = result["rows"]
        rec = summary["recovery"]
        print(
            f"  {model}: recovery {rec['norepair']['recovery_rate']} -> "
            f"{rec['repair']['recovery_rate']} with repair -> "
            f"{rec['repair_nbest']['recovery_rate']} with n-best; "
            f"unsafe {summary['unsafe_capture']} ; median WER {summary['median_wer']} "
            f"({result['live_asr_runs']} live ASR runs)"
        )

    date = datetime.now(timezone.utc).date().isoformat()
    payload = {
        "date": date,
        "clips_scored": len(clips),
        "contains_private_data": args.include_private,
        "excluded": excluded,
        "models": models_payload,
        "per_clip": per_clip,
        "gate": {
            "passed": all(max(s["unsafe_capture"].values()) == 0 for s in models_payload),
            "rule": "0 unsafe captures in every mode for every model",
        },
    }
    if args.write_report:
        args.reports_dir.mkdir(parents=True, exist_ok=True)
        for stem in (f"{REPORT_STEM}_{date}", f"{REPORT_STEM}_latest"):
            (args.reports_dir / f"{stem}.json").write_text(json.dumps(payload, indent=2) + "\n")
            (args.reports_dir / f"{stem}.md").write_text(format_markdown(payload))
        print(f"Reports written to {args.reports_dir}/{REPORT_STEM}_{date}.{{json,md}}")
    if not payload["gate"]["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
