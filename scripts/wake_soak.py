"""Ambient-TV wake soak: CPU, latency, false wakes, and a recall matrix.

Runs OUTSIDE the keyless test suite against the REAL local faster-whisper
model — the same WakeDetector the companion's wake lane uses, fed
browser-sized 16 kHz frames. Sections:

1. **Soak** (gated): a long TV-like track (several macOS `say` voices
   reading news/sports/ad copy stuffed with Parker-adjacent words: parking,
   parked, Parker Brothers, Peter Parker, darker, marker, packer, barker)
   streams through the detector. Every detection is a FALSE wake (no
   greeting + Parker phrase exists in the track). Reports inference count
   per minute of audio, CPU seconds per audio minute, inference p50/p95.
2. **Recall matrix** (gated): effortful positives (`hey parker`,
   `hey... parker`, `hey parka`, `hey par ker`, `hi parker`, `hey parker
   can you help me`) across voices and a slow rate must wake.
3. **Over the TV** (reported, not gated): positives mixed INTO the TV
   audio. Each row is labelled with the SNR the mix ACHIEVED, not the one
   requested: the voice gain is bounded (no over-driving into clipping),
   so when a request needs more, the TV bed is attenuated instead and the
   row records both. Byte-identical mixes count once.
4. **Confusables** (reported, not gated): `hey darker`, `hey marker`,
   `hey barker`, `hey packer`, `hey parked`, `a parker` — chairman decision
   2026-09-01: calibrate for Dad's recall, an occasional extra wake while
   dormant costs only a perk-up.
5. **Paused greeting** (gated): "hey" … real silence … "parker", the parts
   synthesized separately and joined with zeros (a pause, not
   punctuation). The positives must wake; a 10 s stale "hey" must not; the
   other negatives are reported.

A section that did not run says so (`status: not_run`) and the gate is
INCOMPLETE — never a clean zero. A soak shorter than MIN_SOAK_MINUTES, or
one the detector never inferred on, did not run either: a 3 s track once
minted "Gate: PASS … 0 false wakes". Exit 0 PASS, 1 FAIL, 2 INCOMPLETE.

Usage: backend/.venv/bin/python scripts/wake_soak.py [--minutes 4]
Writes benchmark/reports/wake_soak_<date>[_<tag>].{json,md} (aggregate only —
synthesized speech, no private audio). All audio is synthesized BEFORE the
model loads: forking `say` after ctranslate2 is up deadlocked in practice.

The harness seams (`mix_over`, `run_soak`, `recall_case`, `paused_audio`,
`build_report`, `render_md`, `GATE_EXIT`) are pinned model-free by
backend/tests/test_wake_soak_harness.py.
"""

from __future__ import annotations

import argparse
import array
import audioop
import hashlib
import json
import math
import subprocess
import sys
import tempfile
import time
import wave
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.parker.wake import ENERGY_GATE_RMS, WAKE_SAMPLE_RATE, WakeDetector  # noqa: E402
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
    "hey parker",
    "hey, parker.",
    "hey... parker",
    "hey parka",
    "hey par ker",
    "hi parker",
    "hey parker, can you help me",
    "um, hey parker",
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
OVERLAY_PHRASES = ["hey parker", "hey parker, can you help me"]
# Paused greeting (F3): text parts alternate with seconds of real silence.
PAUSED_POSITIVES = [
    ("hey", 3.2, "parker"),
    ("hey", 4.0, "parker, can you help me"),
    ("hi", 3.2, "parker"),
    ("um, hey", 3.2, "parker"),
]
PAUSED_STALE = ("hey", 10.0, "parker")  # past the greeting latch: must stay quiet (gated)
PAUSED_REPORTED = [  # negatives reported like confusables, not gated
    ("hey", 1.0, "I'm parking the car", 2.6, "parker"),
    ("a", 3.2, "parker"),
]
RECALL_VOICES = ["Samantha", "Daniel", "Fred"]
RECALL_RATES = [175, 120]  # words per minute: normal and slow/effortful

LEAD_SILENCE_SECONDS = 0.5
TRAILING_SECONDS = 1.6  # two hops: a live microphone keeps streaming after he stops
TV_LEAD_SECONDS = 2.0  # TV before the voice, so the adaptive gate has a background
TV_TAIL_SECONDS = 1.6
MAX_VOICE_GAIN = 3.0
PEAK_HEADROOM = 0.9  # never drive a voice peak past 90 % of full scale

GATED = ["soak.false_wakes", "recall.misses", "paused.misses", "paused.stale_wakes"]
GATE_EXIT = {"pass": 0, "fail": 1, "incomplete": 2}
MIN_SOAK_MINUTES = 1.0  # below this the soak is not_run — no false-wake claim from seconds of audio


# --- audio ----------------------------------------------------------------


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


def write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(WAKE_SAMPLE_RATE)
        handle.writeframes(pcm)


def silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(seconds * WAKE_SAMPLE_RATE)


def say_pcm(text: str, *, voice: str | None = None, rate: int | None = None, synth_fn=None) -> bytes:
    """One utterance as PCM. ``synth_fn`` resolves at call time so a
    caller that swaps ``synth`` (a say cache, a test stub) is honoured."""

    synth_fn = synth_fn or synth
    with tempfile.TemporaryDirectory() as tmp:
        return read_pcm(synth_fn(text, Path(tmp), voice=voice, rate=rate))


def build_tv_track(minutes: float, out_dir: Path) -> bytes:
    target = int(minutes * 60 * WAKE_SAMPLE_RATE) * 2
    pcm = bytearray()
    index = 0
    gap = silence(0.35)
    while len(pcm) < target:
        line = TV_LINES[index % len(TV_LINES)]
        voice = TV_VOICES[index % len(TV_VOICES)]
        with tempfile.TemporaryDirectory() as tmp:
            pcm += read_pcm(synth(line, Path(tmp), voice=voice)) + gap
        index += 1
    return bytes(pcm[:target])


def isolated(pcm: bytes) -> bytes:
    """A phrase the way the live lane hears it: a beat of quiet before,
    two hops of quiet after (trailing silence shorter than one 0.7 s hop
    cut the final inference mid-word — a harness artifact)."""

    return silence(LEAD_SILENCE_SECONDS) + pcm + silence(TRAILING_SECONDS)


def paused_label(parts: tuple) -> str:
    return " ".join(f"…{p:.1f} s…" if isinstance(p, (int, float)) else p for p in parts)


def paused_audio(parts: tuple, *, voice: str, rate: int, synth_fn=None) -> bytes:
    """Each text part synthesized on its own, joined with real silence —
    the pause is zeros in the PCM, not a comma the voice may swallow."""

    audio = bytearray(silence(LEAD_SILENCE_SECONDS))
    for part in parts:
        if isinstance(part, (int, float)):
            audio += silence(part)
        else:
            audio += say_pcm(part, voice=voice, rate=rate, synth_fn=synth_fn)
    audio += silence(TRAILING_SECONDS)
    return bytes(audio)


def _db(numerator: float, denominator: float) -> float:
    return 20 * math.log10(max(numerator, 1) / max(denominator, 1))


def mix_over(voice: bytes, background: bytes, snr_db: float) -> tuple[bytes, dict]:
    """The voice on top of TV audio at a voice-RMS / TV-RMS ratio of snr_db:
    2 s of TV lead-in (so the adaptive gate has a background), the mix, then
    1.6 s of TV tail. Returns ``(pcm, achieved)``.

    The voice gain is bounded (3x, and never past 90 % of full scale at
    its peak); when the request needs more, the BED is attenuated instead
    — the ratio stays reachable without clipping, and ``achieved`` records
    what actually happened: gain, bed attenuation, clipped fraction, and
    the SNR measured on the surviving voice (mixed minus bed) against the
    bed as mixed. Rows and tables are labelled with ``snr_achieved_db``.
    """

    lead = int(TV_LEAD_SECONDS * WAKE_SAMPLE_RATE) * 2
    tail = int(TV_TAIL_SECONDS * WAKE_SAMPLE_RATE) * 2
    need = lead + len(voice) + tail
    bed = (background * (need // len(background) + 1))[:need]
    bed_rms = max(1, audioop.rms(bed, 2))
    voice_rms = max(1, audioop.rms(voice, 2))
    factor = (bed_rms * (10 ** (snr_db / 20))) / voice_rms
    peak = max(1, audioop.max(voice, 2))
    gain = min(factor, MAX_VOICE_GAIN, PEAK_HEADROOM * 32767 / peak)
    attenuation = gain / factor if factor > gain else 1.0
    if attenuation < 1.0:
        bed = audioop.mul(bed, 2, attenuation)
    scaled = audioop.mul(voice, 2, gain)
    mixed = audioop.add(bed, b"\x00" * lead + scaled + b"\x00" * tail, 2)

    bed_rms_mixed = max(1, audioop.rms(bed, 2))
    segment = slice(lead, lead + len(voice))
    # mixed − bed over the voice segment = the voice that survived the
    # (saturating) add; the difference itself never exceeds full scale.
    surviving = audioop.add(mixed[segment], audioop.mul(bed[segment], 2, -1.0), 2)
    samples = array.array("h", mixed[segment])
    clipped = (samples.count(32767) + samples.count(-32768)) / max(1, len(samples))
    achieved = {
        "snr_requested_db": float(snr_db),
        "snr_after_gain_db": round(_db(audioop.rms(scaled, 2), bed_rms_mixed), 2),
        "snr_achieved_db": round(_db(audioop.rms(surviving, 2), bed_rms_mixed), 2),
        "voice_rms_after_mix": audioop.rms(surviving, 2),
        "bed_rms": bed_rms_mixed,
        "gain": round(gain, 3),
        "gain_capped": factor > gain,
        "bed_attenuation_db": round(-20 * math.log10(attenuation), 2) if attenuation < 1.0 else 0.0,
        "clipped_fraction": round(clipped, 4),
    }
    return mixed, achieved


# --- runners (real WakeDetector; the transcriber is whatever the caller built) ---


def stream(detector: WakeDetector, pcm: bytes, timings: list[float] | None = None):
    hits = []
    for start in range(0, len(pcm), FRAME_SAMPLES * 2):
        before = detector.inferences
        t0 = time.perf_counter()
        result = detector.feed(pcm[start : start + FRAME_SAMPLES * 2])
        if timings is not None and detector.inferences > before:
            timings.append((time.perf_counter() - t0) * 1000)
        if result:
            hit = {"matched": result["matched"], "heard": result["heard"], "tail": result["tail"]}
            if result.get("latch_s") is not None:
                hit["latch_s"] = result["latch_s"]
            hits.append(hit)
    return hits


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = min(len(ordered) - 1, max(0, int(round((p / 100) * (len(ordered) - 1)))))
    return ordered[k]


def not_run(reason: str) -> dict:
    return {"status": "not_run", "reason": reason}


def run_soak(track: bytes, detector: WakeDetector | None) -> dict:
    """Stream the TV track; every wake is a false wake. A track shorter
    than MIN_SOAK_MINUTES (``--minutes 0`` included), or one the detector
    never inferred on, is a section that did not run — no per-minute
    figures, no false-wake claim. ``--skip-soak`` is main()'s not_run."""

    audio_minutes = len(track) / 2 / WAKE_SAMPLE_RATE / 60
    if audio_minutes < MIN_SOAK_MINUTES:
        return not_run(f"soak too short ({audio_minutes:.2f} min < {MIN_SOAK_MINUTES} min)")
    timings: list[float] = []
    cpu0, wall0 = time.process_time(), time.perf_counter()
    false_wakes = stream(detector, track, timings)
    cpu, wall = time.process_time() - cpu0, time.perf_counter() - wall0
    if detector.inferences == 0:
        return not_run("no inferences ran")
    return {
        "status": "ran",
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
    }


def recall_case(make_detector, *, audio: bytes):
    """Stream one pre-built utterance through a fresh detector. Returns
    (wake hit or None, transcripts seen, tail heard after the wake) — the
    post-wake tail lane is simulated by feeding on for 3 s."""

    det = make_detector()
    seen: list[str] = []
    orig = det._transcriber

    def spy(path):
        lines = orig(path)
        seen.append(" ".join(l.strip() for l in lines if l and l.strip()))
        return lines

    det._transcriber = spy
    hit = None
    woke_at = 0
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


def _print_case(prefix: str, label: str, hit, seen, tail_after: str | None = None) -> None:
    detail = (
        f" heard={hit['heard']!r}" + (f" tail={hit['tail']!r}" if hit.get("tail") else "")
        + (f" after={tail_after!r}" if tail_after else "")
        + (f" latch_s={hit['latch_s']}" if hit.get("latch_s") is not None else "")
        if hit
        else f" transcripts={seen[:4]!r}"
    )
    print(f"  {prefix} {label}{detail}", flush=True)


def run_recall(cases: list[dict], make_detector) -> dict:
    rows = []
    for case in cases:
        hit, seen, tail_after = recall_case(make_detector, audio=case["audio"])
        rows.append(
            {"text": case["text"], "voice": case["voice"], "rate": case["rate"], "woke": hit is not None,
             "heard": hit["heard"] if hit else "", "tail": hit["tail"] if hit else "",
             "tail_after": tail_after, "transcripts": seen[:6]}
        )
        _print_case("WAKE " if hit else "MISS ", f"{case['text']!r} {case['voice']}@{case['rate']}", hit, seen, tail_after)
    return {"status": "ran", "rows": rows}


def run_over_tv(cases: list[dict], make_detector) -> dict:
    rows = []
    for case in cases:
        hit, seen, _ = recall_case(make_detector, audio=case["audio"])
        rows.append(
            {"text": case["text"], "voice": case["voice"], "rate": case["rate"], "woke": hit is not None,
             "snr_requested_db": case["mix"]["snr_requested_db"], "snr_achieved_db": case["mix"]["snr_achieved_db"],
             "mix": case["mix"], "audio_sha1": case["audio_sha1"],
             "heard": hit["heard"] if hit else "", "transcripts": seen[:6]}
        )
        label = (f"over TV requested {case['mix']['snr_requested_db']:+.0f} dB → achieved "
                 f"{case['mix']['snr_achieved_db']:+.1f} dB {case['text']!r} {case['voice']}")
        _print_case("WAKE " if hit else "MISS ", label, hit, seen)
    return {"status": "ran", "rows": rows}


def run_confusables(cases: list[dict], make_detector) -> dict:
    rows = []
    for case in cases:
        hits = stream(make_detector(), case["audio"])
        rows.append({"text": case["text"], "voice": case["voice"], "woke": bool(hits), "heard": hits[0]["heard"] if hits else ""})
        print(f"  {'wake ' if hits else 'quiet'} confusable {case['text']!r} {case['voice']}"
              + (f" heard={hits[0]['heard']!r}" if hits else ""), flush=True)
    return {"status": "ran", "rows": rows}


def run_paused(cases: list[dict], make_detector) -> dict:
    rows = []
    for case in cases:
        hit, seen, _ = recall_case(make_detector, audio=case["audio"])
        rows.append(
            {"phrase": case["phrase"], "parts": list(case["parts"]), "voice": case["voice"], "rate": case["rate"],
             "kind": case["kind"], "woke": hit is not None, "latch_s": hit.get("latch_s") if hit else None,
             "heard": hit["heard"] if hit else "", "transcripts": seen[:8]}
        )
        expected_wake = case["kind"] == "positive"
        prefix = ("WAKE " if expected_wake else "wake ") if hit else ("MISS " if expected_wake else "quiet")
        _print_case(prefix, f"paused/{case['kind']} {case['phrase']!r} {case['voice']}@{case['rate']}", hit, seen)
    return {"status": "ran", "rows": rows}


# --- report -----------------------------------------------------------------


def _summarize_recall(section: dict) -> dict:
    if section["status"] != "ran":
        return dict(section)
    rows = section["rows"]
    woke = sum(1 for r in rows if r["woke"])
    return {
        "status": "ran", "rows": rows, "positives": len(rows), "woke": woke, "misses": len(rows) - woke,
        "recall": round(woke / len(rows), 3) if rows else None,
    }


def _summarize_over_tv(section: dict) -> dict:
    if section["status"] != "ran":
        return dict(section)
    rows = [dict(r) for r in section["rows"]]
    first_seen: dict[str, int] = {}
    for index, row in enumerate(rows):
        digest = row.get("audio_sha1")
        if digest and digest in first_seen:
            row["duplicate_of"] = first_seen[digest]  # the same bytes are one data point
        elif digest:
            first_seen[digest] = index
    counted = [r for r in rows if "duplicate_of" not in r]
    woke = sum(1 for r in counted if r["woke"])
    # Group by the achieved SNR to a tenth of a dB — the label's precision.
    achieved = sorted({round(r["snr_achieved_db"], 1) for r in counted}, reverse=True)
    by_snr = {
        f"{snr:+.1f}": f"{sum(1 for r in counted if round(r['snr_achieved_db'], 1) == snr and r['woke'])}/"
        f"{sum(1 for r in counted if round(r['snr_achieved_db'], 1) == snr)}"
        for snr in achieved
    }
    summary = f"{woke}/{len(counted)}" + (
        f" at achieved SNRs {', '.join(f'{s:+.1f}' for s in achieved)} dB" if achieved else ""
    )
    return {
        "status": "ran", "rows": rows, "woke": woke, "total": len(counted),
        "duplicates": len(rows) - len(counted), "by_achieved_snr": by_snr, "summary": summary,
        "policy": "reported, not gated — labelled with the achieved SNR; duplicate mixes excluded",
    }


def _summarize_confusables(section: dict) -> dict:
    if section["status"] != "ran":
        return dict(section)
    rows = section["rows"]
    return {
        "status": "ran", "rows": rows, "extra_wakes": sum(1 for r in rows if r["woke"]), "total": len(rows),
        "policy": "reported, not gated — chairman decision 2026-09-01 (calibrate for Dad's recall)",
    }


def _summarize_paused(section: dict) -> dict:
    if section["status"] != "ran":
        return dict(section)
    rows = section["rows"]
    positives = [r for r in rows if r["kind"] == "positive"]
    stale = [r for r in rows if r["kind"] == "stale"]
    reported = [r for r in rows if r["kind"] == "reported"]
    return {
        "status": "ran", "rows": rows,
        "positives": len(positives), "positives_woke": sum(1 for r in positives if r["woke"]),
        "misses": sum(1 for r in positives if not r["woke"]),
        "stale": len(stale), "stale_quiet": sum(1 for r in stale if not r["woke"]),
        "stale_wakes": sum(1 for r in stale if r["woke"]),
        "reported": len(reported), "reported_wakes": sum(1 for r in reported if r["woke"]),
        "policy": "positives and the 10 s stale greeting are gated; the other negatives are reported",
    }


def _gate(report: dict) -> dict:
    counters = {
        "soak.false_wakes": ("soak", lambda s: len(s["false_wakes"])),
        "recall.misses": ("recall", lambda s: s["misses"]),
        "paused.misses": ("paused", lambda s: s["misses"]),
        "paused.stale_wakes": ("paused", lambda s: s["stale_wakes"]),
    }
    skipped: list[str] = []
    counts: dict[str, int] = {}
    for name, (section_name, count) in counters.items():
        section = report[section_name]
        if section["status"] == "ran":
            counts[name] = count(section)
        elif section_name not in skipped:
            skipped.append(section_name)
    reported_only = {}
    for name in ("over_tv", "confusables"):
        section = report[name]
        if section["status"] != "ran":
            reported_only[name] = f"not run ({section['reason']})"
        elif name == "over_tv":
            reported_only[name] = section["summary"]
        else:
            reported_only[name] = f"{section['extra_wakes']}/{section['total']} extra wakes"
    if report["paused"]["status"] == "ran":
        reported_only["paused_negatives"] = f"{report['paused']['reported_wakes']}/{report['paused']['reported']} extra wakes"
    status = "incomplete" if skipped else ("fail" if any(counts.values()) else "pass")
    return {
        "status": status,
        "passed": None if status == "incomplete" else status == "pass",
        "gated": list(GATED),
        "not_run": skipped,
        "counts": counts,
        "reported_only": reported_only,
    }


def build_report(*, soak: dict, recall: dict, over_tv: dict, confusables: dict, paused: dict, config: dict, today: date | None = None) -> dict:
    """Assemble the wake_soak_v1 report from section results. Each section
    is either a runner's ``{"status": "ran", ...}`` or ``not_run(reason)``;
    the gate reads only sections that ran and says INCOMPLETE otherwise."""

    report = {
        "eval": "wake_soak_v1",
        "date": (today or date.today()).isoformat(),
        "provenance": {"audio": "macOS say synthesized speech only; no private audio", "model": "local faster-whisper"},
        "config": dict(config),
        "soak": dict(soak),
        "recall": _summarize_recall(recall),
        "over_tv": _summarize_over_tv(over_tv),
        "confusables": _summarize_confusables(confusables),
        "paused": _summarize_paused(paused),
    }
    report["gate"] = _gate(report)
    return report


def gate_line(report: dict) -> str:
    gate = report["gate"]
    soak, recall, paused = report["soak"], report["recall"], report["paused"]
    over_tv, confusables = report["over_tv"], report["confusables"]
    parts = []
    if soak["status"] == "ran":
        n = len(soak["false_wakes"])
        parts.append(f"soak {soak['audio_minutes']} min: {n} false wake{'s' if n != 1 else ''}")
    if recall["status"] == "ran":
        parts.append(f"recall {recall['woke']}/{recall['positives']}")
    if paused["status"] == "ran":
        parts.append(f"paused {paused['positives_woke']}/{paused['positives']}, stale quiet {paused['stale_quiet']}/{paused['stale']}")
    over = f"over-TV {over_tv['woke']}/{over_tv['total']}" if over_tv["status"] == "ran" else "over-TV not run"
    conf = (f"confusables {confusables['extra_wakes']}/{confusables['total']} extra wakes"
            if confusables["status"] == "ran" else "confusables not run")
    if gate["status"] == "incomplete":
        skipped = ", ".join(f"{name} not run ({report[name]['reason']})" for name in gate["not_run"])
        return f"Gate: INCOMPLETE — {skipped}; {over} reported only; {conf} reported only."
    verdict = "PASS" if gate["passed"] else "FAIL"
    return f"Gate: {verdict} ({'; '.join(parts)}) — {over} reported, not gated; {conf} reported, not gated."


def render_md(report: dict) -> str:
    config = report["config"]
    soak, recall, over_tv, confusables, paused = (
        report["soak"], report["recall"], report["over_tv"], report["confusables"], report["paused"]
    )
    md = [
        f"# Wake soak {report['date']} (synthesized ambient TV + recall matrix)",
        "",
        "Synthesized speech only (macOS `say`), real local faster-whisper, the real WakeDetector in browser-sized frames.",
        "This is release evidence for CPU/false-wake behaviour on TV-like speech, NOT a substitute for the real-room evening gate with Dad's voice.",
        "Sections that did not run say so; the gate reads only the sections that ran.",
        "",
        f"- config: model={config.get('model')} threads={config.get('threads') or 'auto'} hop={config.get('hop')}s relative_gate={config.get('relative_gate')}",
        "",
        "## Ambient-TV soak (gated: false wakes)",
        "",
    ]
    if soak["status"] == "ran":
        false_wakes = soak["false_wakes"]
        md += [
            f"- audio: {soak['audio_minutes']} min across {len(TV_VOICES)} voices, Parker-adjacent vocabulary throughout",
            f"- inferences: {soak['inferences']} ({soak['inferences_per_audio_minute']}/min of audio)",
            f"- CPU: {soak['cpu_seconds_per_audio_minute']} s per audio minute = {soak['cpu_fraction_of_realtime']:.0%} of one core in real time",
            f"- inference latency: p50 {soak['infer_ms_p50']} ms, p95 {soak['infer_ms_p95']} ms, max {soak['infer_ms_max']} ms",
            f"- false wakes: {len(false_wakes)}" + (" — " + "; ".join(h["heard"] for h in false_wakes) if false_wakes else ""),
            f"- hops skipped by the adaptive gate: {soak['hops_gated_by_background']}",
        ]
    else:
        md.append(f"soak: not run ({soak['reason']})")
    md += ["", "## Recall matrix (gated: must wake)", ""]
    if recall["status"] == "ran":
        md += [
            "| phrase | voice | wpm | woke | heard (wake window) | tail after wake (lane) |",
            "|---|---|---|---|---|---|",
        ]
        md += [f"| {r['text']} | {r['voice']} | {r['rate']} | {'yes' if r['woke'] else 'NO — heard ' + repr(r['transcripts'])} | {r['heard']} | {r['tail_after']} |" for r in recall["rows"]]
        md += ["", f"Recall: {recall['woke']}/{recall['positives']}."]
    else:
        md.append(f"recall: not run ({recall['reason']})")
    md += ["", "## Over the TV (reported, not gated; voice mixed into TV audio, SNR = voice RMS / TV RMS)", ""]
    if over_tv["status"] == "ran":
        md += [
            "Each row is labelled with the SNR the mix achieved. The voice gain is bounded; beyond it the TV bed is attenuated (column), so a request is reached without clipping.",
            "",
            "| phrase | voice | SNR requested → achieved (dB) | bed attenuated (dB) | clipped | woke | heard |",
            "|---|---|---|---|---|---|---|",
        ]
        for index, r in enumerate(over_tv["rows"]):
            mix = r.get("mix", {})
            if "duplicate_of" in r:
                woke = f"duplicate of row {r['duplicate_of'] + 1} (excluded)"
            else:
                woke = "yes" if r["woke"] else "NO — " + repr(r["transcripts"][:3])
            md.append(
                f"| {r['text']} | {r['voice']} | {r['snr_requested_db']:+.0f} → {r['snr_achieved_db']:+.1f} | "
                f"{mix.get('bed_attenuation_db', 0.0):.1f} | {mix.get('clipped_fraction', 0.0):.2%} | {woke} | {r['heard']} |"
            )
        md += ["", f"Over-TV recall: {over_tv['woke']}/{over_tv['total']}"
               + (f" ({over_tv['duplicates']} duplicate mix excluded)" if over_tv["duplicates"] else "") + "."]
        md += [f"- achieved {snr} dB: {ratio}" for snr, ratio in over_tv["by_achieved_snr"].items()]
    else:
        md.append(f"over-TV: not run ({over_tv['reason']})")
    md += ["", "## Confusables (reported, not gated)", ""]
    if confusables["status"] == "ran":
        md += ["| phrase | voice | woke | heard |", "|---|---|---|---|"]
        md += [f"| {r['text']} | {r['voice']} | {'yes' if r['woke'] else 'no'} | {r['heard']} |" for r in confusables["rows"]]
        md += ["", f"Extra wakes: {confusables['extra_wakes']}/{confusables['total']}."]
    else:
        md.append(f"confusables: not run ({confusables['reason']})")
    md += ["", "## Paused greeting (real silence)", ""]
    if paused["status"] == "ran":
        md += [
            "Parts synthesized separately and joined with zeros. Positives must wake and the 10 s stale greeting must stay quiet (gated); the other negatives are reported.",
            "",
            "| phrase | voice | wpm | kind | woke | latch_s | heard |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in paused["rows"]:
            expected = r["kind"] == "positive"
            woke = ("yes" if expected else "YES — extra wake") if r["woke"] else ("NO — heard " + repr(r["transcripts"][:4]) if expected else "no")
            md.append(f"| {r['phrase']} | {r['voice']} | {r['rate']} | {r['kind']} | {woke} | {r['latch_s'] if r['latch_s'] is not None else ''} | {r['heard']} |")
        md += ["", f"Paused positives: {paused['positives_woke']}/{paused['positives']}; stale quiet: {paused['stale_quiet']}/{paused['stale']}; reported negatives that woke: {paused['reported_wakes']}/{paused['reported']}."]
    else:
        md.append(f"paused: not run ({paused['reason']})")
    md += ["", gate_line(report)]
    return "\n".join(md) + "\n"


# --- CLI --------------------------------------------------------------------


def build_cases(args) -> dict:
    """Every piece of audio the run needs, synthesized up front."""

    cases: dict = {"track": b"", "recall": [], "over_tv": [], "confusables": [], "paused": []}
    if not args.skip_soak:
        print(f"synthesizing {args.minutes:.1f} min of TV-like speech…", flush=True)
        with tempfile.TemporaryDirectory() as tmp:
            cases["track"] = build_tv_track(args.minutes, Path(tmp))
    if not args.skip_recall:
        print(f"synthesizing {len(POSITIVES) * len(RECALL_VOICES) * len(RECALL_RATES)} recall positives…", flush=True)
        for text in POSITIVES:
            for voice in RECALL_VOICES:
                for rate in RECALL_RATES:
                    cases["recall"].append({"text": text, "voice": voice, "rate": rate, "audio": isolated(say_pcm(text, voice=voice, rate=rate))})
        print(f"synthesizing {len(CONFUSABLES) * 2} confusables…", flush=True)
        for text in CONFUSABLES:
            for voice in RECALL_VOICES[:2]:
                cases["confusables"].append({"text": text, "voice": voice, "audio": silence(0.5) + say_pcm(text, voice=voice) + silence(0.5)})
    snrs = [float(x) for x in args.overlay_snrs.split(",") if x.strip()]
    if snrs:
        print(f"synthesizing the TV bed and {len(snrs) * len(OVERLAY_PHRASES) * 2} over-TV mixes…", flush=True)
        with tempfile.TemporaryDirectory() as tmp:
            bed = build_tv_track(0.6, Path(tmp))
        voices = {(text, voice): say_pcm(text, voice=voice, rate=175) for text in OVERLAY_PHRASES for voice in RECALL_VOICES[:2]}
        for snr_db in snrs:
            for text in OVERLAY_PHRASES:
                for voice in RECALL_VOICES[:2]:
                    mixed, achieved = mix_over(voices[(text, voice)], bed, snr_db)
                    cases["over_tv"].append({"text": text, "voice": voice, "rate": 175, "audio": mixed, "mix": achieved,
                                             "audio_sha1": hashlib.sha1(mixed).hexdigest()})
    if not args.skip_paused:
        specs = [(p, "positive") for p in PAUSED_POSITIVES] + [(PAUSED_STALE, "stale")] + [(p, "reported") for p in PAUSED_REPORTED]
        print(f"synthesizing {len(specs) * len(RECALL_VOICES) * len(RECALL_RATES)} paused-greeting cases…", flush=True)
        for parts, kind in specs:
            for voice in RECALL_VOICES:
                for rate in RECALL_RATES:
                    cases["paused"].append({"phrase": paused_label(parts), "parts": parts, "voice": voice, "rate": rate,
                                            "kind": kind, "audio": paused_audio(parts, voice=voice, rate=rate)})
    return cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=4.0, help=f"TV-like audio to soak (below {MIN_SOAK_MINUTES} the soak is not_run: INCOMPLETE)")
    parser.add_argument("--model", default="base", help="faster-whisper size for the wake lane")
    parser.add_argument("--threads", type=int, default=0, help="cpu_threads cap (0 = library default)")
    parser.add_argument("--hop", type=float, default=0.7, help="seconds of new audio per inference")
    parser.add_argument("--relative-gate", type=float, default=0.0, help="run only when a hop is this many times louder than the trailing median (0 = off)")
    parser.add_argument("--tag", default="", help="report name suffix")
    parser.add_argument("--skip-soak", action="store_true", help="skip the ambient-TV soak (the gate reports INCOMPLETE)")
    parser.add_argument("--skip-recall", action="store_true", help="skip the recall matrix and confusables (INCOMPLETE)")
    parser.add_argument("--skip-paused", action="store_true", help="skip the paused-greeting section (INCOMPLETE)")
    parser.add_argument("--overlay-snrs", default="0,-6", help="requested voice/TV SNRs in dB for the over-TV rows ('' = none)")
    args = parser.parse_args()

    config = {"model": args.model, "threads": args.threads, "hop": args.hop, "relative_gate": args.relative_gate,
              "minutes": 0.0 if args.skip_soak else args.minutes, "overlay_snrs": args.overlay_snrs}
    cases = build_cases(args)  # all `say` work before the model exists

    print(f"loading local transcriber (faster-whisper {args.model}, threads={args.threads or 'auto'})…", flush=True)
    transcriber = load_local_transcriber(model_size=args.model, cpu_threads=args.threads)

    def make_detector():
        return WakeDetector(transcriber, hop_seconds=args.hop, relative_gate=args.relative_gate)

    soak = not_run("--skip-soak") if args.skip_soak else run_soak(cases["track"], make_detector())
    if soak["status"] == "ran":
        print(
            f"soak: {soak['audio_minutes']} min audio, {soak['inferences']} inferences, "
            f"cpu {soak['cpu_seconds_per_audio_minute'] * soak['audio_minutes']:.1f}s "
            f"({soak['cpu_fraction_of_realtime']:.0%} of realtime), "
            f"p50 {soak['infer_ms_p50']} ms, p95 {soak['infer_ms_p95']} ms, false wakes {len(soak['false_wakes'])}",
            flush=True,
        )
        for hit in soak["false_wakes"]:
            print("  FALSE WAKE:", hit)
    recall = run_recall(cases["recall"], make_detector) if not args.skip_recall else not_run("--skip-recall")
    over_tv = run_over_tv(cases["over_tv"], make_detector) if cases["over_tv"] else not_run("--overlay-snrs empty")
    confusables = run_confusables(cases["confusables"], make_detector) if not args.skip_recall else not_run("--skip-recall")
    paused = run_paused(cases["paused"], make_detector) if not args.skip_paused else not_run("--skip-paused")

    report = build_report(soak=soak, recall=recall, over_tv=over_tv, confusables=confusables, paused=paused, config=config)
    REPORTS.mkdir(parents=True, exist_ok=True)
    stem = REPORTS / (f"wake_soak_{report['date']}" + (f"_{args.tag}" if args.tag else ""))
    stem.with_suffix(".json").write_text(json.dumps(report, indent=2, sort_keys=True))
    stem.with_suffix(".md").write_text(render_md(report))
    print("wrote", stem.with_suffix(".md"))
    print(gate_line(report))
    print(report["gate"]["status"].upper())
    return GATE_EXIT[report["gate"]["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
