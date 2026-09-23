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


# --- 5. clock integrity (2026-09-22 phase7) -------------------------------------
#
# Events are chronological receipt events on one monotonic timeline. A list
# whose timestamps decrease is not a turn that can be scored: it is
# ``invalid_timing`` with no TTFS and unknown endpoint timing. Never reorder.


def test_out_of_order_finals_are_invalid_timing_not_reordered_or_scored():
    # Same two finals as test_ttfs_uses_last_nonempty_final_not_first_partial,
    # received in the wrong order. Before phase7 this scored ok/0.5 with the
    # words scrambled.
    m = _measure(
        [
            sse.FinalTranscript(at_s=1.5, text="tomorrow"),
            sse.FinalTranscript(at_s=1.2, text="call sarah"),
        ]
    )
    assert m.status == "invalid_timing"
    assert m.ttfs_s is None
    assert m.last_final_at_s is None
    assert m.transcript == ""  # no scrambled or silently repaired text
    assert m.endpoint_at_s is None and m.premature_endpoint is None
    assert "1.200" in m.detail and "1.500" in m.detail


def test_out_of_order_list_with_valid_endpoint_still_has_unknown_endpoint():
    # The endpoint itself is fine (1.3 >= speech end 1.0) but the list is
    # not chronological, so nothing on it is trusted: endpoint is unknown.
    m = _measure(
        [
            sse.FinalTranscript(at_s=1.2, text="call"),
            sse.EndpointDecision(at_s=1.3),
            sse.FinalTranscript(at_s=1.25, text="sarah"),
        ]
    )
    assert m.status == "invalid_timing"
    assert m.endpoint_at_s is None
    assert m.premature_endpoint is None


def test_decreasing_timestamps_take_precedence_over_provider_error():
    m = _measure(
        [
            sse.TranscriptError(at_s=1.1, kind="error", detail="socket closed"),
            sse.PartialTranscript(at_s=0.6, text="what time"),
        ]
    )
    assert m.status == "invalid_timing"
    assert m.ttfs_s is None
    assert m.endpoint_at_s is None and m.premature_endpoint is None
    assert "socket closed" not in m.detail


def test_decreasing_timestamps_take_precedence_over_no_output_and_late_final():
    partials_only = _measure(
        [sse.PartialTranscript(at_s=0.9, text="call"), sse.PartialTranscript(at_s=0.5, text="ca")]
    )
    assert partials_only.status == "invalid_timing"
    late_then_early = _measure(
        [sse.FinalTranscript(at_s=4.0, text="sarah"), sse.FinalTranscript(at_s=1.1, text="call")],
        finalization_timeout_s=2.0,
    )
    assert late_then_early.status == "invalid_timing"
    assert late_then_early.ttfs_s is None


def test_decreasing_endpoint_after_final_is_invalid_not_scored():
    m = _measure([sse.FinalTranscript(at_s=1.4, text="hi"), sse.EndpointDecision(at_s=1.3)])
    assert m.status == "invalid_timing"
    assert m.premature_endpoint is None


def test_equal_timestamps_preserve_iterable_order():
    m = _measure(
        [
            sse.PartialTranscript(at_s=1.2, text="call"),
            sse.FinalTranscript(at_s=1.2, text="call sarah"),
            sse.FinalTranscript(at_s=1.2, text="tomorrow"),
            sse.EndpointDecision(at_s=1.2),
        ]
    )
    assert m.status == "ok"
    assert m.transcript == "call sarah tomorrow"
    assert m.ttfs_s == pytest.approx(0.2)
    assert m.endpoint_at_s == pytest.approx(1.2)
    assert m.premature_endpoint is False


def test_first_iterable_endpoint_wins_among_equal_endpoint_timestamps():
    # Two endpoint decisions at the same instant: the first one on the list
    # is the decision; the second is not a new judgement.
    m = _measure(
        [
            sse.EndpointDecision(at_s=0.9),
            sse.EndpointDecision(at_s=0.9),
            sse.FinalTranscript(at_s=1.2, text="hi"),
        ]
    )
    assert m.status == "ok"
    assert m.endpoint_at_s == pytest.approx(0.9)
    assert m.premature_endpoint is True


def test_partial_and_final_at_the_same_instant_are_not_an_endpoint():
    m = _measure([sse.PartialTranscript(at_s=1.2, text="hi"), sse.FinalTranscript(at_s=1.2, text="hi")])
    assert m.status == "ok"
    assert m.endpoint_at_s is None
    assert m.premature_endpoint is None


def test_error_turn_retains_a_genuinely_observed_endpoint():
    early = _measure([sse.EndpointDecision(at_s=0.8), sse.TranscriptError(at_s=1.1, detail="closed")])
    assert early.status == "error"
    assert early.ttfs_s is None
    assert early.endpoint_at_s == pytest.approx(0.8)
    assert early.premature_endpoint is True
    on_time = _measure([sse.EndpointDecision(at_s=1.2), sse.TranscriptError(at_s=1.3, detail="closed")])
    assert on_time.status == "error"
    assert on_time.premature_endpoint is False


def test_negative_ttfs_turn_retains_a_genuinely_observed_endpoint():
    # Chronologically valid list; only the TTFS dimension is invalid.
    m = _measure([sse.FinalTranscript(at_s=0.9, text="hi"), sse.EndpointDecision(at_s=1.1)], speech_end_s=1.0)
    assert m.status == "invalid_timing"
    assert m.ttfs_s is None
    assert m.endpoint_at_s == pytest.approx(1.1)
    assert m.premature_endpoint is False


# --- 6. percentile input validation ---------------------------------------------


@pytest.mark.parametrize("bad", [-0.1, math.nan, math.inf, -math.inf, True, False, "0.4", None, [0.4]])
def test_percentiles_reject_invalid_samples_instead_of_filtering(bad):
    with pytest.raises(ValueError):
        sse.percentiles([0.4, bad, 0.6])


def test_percentiles_accept_zero_and_ints():
    assert sse.percentiles([0, 0.5]) == {"count": 2, "p50": 0.0, "p95": 0.5, "p99": 0.5}


# --- 7. endpoint coverage + split summaries ------------------------------------------
#
# Expected numbers below are hand-derived from the fixture JSON labels
# (benchmark/fixtures/streaming_stt_synthetic_v0.json), not from running the
# code. Per case (status / TTFS / endpoint judged?):
#   train syn-a: pause-001 ok 0.4 F | neg-001 ok 0.4 F | err-001 error - none
#   train syn-b: restart-001 ok 0.5 F | cancel-001 ok 0.2 F | invalid-001 invalid_timing - F
#   test  syn-c: pause-002 ok 0.4 T(premature) | name-001 ok 0.3 F | timeout-001 timeout - none
#   test  syn-d: corr-001 ok 0.4 F | noout-001 no_output - none
# WER over ok turns: train 0, 0.25 (don't->do), 0, 0 ; test 0, 0.5 (2 ins / 4), 0.2 (1 sub / 5).
# Intent matches: train pause, restart, cancel, invalid (4/6); test pause-002, corr, name, timeout (4/5).

FIXTURE_SHA256 = "19ac043116904689e390baef3564377877ecc974c36b6ca0aa58b18a4bedab24"


def test_synthetic_fixture_bytes_are_unchanged_by_this_slice():
    import hashlib

    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == FIXTURE_SHA256


def _case(case_id, speaker, events, *, speech_end_s=1.0, truth="hi", intent="x", observed_intent="x", timeout=3.0):
    return sse.FixtureCase(
        case_id=case_id,
        speaker=speaker,
        tags=(),
        truth_transcript=truth,
        truth_intent=intent,
        speech_end_s=speech_end_s,
        observed_intent=observed_intent,
        events=tuple(events),
        finalization_timeout_s=timeout,
    )


def _fixture(cases, *, train=("a",), test=("b",)):
    return sse.Fixture(
        fixture_id="direct",
        train_speakers=frozenset(train),
        test_speakers=frozenset(test),
        cases=tuple(cases),
        finalization_timeout_s=3.0,
    )


def test_report_endpoint_coverage_counts_turns_from_fixture_labels():
    report = sse.evaluate_fixture(sse.load_fixture(FIXTURE))
    assert report["attempted"] == 11
    assert report["status_counts"] == {"error": 1, "invalid_timing": 1, "no_output": 1, "ok": 7, "timeout": 1}
    assert report["failure_count"] == 4
    assert report["endpoint_scored_turns"] == 8  # 7 ok + invalid-001 (final early, endpoint 1.7 >= 1.6)
    assert report["endpoint_unknown_turns"] == 3  # noout-001, err-001, timeout-001
    assert report["endpoint_scored_turns"] + report["endpoint_unknown_turns"] == report["attempted"]
    assert report["premature_endpoint_count"] == 1
    assert report["premature_endpoint_rate_over_scored"] == pytest.approx(1 / 8)
    rows = {r["case_id"]: r for r in report["cases"]}
    assert rows["invalid-001"]["status"] == "invalid_timing"
    assert rows["invalid-001"]["premature_endpoint"] is False  # known endpoint on a failed turn
    assert rows["err-001"]["premature_endpoint"] is None
    assert report["wer_sample_count"] == 7
    assert report["mean_wer_over_usable"] == pytest.approx((0 + 0 + 0.25 + 0 + 0 + 0.5 + 0.2) / 7)


def test_rows_carry_split_and_all_case_row_order_is_unchanged():
    fx = sse.load_fixture(FIXTURE)
    report = sse.evaluate_fixture(fx)
    assert [r["case_id"] for r in report["cases"]] == [c.case_id for c in fx.cases]
    assert [r["split"] for r in report["cases"]] == [
        "train" if c.speaker in fx.train_speakers else "test" for c in fx.cases
    ]
    assert list(report)[:9] == [
        "fixture_id",
        "attempted",
        "status_counts",
        "usable_output_rate",
        "intent_exact_matches",
        "intent_exact_match_rate",
        "mean_wer_over_usable",
        "premature_endpoint_count",
        "ttfs_s",
    ]
    assert list(report)[-1] == "cases"


def test_by_split_summaries_match_hand_labels_and_add_to_all_case_totals():
    report = sse.evaluate_fixture(sse.load_fixture(FIXTURE))
    train, test = report["by_split"]["train"], report["by_split"]["test"]

    assert train["attempted"] == 6 and test["attempted"] == 5
    assert train["status_counts"] == {"error": 1, "invalid_timing": 1, "ok": 4}
    assert test["status_counts"] == {"no_output": 1, "ok": 3, "timeout": 1}
    assert train["failure_count"] == 2 and test["failure_count"] == 2
    assert train["usable_output_rate"] == pytest.approx(4 / 6)
    assert test["usable_output_rate"] == pytest.approx(3 / 5)

    assert train["ttfs_s"] == {"count": 4, "p50": pytest.approx(0.4), "p95": pytest.approx(0.5), "p99": pytest.approx(0.5)}
    assert test["ttfs_s"] == {"count": 3, "p50": pytest.approx(0.4), "p95": pytest.approx(0.4), "p99": pytest.approx(0.4)}

    assert train["wer_sample_count"] == 4 and test["wer_sample_count"] == 3
    assert train["mean_wer_over_usable"] == pytest.approx(0.25 / 4)
    assert test["mean_wer_over_usable"] == pytest.approx((0.5 + 0.2) / 3)

    assert train["intent_exact_matches"] == 4 and train["intent_exact_match_rate"] == pytest.approx(4 / 6)
    assert test["intent_exact_matches"] == 4 and test["intent_exact_match_rate"] == pytest.approx(4 / 5)

    assert train["endpoint_scored_turns"] == 5 and train["endpoint_unknown_turns"] == 1
    assert train["premature_endpoint_count"] == 0
    assert train["premature_endpoint_rate_over_scored"] == pytest.approx(0.0)
    assert test["endpoint_scored_turns"] == 3 and test["endpoint_unknown_turns"] == 2
    assert test["premature_endpoint_count"] == 1
    assert test["premature_endpoint_rate_over_scored"] == pytest.approx(1 / 3)

    for key in (
        "attempted",
        "failure_count",
        "intent_exact_matches",
        "wer_sample_count",
        "premature_endpoint_count",
        "endpoint_scored_turns",
        "endpoint_unknown_turns",
    ):
        assert train[key] + test[key] == report[key], key
    assert train["ttfs_s"]["count"] + test["ttfs_s"]["count"] == report["ttfs_s"]["count"]
    assert "cases" not in train and "cases" not in test
    assert "training" not in json.dumps(report["by_split"]).lower()


def test_empty_split_has_zero_counts_and_null_rates():
    fx = _fixture([_case("c1", "a", [sse.FinalTranscript(at_s=1.3, text="hi"), sse.EndpointDecision(at_s=1.4)])])
    report = sse.evaluate_fixture(fx)
    empty = report["by_split"]["test"]
    assert empty["attempted"] == 0 and empty["failure_count"] == 0
    assert empty["status_counts"] == {}
    assert empty["usable_output_rate"] is None
    assert empty["intent_exact_matches"] == 0 and empty["intent_exact_match_rate"] is None
    assert empty["wer_sample_count"] == 0 and empty["mean_wer_over_usable"] is None
    assert empty["ttfs_s"] == {"count": 0, "p50": None, "p95": None, "p99": None}
    assert empty["endpoint_scored_turns"] == 0 and empty["endpoint_unknown_turns"] == 0
    assert empty["premature_endpoint_count"] == 0
    assert empty["premature_endpoint_rate_over_scored"] is None
    assert report["by_split"]["train"]["attempted"] == 1
    assert report["by_split"]["train"]["premature_endpoint_rate_over_scored"] == pytest.approx(0.0)


def test_premature_rate_is_null_when_no_endpoint_was_scored():
    fx = _fixture([_case("c1", "a", [sse.FinalTranscript(at_s=1.3, text="hi")])])
    report = sse.evaluate_fixture(fx)
    assert report["status_counts"] == {"ok": 1}
    assert report["endpoint_scored_turns"] == 0 and report["endpoint_unknown_turns"] == 1
    assert report["premature_endpoint_count"] == 0
    assert report["premature_endpoint_rate_over_scored"] is None


def test_direct_fixture_rejects_train_test_overlap():
    fx = _fixture([_case("c1", "a", [sse.FinalTranscript(at_s=1.3, text="hi")])], train=("a",), test=("a", "b"))
    with pytest.raises(ValueError, match="speaker"):
        sse.evaluate_fixture(fx)


def test_direct_fixture_rejects_speaker_outside_both_splits():
    fx = _fixture([_case("c1", "ghost", [sse.FinalTranscript(at_s=1.3, text="hi")])])
    with pytest.raises(ValueError, match="ghost"):
        sse.evaluate_fixture(fx)


def test_mixed_measured_and_unmeasured_turns_keep_unmeasured_out_of_percentiles():
    fx = _fixture(
        [
            _case("ok1", "a", [sse.FinalTranscript(at_s=1.5, text="hi"), sse.EndpointDecision(at_s=1.6)]),
            _case("err1", "a", [sse.TranscriptError(at_s=1.1, detail="closed")]),
            _case("ok2", "b", [sse.FinalTranscript(at_s=1.2, text="hi")]),
            _case("noout", "b", [sse.FinalTranscript(at_s=1.2, text="")]),
        ]
    )
    report = sse.evaluate_fixture(fx)
    assert report["attempted"] == 4
    assert report["status_counts"] == {"error": 1, "no_output": 1, "ok": 2}
    assert report["ttfs_s"] == {"count": 2, "p50": pytest.approx(0.2), "p95": pytest.approx(0.5), "p99": pytest.approx(0.5)}
    assert report["usable_output_rate"] == pytest.approx(0.5)
    assert report["by_split"]["train"]["ttfs_s"]["count"] == 1
    assert report["by_split"]["test"]["ttfs_s"]["count"] == 1
    assert report["endpoint_scored_turns"] == 1 and report["endpoint_unknown_turns"] == 3


def test_decreasing_timestamp_plus_error_case_is_invalid_timing_and_endpoint_unknown():
    fx = _fixture(
        [
            _case(
                "bad-order",
                "a",
                [
                    sse.EndpointDecision(at_s=1.2),
                    sse.TranscriptError(at_s=1.1, detail="closed"),
                ],
            ),
            _case("good", "b", [sse.FinalTranscript(at_s=1.3, text="hi"), sse.EndpointDecision(at_s=1.4)]),
        ]
    )
    report = sse.evaluate_fixture(fx)
    rows = {r["case_id"]: r for r in report["cases"]}
    assert rows["bad-order"]["status"] == "invalid_timing"
    assert rows["bad-order"]["ttfs_s"] is None and rows["bad-order"]["wer"] is None
    assert rows["bad-order"]["premature_endpoint"] is None
    assert report["status_counts"] == {"invalid_timing": 1, "ok": 1}
    assert report["failure_count"] == 1
    assert report["usable_output_rate"] == pytest.approx(0.5)
    assert report["wer_sample_count"] == 1
    assert report["ttfs_s"]["count"] == 1
    assert report["endpoint_scored_turns"] == 1 and report["endpoint_unknown_turns"] == 1
    assert report["premature_endpoint_rate_over_scored"] == pytest.approx(0.0)
    assert report["by_split"]["train"]["status_counts"] == {"invalid_timing": 1}
    assert report["by_split"]["train"]["endpoint_unknown_turns"] == 1


def test_cli_report_carries_split_and_endpoint_coverage():
    proc = subprocess.run(
        [sys.executable, "-m", "app.audio_harness.streaming_stt_eval"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    fr = json.loads(proc.stdout)["fixture_report"]
    assert set(fr["by_split"]) == {"train", "test"}
    assert fr["endpoint_scored_turns"] + fr["endpoint_unknown_turns"] == fr["attempted"]
    assert all("split" in row for row in fr["cases"])
