"""scripts/wake_soak.py — the harness must not overclaim (F8).

Keyless and model-free: the script is imported by path (its imports are
lazy about faster-whisper), audio is synthetic PCM, and the detector runs
on a stub transcriber. What is pinned here is the report's honesty, not
recall: an over-TV row is labelled with the SNR it ACHIEVED, byte-identical
mixes are not two data points, a skipped section is "not run" rather than
a clean zero, and the gate only says PASS when every gated section ran.
"""

from __future__ import annotations

import array
import audioop
import hashlib
import importlib.util
import math
import random
from pathlib import Path

import pytest

from app.parker.wake import WAKE_SAMPLE_RATE, WakeDetector

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "wake_soak.py"


@pytest.fixture(scope="module")
def soak():
    spec = importlib.util.spec_from_file_location("wake_soak_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pcm(values) -> bytes:
    return array.array("h", (int(max(-32768, min(32767, v))) for v in values)).tobytes()


def _sine(seconds: float, amplitude: float, freq: float = 220.0) -> bytes:
    n = int(seconds * WAKE_SAMPLE_RATE)
    return _pcm(amplitude * math.sin(2 * math.pi * freq * i / WAKE_SAMPLE_RATE) for i in range(n))


def _noise(seconds: float, sigma: float, seed: int = 1) -> bytes:
    rng = random.Random(seed)
    return _pcm(rng.gauss(0, sigma) for _ in range(int(seconds * WAKE_SAMPLE_RATE)))


def _stub_detector(heard: str = "the parking garage downtown"):
    return WakeDetector(lambda path: [heard])


# --- mix_over: label the achieved SNR, not the request ----------------------


def test_mix_over_reports_achieved_snr_not_the_request(soak):
    """Bed twice as loud as the voice, +12 dB requested: the old harness
    capped the voice gain at 3x and mixed at +0.5 dB while labelling the
    row +12. Now the bed is attenuated instead, the request is reached
    without clipping, and the row says what was achieved."""

    voice = _sine(1.0, 4000)
    bed = _noise(3.0, 8000)
    mixed, achieved = soak.mix_over(voice, bed, 12.0)

    assert achieved["snr_requested_db"] == 12.0
    assert achieved["gain_capped"] is True
    assert achieved["bed_attenuation_db"] > 0
    assert achieved["snr_achieved_db"] == pytest.approx(12.0, abs=0.5)
    assert achieved["clipped_fraction"] < 0.01
    # The attenuated bed still clears the detector's energy gate by a wide
    # margin — the harness did not buy its label by silencing the TV.
    assert achieved["bed_rms"] > 4 * soak.ENERGY_GATE_RMS
    assert len(mixed) == int(2.0 * WAKE_SAMPLE_RATE) * 2 + len(voice) + int(1.6 * WAKE_SAMPLE_RATE) * 2


def test_mix_over_without_a_cap_achieves_the_request_and_keeps_the_bed(soak):
    voice = _sine(1.0, 8000)
    bed = _noise(3.0, 4000)
    _, achieved = soak.mix_over(voice, bed, -6.0)
    assert achieved["gain_capped"] is False
    assert achieved["bed_attenuation_db"] == 0.0
    assert achieved["snr_achieved_db"] == pytest.approx(-6.0, abs=0.3)
    assert achieved["bed_rms"] == audioop.rms(
        (bed * 10)[: int(2.0 * WAKE_SAMPLE_RATE) * 2 + len(voice) + int(1.6 * WAKE_SAMPLE_RATE) * 2], 2
    )


def test_different_requests_never_collapse_to_the_same_bytes(soak):
    voice = _sine(1.0, 4000)
    bed = _noise(3.0, 8000)
    six, _ = soak.mix_over(voice, bed, 6.0)
    twelve, _ = soak.mix_over(voice, bed, 12.0)
    assert six != twelve


# --- report: skipped sections are not clean zeros ---------------------------


def _over_tv_row(text, voice, snr, woke, digest):
    return {
        "text": text, "voice": voice, "rate": 175, "woke": woke,
        "snr_requested_db": snr, "snr_achieved_db": snr, "audio_sha1": digest,
        "heard": "hey parker" if woke else "", "transcripts": [],
    }


def _recall_row(text="hey parker", voice="Samantha", rate=175, woke=True):
    return {
        "text": text, "voice": voice, "rate": rate, "woke": woke,
        "heard": "hey parker" if woke else "", "tail": "", "tail_after": "",
        "transcripts": [] if woke else ["hey park"],
    }


def _paused_row(kind="positive", woke=True):
    return {
        "phrase": "hey …3.2 s… parker", "voice": "Samantha", "rate": 175, "kind": kind,
        "woke": woke, "latch_s": 3.4 if woke else None, "heard": "parker" if woke else "",
        "transcripts": [],
    }


def _ran_soak(false_wakes=()):
    return {
        "status": "ran", "audio_minutes": 4.0, "inferences": 312,
        "inferences_per_audio_minute": 78.0, "cpu_seconds_per_audio_minute": 159.0,
        "cpu_fraction_of_realtime": 2.65, "wall_seconds": 162.0,
        "infer_ms_p50": 464.0, "infer_ms_p95": 751.0, "infer_ms_max": 900.0,
        "false_wakes": list(false_wakes), "hops_gated_by_background": 0,
    }


CONFIG = {"model": "base", "threads": 0, "hop": 0.7, "relative_gate": 0.0, "minutes": 4.0}


def _report(harness, **sections):
    defaults = {
        "soak": _ran_soak(),
        "recall": {"status": "ran", "rows": [_recall_row()]},
        "over_tv": {"status": "ran", "rows": []},
        "confusables": {"status": "ran", "rows": []},
        "paused": {"status": "ran", "rows": [_paused_row(), _paused_row("stale", woke=False)]},
    }
    defaults.update(sections)
    return harness.build_report(config=CONFIG, **defaults)


def test_identical_mixes_are_not_counted_twice(soak):
    same = hashlib.sha1(b"identical mix").hexdigest()
    other = hashlib.sha1(b"another mix").hexdigest()
    rows = [
        _over_tv_row("hey parker", "Daniel", 6.0, False, same),
        _over_tv_row("hey parker", "Daniel", 12.0, False, same),
        _over_tv_row("hey parker", "Samantha", 12.0, True, other),
    ]
    report = _report(soak, over_tv={"status": "ran", "rows": rows})
    over_tv = report["over_tv"]
    assert "duplicate_of" not in over_tv["rows"][0]
    assert over_tv["rows"][1]["duplicate_of"] == 0
    assert over_tv["total"] == 2 and over_tv["woke"] == 1
    assert over_tv["duplicates"] == 1
    md = soak.render_md(report)
    assert "duplicate of row 1" in md
    assert "Over-TV recall: 1/2" in md


def test_over_tv_groups_rows_by_achieved_snr_to_a_tenth(soak):
    """Two mixes achieving 11.97 and 12.02 dB are one +12.0 bucket, not two
    labels that collide (seen in the end-to-end smoke: '+12.0, +12.0')."""

    rows = [
        _over_tv_row("hey parker", "Samantha", 12.0, True, "a1"),
        _over_tv_row("hey parker, can you help me", "Samantha", 12.0, False, "b2"),
    ]
    rows[0]["snr_achieved_db"], rows[1]["snr_achieved_db"] = 11.97, 12.02
    over_tv = _report(soak, over_tv={"status": "ran", "rows": rows})["over_tv"]
    assert over_tv["by_achieved_snr"] == {"+12.0": "1/2"}
    assert over_tv["summary"] == "1/2 at achieved SNRs +12.0 dB"


def test_skipped_soak_is_not_run_and_carries_no_false_wake_claim(soak):
    assert soak.run_soak(b"", None) == {"status": "not_run", "reason": "--skip-soak"}
    report = _report(soak, soak=soak.run_soak(b"", None))
    section = report["soak"]
    assert section["status"] == "not_run"
    for key in ("false_wakes", "cpu_seconds_per_audio_minute", "inferences_per_audio_minute", "audio_minutes"):
        assert key not in section
    assert report["gate"]["status"] == "incomplete"
    assert report["gate"]["passed"] is None
    assert "soak" in report["gate"]["not_run"]
    md = soak.render_md(report)
    assert "Gate: PASS" not in md
    assert "Gate: INCOMPLETE" in md
    assert "soak: not run (--skip-soak)" in md
    assert "per audio minute" not in md


def test_skipped_recall_is_not_one_of_one(soak):
    report = _report(soak, recall=soak.not_run("--skip-recall"))
    assert report["recall"]["status"] == "not_run"
    assert "positives" not in report["recall"] and "recall" not in report["recall"]
    md = soak.render_md(report)
    assert "Recall: 1/1" not in md
    assert "recall: not run (--skip-recall)" in md
    assert report["gate"]["status"] == "incomplete"


def test_gate_line_names_ungated_over_tv_and_full_runs_only_pass(soak):
    rows = [
        _over_tv_row("hey parker", v, s, woke, hashlib.sha1(f"{v}{s}{t}".encode()).hexdigest())
        for s in (12.0, 6.0, 0.0)
        for t in ("hey parker", "hey parker, can you help me")
        for v, woke in (("Samantha", s > 0 and "help" in t), ("Daniel", s > 0 and "help" in t))
    ]
    full = _report(soak, over_tv={"status": "ran", "rows": rows})
    assert full["over_tv"]["woke"] == 4 and full["over_tv"]["total"] == 12
    assert full["gate"]["status"] == "pass" and full["gate"]["passed"] is True
    assert full["gate"]["not_run"] == []
    gate_line = soak.render_md(full).rstrip().splitlines()[-1]
    assert gate_line.startswith("Gate: PASS")
    assert "over-TV 4/12 reported, not gated" in gate_line
    assert soak.GATE_EXIT[full["gate"]["status"]] == 0

    partial = _report(soak, over_tv={"status": "ran", "rows": rows}, soak=soak.not_run("--skip-soak"))
    assert partial["gate"]["status"] == "incomplete"
    assert soak.GATE_EXIT[partial["gate"]["status"]] == 2
    assert "over-TV 4/12 reported only" in soak.render_md(partial).rstrip().splitlines()[-1]

    missed = _report(soak, recall={"status": "ran", "rows": [_recall_row(woke=False)]})
    assert missed["gate"]["status"] == "fail" and missed["gate"]["passed"] is False
    assert missed["recall"]["misses"] == 1
    assert soak.GATE_EXIT[missed["gate"]["status"]] == 1
    assert soak.render_md(missed).rstrip().splitlines()[-1].startswith("Gate: FAIL")


def test_paused_greeting_section_is_gated_on_positives_and_the_stale_negative(soak):
    """F3's paused-greeting evidence: a missed paused positive or a wake on
    the 10 s stale 'hey' fails the run; the other negatives are reported."""

    rows = [_paused_row("positive", woke=False), _paused_row("stale", woke=True), _paused_row("reported", woke=True)]
    report = _report(soak, paused={"status": "ran", "rows": rows})
    paused = report["paused"]
    assert paused["misses"] == 1 and paused["stale_wakes"] == 1 and paused["reported_wakes"] == 1
    assert report["gate"]["status"] == "fail"
    assert report["gate"]["counts"]["paused.misses"] == 1
    assert report["gate"]["counts"]["paused.stale_wakes"] == 1
    assert "Paused greeting (real silence)" in soak.render_md(report)

    quiet = _report(soak, paused=soak.not_run("--skip-paused"))
    assert quiet["gate"]["status"] == "incomplete" and "paused" in quiet["gate"]["not_run"]


def test_report_schema_is_v1_with_per_section_status(soak):
    report = _report(soak)
    assert report["eval"] == "wake_soak_v1"
    assert {report[s]["status"] for s in ("soak", "recall", "over_tv", "confusables", "paused")} == {"ran"}
    assert report["gate"]["gated"] == ["soak.false_wakes", "recall.misses", "paused.misses", "paused.stale_wakes"]
    assert report["config"] == CONFIG


# --- runners work on the real WakeDetector with a stub transcriber -----------


def test_run_soak_measures_a_real_track(soak):
    track = _sine(3.0, 3000)
    section = soak.run_soak(track, _stub_detector())
    assert section["status"] == "ran"
    assert section["audio_minutes"] == pytest.approx(0.05, abs=0.001)
    assert section["inferences"] > 0
    assert section["inferences_per_audio_minute"] == pytest.approx(section["inferences"] / 0.05, rel=0.05)
    assert section["false_wakes"] == []


def test_recall_case_streams_prebuilt_audio_and_reports_the_tail(soak):
    audio = b"\x00\x00" * (WAKE_SAMPLE_RATE // 2) + _sine(1.5, 3000) + b"\x00\x00" * int(1.6 * WAKE_SAMPLE_RATE)
    hit, seen, _ = soak.recall_case(lambda: _stub_detector("hey parker can you"), audio=audio)
    assert hit is not None and hit["matched"] == "hey parker" and hit["tail"] == "can you"
    assert seen and seen[0] == "hey parker can you"
    miss, seen, _ = soak.recall_case(lambda: _stub_detector("the parking garage"), audio=audio)
    assert miss is None and seen


def test_paused_audio_joins_separately_synthesized_parts_with_real_silence(soak):
    calls = []

    def fake_synth(text, out_dir, voice=None, rate=None):
        calls.append((text, voice, rate))
        path = out_dir / "utterance.wav"
        soak.write_wav(path, _sine(0.5, 3000))
        return path

    audio = soak.paused_audio(("hey", 3.2, "parker"), voice="Fred", rate=120, synth_fn=fake_synth)
    assert [c[0] for c in calls] == ["hey", "parker"] and calls[0][1:] == ("Fred", 120)
    part = int(0.5 * WAKE_SAMPLE_RATE) * 2
    lead = int(0.5 * WAKE_SAMPLE_RATE) * 2
    gap = int(3.2 * WAKE_SAMPLE_RATE) * 2
    trailing = int(1.6 * WAKE_SAMPLE_RATE) * 2
    assert len(audio) == lead + part + gap + part + trailing
    assert audio[lead + part : lead + part + gap] == b"\x00" * gap  # silence, not punctuation
    assert soak.paused_label(("hey", 1.0, "I'm parking the car", 2.6, "parker")) == "hey …1.0 s… I'm parking the car …2.6 s… parker"


def test_swapping_synth_reaches_every_synthesis_path(soak, monkeypatch):
    """A say cache or a stub replaces ``soak.synth`` (the F3 prototype did);
    the helpers must resolve it at call time, not bind the real `say` as a
    default argument."""

    def fake_synth(text, out_dir, voice=None, rate=None):
        path = out_dir / "utterance.wav"
        soak.write_wav(path, _sine(0.25, 3000))
        return path

    monkeypatch.setattr(soak, "synth", fake_synth)
    part = int(0.25 * WAKE_SAMPLE_RATE) * 2
    assert len(soak.say_pcm("hey parker", voice="Daniel", rate=175)) == part
    lead_and_tail = int(0.5 * WAKE_SAMPLE_RATE) * 2 + int(1.6 * WAKE_SAMPLE_RATE) * 2
    assert len(soak.paused_audio(("hey", 1.0, "parker"), voice="Fred", rate=120)) == lead_and_tail + 2 * part + int(1.0 * WAKE_SAMPLE_RATE) * 2
