"""Boundary tests for the offline streaming-STT evaluation seam.

Authored before the implementation from the acceptance list in the
2026-09-22 build brief. No audio, no provider, no network: PCM bytes are
generated in-test, timestamps are hand-labeled, and the clock/sleep are
injected so pacing is deterministic.

Timing vocabulary (after pipecat-ai/stt-benchmark docs/measuring-ttfs.md):
speech end = VAD stop minus the configured stop delay; TTFS = time of the
LAST nonempty final transcript minus speech end. A final transcript
segment does not by itself mean the user finished; only an explicit
endpoint event is an end-of-turn decision.
"""

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
FIXTURE = REPO / "benchmark" / "fixtures" / "streaming_stt_synthetic_v0.json"

sys.path.append(str(REPO))

from app.audio_harness import streaming_stt_eval as sse  # noqa: E402


class FakeClock:
    """Monotonic clock advanced only by injected sleep."""

    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0
        self.sleeps.append(seconds)
        self.now += seconds


def _pcm(sample_count: int) -> bytes:
    return bytes(2 * sample_count)


# --- 1. pacing contract -----------------------------------------------------


def test_pacing_follows_sample_time_with_injected_clock():
    clock = FakeClock()
    # 100 ms of 16 kHz mono = 1600 samples = 3200 bytes → five 20 ms chunks.
    chunks = list(
        sse.pace_pcm16(_pcm(1600), 16000, chunk_ms=20, monotonic=clock.monotonic, sleep=clock.sleep)
    )
    assert [c.audio_time_s for c in chunks] == pytest.approx([0.0, 0.02, 0.04, 0.06, 0.08])
    assert all(len(c.pcm) == 640 for c in chunks)
    assert sum(clock.sleeps) == pytest.approx(0.08)
    # Each chunk was emitted at (not before) its sample-time offset.
    assert [c.emitted_at_s for c in chunks] == pytest.approx([0.0, 0.02, 0.04, 0.06, 0.08])


def test_pacing_does_not_sleep_when_clock_already_late():
    clock = FakeClock()

    def slow_sleep(seconds: float) -> None:
        clock.sleeps.append(seconds)
        clock.now += seconds + 0.05  # consumer fell behind by 50 ms

    chunks = list(sse.pace_pcm16(_pcm(1600), 16000, monotonic=clock.monotonic, sleep=slow_sleep))
    assert len(chunks) == 5
    # First sleep (20 ms) overshoots to 70 ms: chunks at 40/60 ms release with
    # no sleep; the 80 ms chunk sleeps only the remaining 10 ms. Never dropped.
    assert clock.sleeps == pytest.approx([0.02, 0.01])
    assert all(c.emitted_at_s >= c.audio_time_s for c in chunks)


def test_pacing_keeps_short_tail_chunk():
    clock = FakeClock()
    chunks = list(sse.pace_pcm16(_pcm(1700), 16000, monotonic=clock.monotonic, sleep=clock.sleep))
    assert len(chunks) == 6
    assert len(chunks[-1].pcm) == 200  # 100 samples


@pytest.mark.parametrize("rate", [0, -16000, 15999, 16000.0, "16000"])
def test_rejects_malformed_sample_rate(rate):
    with pytest.raises(ValueError):
        list(sse.pace_pcm16(_pcm(10), rate, monotonic=lambda: 0.0, sleep=lambda s: None))


def test_rejects_odd_pcm_byte_length():
    with pytest.raises(ValueError, match="odd"):
        list(sse.pace_pcm16(b"\x00" * 3, 16000, monotonic=lambda: 0.0, sleep=lambda s: None))


def test_pacing_has_no_cloud_default():
    assert not hasattr(sse, "DEFAULT_PROVIDER")
    assert not any(name.lower().startswith(("openai", "deepgram", "cloud")) for name in dir(sse))


# --- 2. TTFS + endpoint measurement ------------------------------------------


def _measure(events, speech_end_s=1.0, **kw):
    return sse.measure_turn(speech_end_s=speech_end_s, events=events, **kw)


def test_ttfs_uses_last_nonempty_final_not_first_partial():
    m = _measure(
        [
            sse.PartialTranscript(at_s=0.6, text="call"),
            sse.FinalTranscript(at_s=1.2, text="call sarah"),
            sse.FinalTranscript(at_s=1.5, text="tomorrow"),
        ]
    )
    assert m.status == "ok"
    assert m.ttfs_s == pytest.approx(0.5)
    assert m.transcript == "call sarah tomorrow"


def test_empty_final_is_ignored_for_timing_and_text():
    m = _measure(
        [
            sse.FinalTranscript(at_s=1.3, text="call sarah"),
            sse.FinalTranscript(at_s=2.0, text=""),
            sse.FinalTranscript(at_s=2.1, text="   "),
        ]
    )
    assert m.status == "ok"
    assert m.ttfs_s == pytest.approx(0.3)
    assert m.transcript == "call sarah"


def test_only_empty_finals_is_no_output():
    m = _measure([sse.FinalTranscript(at_s=1.3, text="")])
    assert m.status == "no_output"
    assert m.ttfs_s is None
    assert m.transcript == ""


def test_partial_only_is_no_output():
    m = _measure([sse.PartialTranscript(at_s=0.5, text="call")])
    assert m.status == "no_output"
    assert m.ttfs_s is None


def test_negative_ttfs_is_invalid_not_clamped():
    m = _measure([sse.FinalTranscript(at_s=0.9, text="hi")], speech_end_s=1.0)
    assert m.status == "invalid_timing"
    assert m.ttfs_s is None


def test_final_segment_is_not_an_endpoint_decision():
    m = _measure([sse.FinalTranscript(at_s=0.7, text="call"), sse.FinalTranscript(at_s=1.4, text="sarah")])
    assert m.endpoint_at_s is None
    assert m.premature_endpoint is None  # no decision was made, so not judged
    assert m.status == "ok"


def test_premature_endpoint_iff_decision_before_speech_end():
    early = _measure([sse.EndpointDecision(at_s=0.8), sse.FinalTranscript(at_s=1.2, text="hi")])
    assert early.premature_endpoint is True
    on_time = _measure([sse.FinalTranscript(at_s=1.2, text="hi"), sse.EndpointDecision(at_s=1.3)])
    assert on_time.premature_endpoint is False
    assert on_time.endpoint_at_s == pytest.approx(1.3)


def test_error_and_timeout_events_have_no_latency():
    err = _measure([sse.TranscriptError(at_s=1.1, kind="error", detail="socket closed")])
    assert err.status == "error"
    assert err.ttfs_s is None
    late = _measure([sse.FinalTranscript(at_s=4.0, text="hi")], finalization_timeout_s=2.0)
    assert late.status == "timeout"
    assert late.ttfs_s is None


def test_late_last_final_cannot_turn_partial_transcript_into_fast_success():
    m = _measure(
        [sse.FinalTranscript(at_s=1.1, text="call"),
         sse.FinalTranscript(at_s=4.0, text="sarah not sam")],
        finalization_timeout_s=2.0,
    )
    assert m.status == "timeout"
    assert m.ttfs_s is None
    assert m.transcript == "call"  # only observed before the deadline


def test_drain_deadline_keeps_its_exact_boundary():
    m = _measure([sse.FinalTranscript(at_s=3.0, text="cancel")], finalization_timeout_s=2.0)
    assert m.status == "ok"
    assert m.ttfs_s == 2.0


@pytest.mark.parametrize("bad", [-0.1, math.nan, math.inf])
def test_rejects_invalid_event_timestamps(bad):
    with pytest.raises(ValueError):
        sse.FinalTranscript(at_s=bad, text="hi")


def test_rejects_invalid_speech_end():
    with pytest.raises(ValueError):
        _measure([], speech_end_s=-1.0)


def test_speech_end_from_vad_subtracts_stop_delay():
    assert sse.speech_end_from_vad(vad_stop_s=1.5, stop_delay_s=0.2) == pytest.approx(1.3)
    with pytest.raises(ValueError):
        sse.speech_end_from_vad(vad_stop_s=0.1, stop_delay_s=0.2)


# --- 3. fixture + aggregate report ----------------------------------------------


def test_fixture_loads_and_covers_required_phenomena():
    fx = sse.load_fixture(FIXTURE)
    tags = {t for case in fx.cases for t in case.tags}
    assert {"pause", "restart", "correction", "negation", "name", "cancel", "no_output", "error", "timeout"} <= tags
    assert fx.train_speakers.isdisjoint(fx.test_speakers)
    assert all(case.speaker in fx.train_speakers | fx.test_speakers for case in fx.cases)


def test_rejects_speaker_overlap(tmp_path):
    raw = json.loads(FIXTURE.read_text())
    raw["speaker_split"]["test"].append(raw["speaker_split"]["train"][0])
    bad = tmp_path / "overlap.json"
    bad.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="speaker"):
        sse.load_fixture(bad)


def test_rejects_case_with_unlisted_speaker(tmp_path):
    raw = json.loads(FIXTURE.read_text())
    raw["cases"][0]["speaker"] = "ghost"
    bad = tmp_path / "ghost.json"
    bad.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="speaker"):
        sse.load_fixture(bad)


def test_report_rates_use_all_attempted_and_percentiles_use_valid_only():
    report = sse.evaluate_fixture(sse.load_fixture(FIXTURE))
    n = report["attempted"]
    assert n == len(sse.load_fixture(FIXTURE).cases)
    statuses = report["status_counts"]
    assert statuses["no_output"] >= 1 and statuses["error"] >= 1 and statuses["timeout"] >= 1
    assert statuses["invalid_timing"] >= 1
    ttfs = report["ttfs_s"]
    assert ttfs["count"] == statuses["ok"]
    assert ttfs["count"] < n
    assert 0 < ttfs["p50"] <= ttfs["p95"] <= ttfs["p99"]
    assert report["usable_output_rate"] == pytest.approx(statuses["ok"] / n)
    assert report["intent_exact_match_rate"] == pytest.approx(report["intent_exact_matches"] / n)
    assert report["premature_endpoint_count"] >= 1
    assert "ranking" not in json.dumps(report)


def test_intent_match_is_separate_from_wer():
    fx = sse.load_fixture(FIXTURE)
    by_id = {c.case_id: c for c in fx.cases}
    rows = {r["case_id"]: r for r in sse.evaluate_fixture(fx)["cases"]}
    # Negation: transcript nearly right (low WER) yet intent label wrong.
    neg = rows["neg-001"]
    assert neg["wer"] < 0.5 and neg["intent_match"] is False
    # Correction: transcript differs from the plain reference (high WER) yet intent right.
    cor = rows["corr-001"]
    assert cor["wer"] > 0.0 and cor["intent_match"] is True
    assert by_id["corr-001"].truth_intent == "reminder"


def test_wer_matches_existing_harness_implementation():
    from benchmark.audio_harness.score import wer as harness_wer

    for ref, hyp in [("call sarah", "call sara"), ("", ""), ("a b c", ""), ("Hello, world!", "hello world")]:
        assert sse.wer(ref, hyp) == pytest.approx(harness_wer(ref, hyp))


def test_percentiles_empty_and_single():
    assert sse.percentiles([]) == {"count": 0, "p50": None, "p95": None, "p99": None}
    assert sse.percentiles([0.4]) == {"count": 1, "p50": 0.4, "p95": 0.4, "p99": 0.4}


# --- 4. CLI smoke -------------------------------------------------------------


def test_cli_smoke_emits_labeled_synthetic_json():
    proc = subprocess.run(
        [sys.executable, "-m", "app.audio_harness.streaming_stt_eval"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["kind"] == "synthetic_timing_smoke"
    assert out["real_speech"] is False and out["live_provider"] is False
    assert out["pacing"]["chunks"] > 0
    assert out["pacing"]["sample_rate"] == 16000
    assert out["fixture_report"]["attempted"] > 0
    assert "not ASR evidence" in out["note"]


def test_no_audio_files_committed_for_this_slice():
    fixtures_dir = REPO / "benchmark" / "fixtures"
    assert not [p for p in fixtures_dir.rglob("*") if p.suffix.lower() in {".wav", ".mp3", ".flac", ".pcm", ".ogg"}]
