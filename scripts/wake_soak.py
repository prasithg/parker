"""Ambient-TV wake soak: CPU, latency, false wakes, and a recall matrix.

Runs OUTSIDE the keyless test suite against the REAL local faster-whisper
model — the same WakeDetector the companion's wake lane uses, fed
browser-sized 16 kHz frames. Two parts:

1. **Soak**: a long TV-like track (several macOS `say` voices reading
   news/sports/ad copy stuffed with Parker-adjacent words: parking, parked,
   Parker Brothers, Peter Parker, darker, marker, packer, barker) streams
   through the detector. Every detection is a FALSE wake (no greeting +
   Parker phrase exists in the track). Reports inference count per minute
   of audio, CPU seconds per audio minute, inference p50/p95.
2. **Recall matrix**: effortful positives (`hey parker`, `hey... parker`,
   `hey parka`, `hey par ker`, `hi parker`, `hey parker can you help me`)
   across voices and a slow rate must wake; the review's confusable
   negatives (`hey darker`, `hey marker`, `hey barker`, `hey packer`,
   `hey parked`, `a parker`) are REPORTED, not gated — chairman decision
   2026-09-01: calibrate for Dad's recall, an occasional extra wake while
   dormant costs only a perk-up.

Usage: backend/.venv/bin/python scripts/wake_soak.py [--minutes 4]
Writes benchmark/reports/wake_soak_<date>.{json,md} (aggregate only —
synthesized speech, no private audio). Exit 0 unless a positive missed or
the soak produced a false wake; the confusable rows are informational.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
import time
import wave
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.parker.wake import WAKE_SAMPLE_RATE, WakeDetector  # noqa: E402
from app.voice.transcribe import load_local_transcriber  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
REPORTS = REPO / "benchmark" / "reports"
FRAME_SAMPLES = 1365  # one browser ScriptProcessor callback resampled to 16 kHz

TV_VOICES = ["Samantha", "Daniel", "Karen", "Fred", "Moira"]
TV_LINES = [
    "Good evening. The parking garage downtown will close for repairs on Monday, "
    "and drivers who parked overnight should move their cars by six.",
    "In tennis, the second seed booked her place in the quarterfinals after a "
    "marathon three-setter that finished well after dark, darker still by the "
    "time the crowd left Arthur Ashe.",
    "Coming up: the Parker Brothers board game that made Monopoly a household "
    "name, and why Peter Parker never learned to park a car.",
    "Sports now. The Packers held on in the fourth quarter. Their kicker, a "
    "former marker of the trade, made every field goal.",
    "Weather: a marker of the changing season, tonight's low will feel darker "
    "and colder than yesterday. Bring a parka if you are out late.",
    "Local news: a barking dog at Barker Street kept neighbours awake; the "
    "owner, Mr. Barker, apologised. Park rangers said the park stays open.",
    "Our sponsor: Parker pens. Write like you mean it. Available at every "
    "department store and the park kiosk.",
    "Traffic is heavy near the parkway. A parked truck blocked two lanes near "
    "the market. Expect delays of ten minutes.",
    "And finally, tonight's film: an old western, darker in tone than its "
    "poster, starring an actor named Parker something.",
    "Hey, folks, that's the news. Hey, stay warm tonight. Hi to everyone "
    "watching from the park. Hay fever season is here, doctors say.",
]

POSITIVES = [
    ("hey parker", None),
    ("hey, parker.", None),
    ("hey... parker", None),
    ("hey parka", None),
    ("hey par ker", None),
    ("hi parker", None),
    ("hey parker, can you help me", None),
    ("um, hey parker", None),
]
CONFUSABLES = [
    "hey darker",
    "hey marker",
    "hey barker",
    "hey packer",
    "hey parked",
    "a parker",
    "hey partner",
    "hey, the parking lot is full",
    "peter parker was here",
    "the parker brothers game",
]
RECALL_VOICES = ["Samantha", "Daniel", "Fred"]
RECALL_RATES = [175, 120]  # words per minute: normal and slow/effortful


def synth(text: str, out_dir: Path, voice: str | None = None, rate: int | None = None) -> Path:
    aiff = out_dir / "utterance.aiff"
    wav = out_dir / "utterance.wav"
    cmd = ["say", "-o", str(aiff)]
    if voice:
        cmd += ["-v", voice]
    if rate:
        cmd += ["-r", str(rate)]
    subprocess.run(cmd + [text], check=True, capture_output=True)
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(wav)],
        check=True,
        capture_output=True,
    )
    return wav


def read_pcm(wav_path: Path) -> bytes:
    with wave.open(str(wav_path), "rb") as handle:
        assert handle.getframerate() == WAKE_SAMPLE_RATE, handle.getframerate()
        return handle.readframes(handle.getnframes())


def build_tv_track(minutes: float, out_dir: Path) -> bytes:
    target = int(minutes * 60 * WAKE_SAMPLE_RATE) * 2
    pcm = bytearray()
    index = 0
    gap = b"\x00\x00" * int(0.35 * WAKE_SAMPLE_RATE)
    while len(pcm) < target:
        line = TV_LINES[index % len(TV_LINES)]
        voice = TV_VOICES[index % len(TV_VOICES)]
        with tempfile.TemporaryDirectory() as tmp:
            pcm += read_pcm(synth(line, Path(tmp), voice=voice)) + gap
        index += 1
    return bytes(pcm[:target])


def stream(detector: WakeDetector, pcm: bytes, timings: list[float] | None = None):
    hits = []
    for start in range(0, len(pcm), FRAME_SAMPLES * 2):
        before = detector.inferences
        t0 = time.perf_counter()
        result = detector.feed(pcm[start : start + FRAME_SAMPLES * 2])
        if timings is not None and detector.inferences > before:
            timings.append((time.perf_counter() - t0) * 1000)
        if result:
            hits.append({"matched": result["matched"], "heard": result["heard"], "tail": result["tail"]})
    return hits


def mix_over(voice: bytes, background: bytes, snr_db: float) -> bytes:
    """The voice on top of TV audio, scaled so voice RMS / TV RMS = snr_db.
    2 s of TV lead-in (so the adaptive gate has a background), then the
    mix, then 1 s of TV tail."""

    import audioop

    lead = int(2.0 * WAKE_SAMPLE_RATE) * 2
    tail = int(1.6 * WAKE_SAMPLE_RATE) * 2
    need = lead + len(voice) + tail
    bed = (background * (need // len(background) + 1))[:need]
    bed_rms = max(1, audioop.rms(bed, 2))
    voice_rms = max(1, audioop.rms(voice, 2))
    factor = (bed_rms * (10 ** (snr_db / 20))) / voice_rms
    scaled = audioop.mul(voice, 2, min(factor, 3.0))
    padded = b"\x00" * lead + scaled + b"\x00" * tail
    return audioop.add(bed, padded, 2)


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = min(len(ordered) - 1, max(0, int(round((p / 100) * (len(ordered) - 1)))))
    return ordered[k]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=4.0)
    parser.add_argument("--model", default="base", help="faster-whisper size for the wake lane")
    parser.add_argument("--threads", type=int, default=0, help="cpu_threads cap (0 = library default)")
    parser.add_argument("--hop", type=float, default=0.7, help="seconds of new audio per inference")
    parser.add_argument("--relative-gate", type=float, default=0.0, help="run only when a hop is this many times louder than the trailing median (0 = off)")
    parser.add_argument("--tag", default="", help="report name suffix")
    parser.add_argument("--skip-soak", action="store_true", help="only the recall/over-TV matrices")
    parser.add_argument("--skip-recall", action="store_true", help="only the soak and over-TV rows")
    parser.add_argument("--overlay-snrs", default="0,-6", help="voice/TV SNRs in dB for the over-TV rows")
    args = parser.parse_args()

    def make_detector(t):
        return WakeDetector(t, hop_seconds=args.hop, relative_gate=args.relative_gate)

    print(f"loading local transcriber (faster-whisper {args.model}, threads={args.threads or 'auto'})…", flush=True)
    transcriber = load_local_transcriber(model_size=args.model, cpu_threads=args.threads)

    # ---- 1. ambient-TV soak -------------------------------------------------
    soak_minutes = 0.0 if args.skip_soak else args.minutes
    print(f"synthesizing {soak_minutes:.1f} min of TV-like speech…", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        track = build_tv_track(soak_minutes, Path(tmp)) if soak_minutes else b""
    audio_minutes = max(1e-6, len(track) / 2 / WAKE_SAMPLE_RATE / 60)
    detector = make_detector(transcriber)
    timings: list[float] = []
    cpu0, wall0 = time.process_time(), time.perf_counter()
    false_wakes = stream(detector, track, timings) if track else []
    cpu, wall = time.process_time() - cpu0, time.perf_counter() - wall0
    soak = {
        "audio_minutes": round(audio_minutes, 2),
        "inferences": detector.inferences,
        "inferences_per_audio_minute": round(detector.inferences / audio_minutes, 1),
        "cpu_seconds_per_audio_minute": round(cpu / audio_minutes, 2),
        "cpu_fraction_of_realtime": round(cpu / (audio_minutes * 60), 3),
        "wall_seconds": round(wall, 1),
        "infer_ms_p50": round(pct(timings, 50), 1),
        "infer_ms_p95": round(pct(timings, 95), 1),
        "infer_ms_max": round(max(timings), 1) if timings else 0.0,
        "false_wakes": false_wakes,
        "hops_gated_by_background": detector.gated_by_background,
        "config": {"model": args.model, "threads": args.threads, "hop": args.hop, "relative_gate": args.relative_gate},
    }
    print(
        f"soak: {audio_minutes:.1f} min audio, {detector.inferences} inferences, "
        f"cpu {cpu:.1f}s ({soak['cpu_fraction_of_realtime']:.0%} of realtime), "
        f"p50 {soak['infer_ms_p50']} ms, p95 {soak['infer_ms_p95']} ms, "
        f"false wakes {len(false_wakes)}",
        flush=True,
    )
    for hit in false_wakes:
        print("  FALSE WAKE:", hit)

    # ---- 2. recall matrix ---------------------------------------------------
    def recall_case(text, voice, rate, *, background: bytes | None = None, snr_db: float = 0.0):
        """One positive: optionally mixed over TV audio at snr_db (voice
        relative to TV). Returns (woke, transcripts seen, tail after wake)
        — the post-wake tail lane is simulated by feeding on for 3 s."""

        with tempfile.TemporaryDirectory() as tmp:
            pcm = read_pcm(synth(text, Path(tmp), voice=voice, rate=rate))
        silence = b"\x00\x00" * (WAKE_SAMPLE_RATE // 2)
        # A live microphone keeps streaming after he stops talking, so the
        # detector always gets a hop with the whole phrase in the window;
        # trailing silence shorter than one hop (0.7 s) would cut the final
        # inference mid-word ("hey park|er") — a harness artifact, not the
        # lane's behaviour. Pad two hops.
        trailing = b"\x00\x00" * int(1.6 * WAKE_SAMPLE_RATE)
        if background is None:
            audio = silence + pcm + trailing
        else:
            audio = mix_over(pcm, background, snr_db)
        det = make_detector(transcriber)
        seen: list[str] = []
        orig = det._transcriber

        def spy(path):
            lines = orig(path)
            seen.append(" ".join(l.strip() for l in lines if l and l.strip()))
            return lines

        det._transcriber = spy
        hit = None
        tail_after = ""
        for start in range(0, len(audio), FRAME_SAMPLES * 2):
            frame = audio[start : start + FRAME_SAMPLES * 2]
            if hit is None:
                result = det.feed(frame)
                if result:
                    hit = result
                    woke_at = start
            else:
                if start - woke_at > 3 * WAKE_SAMPLE_RATE * 2:
                    break
                heard = det.hear(frame)
                if heard and heard["heard"]:
                    tail_after = heard["heard"]
        return hit, seen, tail_after

    recall_rows = []
    misses = 0
    for text, _ in ([] if args.skip_recall else POSITIVES):
        for voice in RECALL_VOICES:
            for rate in RECALL_RATES:
                hit, seen, tail_after = recall_case(text, voice, rate)
                woke = hit is not None
                misses += 0 if woke else 1
                recall_rows.append(
                    {"text": text, "voice": voice, "rate": rate, "woke": woke,
                     "heard": hit["heard"] if hit else "", "tail": hit["tail"] if hit else "",
                     "tail_after": tail_after, "transcripts": seen[:6]}
                )
                print(f"  {'WAKE ' if woke else 'MISS '} {text!r} {voice}@{rate}"
                      + (f" heard={hit['heard']!r} tail={hit['tail']!r} after={tail_after!r}" if hit else f" transcripts={seen!r}"))

    # Over the TV: the same positives mixed into TV audio at two SNRs.
    with tempfile.TemporaryDirectory() as tmp:
        tv_bed = build_tv_track(0.6, Path(tmp))
    overlay_rows = []
    for snr_db in [float(x) for x in args.overlay_snrs.split(",") if x.strip()]:
        for text in ("hey parker", "hey parker, can you help me"):
            for voice in RECALL_VOICES[:2]:
                hit, seen, tail_after = recall_case(text, voice, 175, background=tv_bed, snr_db=snr_db)
                overlay_rows.append({"text": text, "voice": voice, "snr_db": snr_db, "woke": hit is not None,
                                     "heard": hit["heard"] if hit else "", "transcripts": seen[:6]})
                print(f"  {'WAKE ' if hit else 'MISS '} over TV @{snr_db:+.0f} dB {text!r} {voice}"
                      + (f" heard={hit['heard']!r}" if hit else f" transcripts={seen[:4]!r}"))
    confusable_rows = []
    for text in ([] if args.skip_recall else CONFUSABLES):
        for voice in RECALL_VOICES[:2]:
            with tempfile.TemporaryDirectory() as tmp:
                pcm = read_pcm(synth(text, Path(tmp), voice=voice))
            silence = b"\x00\x00" * (WAKE_SAMPLE_RATE // 2)
            det = make_detector(transcriber)
            hits = stream(det, silence + pcm + silence)
            confusable_rows.append(
                {"text": text, "voice": voice, "woke": bool(hits), "heard": hits[0]["heard"] if hits else ""}
            )
            print(f"  {'wake ' if hits else 'quiet'} confusable {text!r} {voice}" + (f" heard={hits[0]['heard']!r}" if hits else ""))

    positives_total = max(1, len(recall_rows))
    report = {
        "eval": "wake_soak_v0",
        "date": date.today().isoformat(),
        "provenance": {"audio": "macOS say synthesized speech only; no private audio", "model": "local faster-whisper"},
        "soak": soak,
        "recall": {
            "positives": positives_total,
            "woke": positives_total - misses,
            "recall": round((positives_total - misses) / positives_total, 3),
            "rows": recall_rows,
        },
        "over_tv": {"rows": overlay_rows, "woke": sum(1 for r in overlay_rows if r["woke"]), "total": len(overlay_rows)},
        "confusables": {
            "rows": confusable_rows,
            "extra_wakes": sum(1 for r in confusable_rows if r["woke"]),
            "policy": "reported, not gated — chairman decision 2026-09-01 (calibrate for Dad's recall)",
        },
        "gate": {"passed": misses == 0 and not false_wakes, "misses": misses, "false_wakes": len(false_wakes)},
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    stem = REPORTS / (f"wake_soak_{report['date']}" + (f"_{args.tag}" if args.tag else ""))
    stem.with_suffix(".json").write_text(json.dumps(report, indent=2, sort_keys=True))
    md = [
        f"# Wake soak {report['date']} (synthesized ambient TV + recall matrix)",
        "",
        "Synthesized speech only (macOS `say`), real local faster-whisper, the real WakeDetector in browser-sized frames.",
        "This is release evidence for CPU/false-wake behaviour on TV-like speech, NOT a substitute for the real-room evening gate with Dad's voice.",
        "",
        "## Ambient-TV soak",
        "",
        f"- audio: {soak['audio_minutes']} min across {len(TV_VOICES)} voices, Parker-adjacent vocabulary throughout",
        f"- inferences: {soak['inferences']} ({soak['inferences_per_audio_minute']}/min of audio)",
        f"- CPU: {soak['cpu_seconds_per_audio_minute']} s per audio minute = {soak['cpu_fraction_of_realtime']:.0%} of one core in real time",
        f"- inference latency: p50 {soak['infer_ms_p50']} ms, p95 {soak['infer_ms_p95']} ms, max {soak['infer_ms_max']} ms",
        f"- false wakes: {len(false_wakes)}" + (" — " + "; ".join(h['heard'] for h in false_wakes) if false_wakes else ""),
        f"- hops skipped by the adaptive gate: {soak['hops_gated_by_background']}",
        f"- config: model={args.model} threads={args.threads or 'auto'} hop={args.hop}s relative_gate={args.relative_gate}",
        "",
        "## Recall matrix (must wake)",
        "",
        "| phrase | voice | wpm | woke | heard (wake window) | tail after wake (lane) |",
        "|---|---|---|---|---|---|",
    ]
    md += [f"| {r['text']} | {r['voice']} | {r['rate']} | {'yes' if r['woke'] else 'NO — heard ' + repr(r['transcripts'])} | {r['heard']} | {r['tail_after']} |" for r in recall_rows]
    md += [
        "",
        f"Recall: {report['recall']['woke']}/{positives_total}.",
        "",
        "## Over the TV (voice mixed into TV audio; voice/TV RMS ratio = SNR)",
        "",
        "| phrase | voice | SNR dB | woke | heard |",
        "|---|---|---|---|---|",
    ]
    md += [f"| {r['text']} | {r['voice']} | {r['snr_db']:+.0f} | {'yes' if r['woke'] else 'NO — ' + repr(r['transcripts'][:3])} | {r['heard']} |" for r in overlay_rows]
    md += [
        "",
        f"Over-TV recall: {report['over_tv']['woke']}/{report['over_tv']['total']}.",
        "",
        "## Confusables (reported, not gated)",
        "",
        "| phrase | voice | woke | heard |",
        "|---|---|---|---|",
    ]
    md += [f"| {r['text']} | {r['voice']} | {'yes' if r['woke'] else 'no'} | {r['heard']} |" for r in confusable_rows]
    md += ["", f"Gate: {'PASS' if report['gate']['passed'] else 'FAIL'} (misses={misses}, false wakes in soak={len(false_wakes)})."]
    stem.with_suffix(".md").write_text("\n".join(md) + "\n")
    print("wrote", stem.with_suffix(".md"))
    print("PASS" if report["gate"]["passed"] else "FAIL")
    return 0 if report["gate"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
