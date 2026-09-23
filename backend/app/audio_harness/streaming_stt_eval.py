"""Offline streaming-STT evaluation seam: replay pacing + turn timing.

What this is: a small, provider-independent contract for measuring a
streaming transcriber's *turn* behaviour from labeled event timelines,
plus a PCM16 pacer for replaying audio at sample time. What it is not: an
ASR adapter, a provider ranking, or a production voice-path change. No
network, no dependency beyond the standard library.

Timing vocabulary (after pipecat-ai/stt-benchmark ``docs/measuring-ttfs.md``
and its synthetic transport, which paces 20 ms PCM16 chunks and stamps the
LAST final receipt with ``time.monotonic``):

- **speech end** = annotated end of the user's speech. From a VAD it is
  ``vad_stop - stop_delay`` (:func:`speech_end_from_vad`); in fixtures it is
  a hand label.
- **TTFS** (time to final segment) = time of the LAST nonempty final
  transcript minus speech end. Partials and empty finals never count. A
  negative TTFS is *invalid timing*, never clamped to zero.
- **endpoint decision** = an explicit end-of-turn event. A final transcript
  segment is not an endpoint: providers finalize mid-utterance. An endpoint
  is *premature* iff its decision time is earlier than the annotated speech
  end.

All timestamps are seconds on one monotonic timeline whose zero is the
start of the replayed audio. An event list is a chronological receipt log:
a decreasing timestamp makes the whole turn ``invalid_timing`` (no TTFS,
unknown endpoint) and is never sorted or repaired. Fixture labels are
timing/behaviour labels, not ASR evidence.

CLI (from ``backend``)::

    python -m app.audio_harness.streaming_stt_eval [--fixture PATH]

emits one JSON document: a locally generated tone/silence pacing smoke and
the fixture report. Nothing in it is a live-provider or real-speech claim.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "PacedChunk",
    "pace_pcm16",
    "PartialTranscript",
    "FinalTranscript",
    "EndpointDecision",
    "TranscriptError",
    "TurnMeasurement",
    "measure_turn",
    "speech_end_from_vad",
    "wer",
    "percentiles",
    "FixtureCase",
    "Fixture",
    "load_fixture",
    "evaluate_fixture",
    "main",
]

DEFAULT_FIXTURE = Path(__file__).resolve().parents[3] / "benchmark" / "fixtures" / "streaming_stt_synthetic_v0.json"
_BYTES_PER_SAMPLE = 2  # PCM16 mono


# --- pacing ---------------------------------------------------------------------


@dataclass(frozen=True)
class PacedChunk:
    pcm: bytes
    audio_time_s: float
    """Sample-time offset of the chunk's first sample from the start of audio."""
    emitted_at_s: float
    """Monotonic time the chunk was released, relative to the pacing start."""


def pace_pcm16(
    pcm: bytes,
    sample_rate: int,
    *,
    chunk_ms: int = 20,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[PacedChunk]:
    """Yield PCM16 mono chunks no earlier than their sample-time offset.

    Pacing is by sample time on the injected monotonic clock. If the
    consumer falls behind, chunks are released immediately (never dropped)
    so the audio timeline stays intact. The last chunk may be short.
    """

    if type(sample_rate) is not int or sample_rate <= 0 or sample_rate % 100:
        raise ValueError(f"sample_rate must be a positive int multiple of 100 Hz, got {sample_rate!r}")
    if type(chunk_ms) is not int or chunk_ms <= 0:
        raise ValueError(f"chunk_ms must be a positive int, got {chunk_ms!r}")
    if len(pcm) % _BYTES_PER_SAMPLE:
        raise ValueError(f"PCM16 mono byte length must be even, got odd length {len(pcm)}")

    chunk_bytes = sample_rate * chunk_ms // 1000 * _BYTES_PER_SAMPLE
    start = monotonic()
    for offset in range(0, len(pcm), chunk_bytes):
        audio_time_s = offset / _BYTES_PER_SAMPLE / sample_rate
        lag = (start + audio_time_s) - monotonic()
        if lag > 0:
            sleep(lag)
        yield PacedChunk(pcm=pcm[offset : offset + chunk_bytes], audio_time_s=audio_time_s, emitted_at_s=monotonic() - start)


# --- events -----------------------------------------------------------------------


def _check_time(at_s: Any, what: str) -> float:
    if isinstance(at_s, bool) or not isinstance(at_s, (int, float)) or not math.isfinite(at_s) or at_s < 0:
        raise ValueError(f"{what} must be a finite non-negative number of seconds, got {at_s!r}")
    return float(at_s)


@dataclass(frozen=True)
class PartialTranscript:
    at_s: float
    text: str

    def __post_init__(self) -> None:
        _check_time(self.at_s, "partial at_s")


@dataclass(frozen=True)
class FinalTranscript:
    at_s: float
    text: str

    def __post_init__(self) -> None:
        _check_time(self.at_s, "final at_s")


@dataclass(frozen=True)
class EndpointDecision:
    """Explicit end-of-turn decision. Distinct from any transcript segment."""

    at_s: float

    def __post_init__(self) -> None:
        _check_time(self.at_s, "endpoint at_s")


@dataclass(frozen=True)
class TranscriptError:
    at_s: float
    kind: str = "error"
    detail: str = ""

    def __post_init__(self) -> None:
        _check_time(self.at_s, "error at_s")


Event = PartialTranscript | FinalTranscript | EndpointDecision | TranscriptError

_EVENT_TYPES: dict[str, type] = {
    "partial": PartialTranscript,
    "final": FinalTranscript,
    "endpoint": EndpointDecision,
    "error": TranscriptError,
}


def event_from_dict(raw: dict[str, Any]) -> Event:
    kind = raw.get("type")
    cls = _EVENT_TYPES.get(kind)
    if cls is None:
        raise ValueError(f"unknown event type {kind!r}; expected one of {sorted(_EVENT_TYPES)}")
    fields = {k: v for k, v in raw.items() if k != "type"}
    return cls(**fields)


# --- measurement ---------------------------------------------------------------------


@dataclass(frozen=True)
class TurnMeasurement:
    status: str
    """ok | no_output | error | timeout | invalid_timing"""
    transcript: str
    ttfs_s: float | None
    last_final_at_s: float | None
    endpoint_at_s: float | None
    premature_endpoint: bool | None
    """None when no endpoint decision was observed."""
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def speech_end_from_vad(*, vad_stop_s: float, stop_delay_s: float) -> float:
    """Annotated speech end = VAD stop minus the VAD's configured stop delay."""

    _check_time(vad_stop_s, "vad_stop_s")
    _check_time(stop_delay_s, "stop_delay_s")
    end = vad_stop_s - stop_delay_s
    if end < 0:
        raise ValueError(f"speech end {end:.3f}s is before the start of audio")
    return end


def measure_turn(
    *,
    speech_end_s: float,
    events: Iterable[Event],
    finalization_timeout_s: float | None = None,
) -> TurnMeasurement:
    """Measure one turn from its annotated speech end and observed events.

    ``finalization_timeout_s`` bounds the drain window after speech end:
    nonempty finals received after ``speech_end_s + timeout`` are dropped,
    mirroring a bounded drain; any such late final makes the turn ``timeout``
    even if an earlier segment arrived on time (the transcript is incomplete).
    Statuses other than ``ok`` carry no latency at all (``ttfs_s`` is None),
    so failures never enter percentiles as zero.
    """

    speech_end = _check_time(speech_end_s, "speech_end_s")
    if finalization_timeout_s is not None:
        _check_time(finalization_timeout_s, "finalization_timeout_s")
    events = list(events)

    # Events are receipt events on one monotonic timeline, so they must be
    # chronological. Validate the whole list FIRST: a decreasing timestamp
    # means nothing on the list can be trusted (no transcript, no TTFS,
    # unknown endpoint). Equal timestamps keep iterable order. Never sort.
    for i in range(1, len(events)):
        if events[i].at_s < events[i - 1].at_s:
            return TurnMeasurement(
                status="invalid_timing",
                transcript="",
                ttfs_s=None,
                last_final_at_s=None,
                endpoint_at_s=None,
                premature_endpoint=None,
                detail=(
                    f"event {i} at {events[i].at_s:.3f}s precedes event {i - 1} at "
                    f"{events[i - 1].at_s:.3f}s; events must be chronological"
                ),
            )

    endpoint = next((e for e in events if isinstance(e, EndpointDecision)), None)
    endpoint_at = endpoint.at_s if endpoint else None
    premature = None if endpoint is None else endpoint.at_s < speech_end

    finals = [e for e in events if isinstance(e, FinalTranscript) and e.text.strip()]
    deadline = None if finalization_timeout_s is None else speech_end + finalization_timeout_s
    late = [f for f in finals if deadline is not None and f.at_s > deadline]
    finals = [f for f in finals if deadline is None or f.at_s <= deadline]
    transcript = " ".join(f.text.strip() for f in finals)
    last_final_at = max((f.at_s for f in finals), default=None)

    def _result(status: str, ttfs: float | None = None, detail: str = "") -> TurnMeasurement:
        return TurnMeasurement(
            status=status,
            transcript=transcript,
            ttfs_s=ttfs,
            last_final_at_s=last_final_at,
            endpoint_at_s=endpoint_at,
            premature_endpoint=premature,
            detail=detail,
        )

    error = next((e for e in events if isinstance(e, TranscriptError)), None)
    if error is not None:
        return _result("error", detail=f"{error.kind}: {error.detail}".rstrip(": "))
    if late:
        # A pre-deadline segment is not a completed turn when a later final
        # misses the drain window. Never reward truncated output as fast TTFS.
        return _result("timeout", detail=f"{len(late)} nonempty final(s) after the {finalization_timeout_s}s drain window")
    if not finals:
        return _result("no_output", detail="no nonempty final transcript")
    ttfs = last_final_at - speech_end
    if ttfs < 0:
        return _result("invalid_timing", detail=f"last final at {last_final_at:.3f}s precedes speech end {speech_end:.3f}s")
    return _result("ok", ttfs=ttfs)


# --- scoring ---------------------------------------------------------------------------

_WORD = re.compile(r"[a-z0-9']+")


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate via word-level Levenshtein distance.

    Same normalization and algorithm as ``benchmark/audio_harness/score.py``
    (pinned by a parity test); duplicated here because app code must not
    import from the repo-root ``benchmark`` tree.
    """

    ref = _WORD.findall(reference.lower())
    hyp = _WORD.findall(hypothesis.lower())
    if not ref:
        return 0.0 if not hyp else 1.0
    previous = list(range(len(hyp) + 1))
    for i, ref_word in enumerate(ref, start=1):
        current = [i] + [0] * len(hyp)
        for j, hyp_word in enumerate(hyp, start=1):
            cost = 0 if ref_word == hyp_word else 1
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
        previous = current
    return previous[-1] / len(ref)


def percentiles(values: Iterable[float]) -> dict[str, Any]:
    """Nearest-rank P50/P95/P99 with the count they were computed from.

    Every value must be a measured, finite, non-negative number. Bad samples
    raise ``ValueError`` rather than being filtered: the caller decides what
    is a sample (``None`` = unmeasured, never passed here).
    """

    ordered = sorted(_check_time(v, "percentile sample") for v in values)
    if not ordered:
        return {"count": 0, "p50": None, "p95": None, "p99": None}

    def rank(p: float) -> float:
        return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]

    return {"count": len(ordered), "p50": rank(50), "p95": rank(95), "p99": rank(99)}


# --- fixture ------------------------------------------------------------------------------


@dataclass(frozen=True)
class FixtureCase:
    case_id: str
    speaker: str
    tags: tuple[str, ...]
    truth_transcript: str
    truth_intent: str | None
    speech_end_s: float
    observed_intent: str | None
    events: tuple[Event, ...]
    finalization_timeout_s: float | None


@dataclass(frozen=True)
class Fixture:
    fixture_id: str
    train_speakers: frozenset[str]
    test_speakers: frozenset[str]
    cases: tuple[FixtureCase, ...]
    finalization_timeout_s: float | None
    meta: dict[str, Any] = field(default_factory=dict)


def load_fixture(path: Path | str) -> Fixture:
    """Load a labeled fixture; reject any train/test speaker overlap."""

    raw = json.loads(Path(path).read_text())
    split = raw.get("speaker_split") or {}
    train = frozenset(split.get("train") or ())
    test = frozenset(split.get("test") or ())
    overlap = train & test
    if overlap:
        raise ValueError(f"speaker split leaks train speakers into test: {sorted(overlap)}")
    known = train | test
    default_timeout = raw.get("finalization_timeout_s")
    cases = []
    seen: set[str] = set()
    for c in raw.get("cases") or ():
        case_id = c["case_id"]
        if case_id in seen:
            raise ValueError(f"duplicate case_id {case_id!r}")
        seen.add(case_id)
        if c.get("speaker") not in known:
            raise ValueError(f"case {case_id!r} names speaker {c.get('speaker')!r} not in the speaker split")
        truth, observed = c["truth"], c["observed"]
        cases.append(
            FixtureCase(
                case_id=case_id,
                speaker=c["speaker"],
                tags=tuple(c.get("tags") or ()),
                truth_transcript=truth.get("transcript", ""),
                truth_intent=truth.get("intent"),
                speech_end_s=_check_time(truth["speech_end_s"], f"{case_id} speech_end_s"),
                observed_intent=observed.get("intent"),
                events=tuple(event_from_dict(e) for e in observed.get("events") or ()),
                finalization_timeout_s=c.get("finalization_timeout_s", default_timeout),
            )
        )
    if not cases:
        raise ValueError("fixture has no cases")
    return Fixture(
        fixture_id=raw.get("fixture_id", Path(path).stem),
        train_speakers=train,
        test_speakers=test,
        cases=tuple(cases),
        finalization_timeout_s=default_timeout,
        meta={k: raw[k] for k in ("purpose", "private_data", "asr_evidence") if k in raw},
    )


def _split_of(fixture: Fixture) -> Callable[[FixtureCase], str]:
    """Derive each case's split from the declared speaker sets; reject leaks/unknowns.

    Runs at ``evaluate_fixture`` entry so a directly constructed ``Fixture``
    gets the same guarantees as a loaded one. An unknown speaker is an error,
    never defaulted to test.
    """

    overlap = fixture.train_speakers & fixture.test_speakers
    if overlap:
        raise ValueError(f"speaker split leaks train speakers into test: {sorted(overlap)}")
    for case in fixture.cases:
        if case.speaker not in fixture.train_speakers and case.speaker not in fixture.test_speakers:
            raise ValueError(f"case {case.case_id!r} names speaker {case.speaker!r} not in the speaker split")
    return lambda case: "train" if case.speaker in fixture.train_speakers else "test"


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Reduce scored rows to one summary. Reused for all-case and per-split views.

    Counts are turns. Rates use all attempted turns; an empty row set yields
    zero counts and null rates (never a divide-by-zero or an invented zero).
    ``ttfs_s`` percentiles use measured values only: ``None`` is unmeasured
    and stays represented in attempt/status counts, not as a sample.
    Endpoint coverage is separate from transcript success: a failed turn can
    still have a scored endpoint, and an ``ok`` turn can have none.
    """

    attempted = len(rows)
    status_counts = Counter(r["status"] for r in rows)
    usable = status_counts["ok"]
    wers = [r["wer"] for r in rows if r["wer"] is not None]
    intent_matches = sum(1 for r in rows if r["intent_match"] is True)
    endpoint_scored = sum(1 for r in rows if r["premature_endpoint"] is not None)
    premature = sum(1 for r in rows if r["premature_endpoint"] is True)
    return {
        "attempted": attempted,
        "status_counts": dict(sorted(status_counts.items())),
        "usable_output_rate": usable / attempted if attempted else None,
        "intent_exact_matches": intent_matches,
        "intent_exact_match_rate": intent_matches / attempted if attempted else None,
        "mean_wer_over_usable": (sum(wers) / len(wers)) if wers else None,
        "premature_endpoint_count": premature,
        "ttfs_s": percentiles(r["ttfs_s"] for r in rows if r["ttfs_s"] is not None),
        "failure_count": attempted - usable,
        "wer_sample_count": len(wers),
        "endpoint_scored_turns": endpoint_scored,
        "endpoint_unknown_turns": attempted - endpoint_scored,
        "premature_endpoint_rate_over_scored": premature / endpoint_scored if endpoint_scored else None,
    }


def evaluate_fixture(fixture: Fixture) -> dict[str, Any]:
    """Score every case. Rates use all attempted cases; percentiles use measured timings only.

    Rows carry an explicit ``split`` derived from the fixture's speaker sets.
    ``by_split`` reuses the same reducer per split; it is a speaker-holdout
    view of hand-labeled timelines, not a training or real-speech claim.
    """

    split_of = _split_of(fixture)
    rows = []
    for case in fixture.cases:
        m = measure_turn(
            speech_end_s=case.speech_end_s,
            events=case.events,
            finalization_timeout_s=case.finalization_timeout_s,
        )
        usable = m.status == "ok"
        rows.append(
            {
                "case_id": case.case_id,
                "speaker": case.speaker,
                "split": split_of(case),
                "tags": list(case.tags),
                "status": m.status,
                "ttfs_s": m.ttfs_s,
                "premature_endpoint": m.premature_endpoint,
                "transcript": m.transcript,
                "wer": wer(case.truth_transcript, m.transcript) if usable else None,
                "intent_match": (
                    case.observed_intent == case.truth_intent if case.truth_intent is not None else None
                ),
                "detail": m.detail,
            }
        )

    return {
        "fixture_id": fixture.fixture_id,
        **_summarize(rows),
        "speakers": {"train": sorted(fixture.train_speakers), "test": sorted(fixture.test_speakers)},
        "by_split": {name: _summarize([r for r in rows if r["split"] == name]) for name in ("train", "test")},
        "cases": rows,
    }


# --- CLI smoke --------------------------------------------------------------------------------


def _synthetic_pcm(sample_rate: int, tone_ms: int, silence_ms: int, hz: float = 440.0) -> bytes:
    """Local PCM16 mono tone followed by silence. Non-speech; timing only."""

    samples = bytearray()
    for n in range(sample_rate * tone_ms // 1000):
        v = int(0.3 * 32767 * math.sin(2 * math.pi * hz * n / sample_rate))
        samples += v.to_bytes(2, "little", signed=True)
    samples += bytes(_BYTES_PER_SAMPLE * (sample_rate * silence_ms // 1000))
    return bytes(samples)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline streaming-STT timing smoke (synthetic, no provider).")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--tone-ms", type=int, default=200)
    parser.add_argument("--silence-ms", type=int, default=100)
    args = parser.parse_args(argv)

    sample_rate = 16000
    pcm = _synthetic_pcm(sample_rate, args.tone_ms, args.silence_ms)
    wall_start = time.monotonic()
    chunks = list(pace_pcm16(pcm, sample_rate))
    wall_s = time.monotonic() - wall_start

    report = {
        "kind": "synthetic_timing_smoke",
        "real_speech": False,
        "live_provider": False,
        "note": (
            "Locally generated tone/silence paced at sample time plus hand-labeled synthetic "
            "timelines. This is a contract smoke, not ASR evidence, not a provider ranking, "
            "and not a real-audio latency claim."
        ),
        "pacing": {
            "sample_rate": sample_rate,
            "audio_s": len(pcm) / _BYTES_PER_SAMPLE / sample_rate,
            "chunks": len(chunks),
            "chunk_ms": 20,
            "wall_s": round(wall_s, 4),
            "last_chunk_emitted_at_s": round(chunks[-1].emitted_at_s, 4) if chunks else None,
        },
        "fixture_report": evaluate_fixture(load_fixture(args.fixture)),
    }
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
