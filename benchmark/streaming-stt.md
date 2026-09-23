# Streaming-STT evaluation seam (offline, synthetic)

`backend/app/audio_harness/streaming_stt_eval.py` is a small, provider-independent
contract for measuring streaming-transcriber *turn* behaviour. It replays PCM16
mono audio paced by sample time and scores hand-labeled event timelines. It has
no provider adapter, no network path, and no dependency beyond the standard
library. It does not touch the production voice lanes (Realtime semantic VAD
stays as it is; this seam is not a reason to shorten pauses).

## Vocabulary

Borrowed from the public pipecat-ai/stt-benchmark methodology
(`docs/measuring-ttfs.md`, `pipeline/synthetic_transport.py`,
`observers/transcription_collector.py`) so Parker numbers stay comparable in
*definition*, never in *result*:

| Term | Definition here |
| --- | --- |
| speech end | Annotated end of user speech. From a VAD: `vad_stop - stop_delay` (`speech_end_from_vad`). In fixtures: a hand label. |
| TTFS | Time of the **last nonempty final** transcript minus speech end. Partials and empty finals never count. |
| endpoint decision | An explicit end-of-turn event. A final transcript segment is **not** an endpoint. |
| premature endpoint | Endpoint decision time < annotated speech end. |
| invalid timing | Either the event list is not chronological (a timestamp decreases), or the last final precedes speech end (negative TTFS). A failure status, never clamped to zero, never reordered. |

Statuses per turn: `ok`, `no_output`, `error`, `timeout`, `invalid_timing`.
A final after the drain deadline makes the entire turn `timeout`, even if an
earlier segment arrived on time; incomplete transcripts are not fast success.
Finals after a premature endpoint still contribute to transcript/TTFS: these
are separate measurements, so an `ok` timing can carry a premature flag.
The offline fixture parser rejects unknown event fields via `TypeError`.
Only `ok` turns carry a TTFS. Rates use **all attempted** turns; TTFS
percentiles (nearest-rank P50/P95/P99) report the count of measured timings
they were computed from and raise on any non-finite, negative, boolean or
non-numeric sample rather than filtering it.

### Clock integrity

Events are receipt events on one monotonic timeline and must be listed in
chronological order. `measure_turn` validates the whole list **first**: a
decreasing `at_s` yields `invalid_timing` with an empty transcript, no TTFS
and *unknown* endpoint timing, and takes precedence over provider error,
no-output and late-final conditions. The list is never sorted or repaired.
Equal timestamps keep iterable order (finals concatenate in that order; among
equal endpoint timestamps the first listed decision wins).

`invalid_timing` (either cause) counts in `attempted`, `status_counts` and
`failure_count`, and is excluded from the usable-output numerator, WER
samples, TTFS samples and (for the out-of-order cause) endpoint-scored turns.
A chronologically valid turn keeps a genuinely observed endpoint even when its
transcript dimension failed: an `error` or negative-TTFS turn can still be
endpoint-scored.

### Endpoint coverage

`premature_endpoint_count` alone cannot distinguish a measured zero from
missing observations, so every summary also reports, in turns:

| Field | Meaning |
| --- | --- |
| `endpoint_scored_turns` | Turns with an observed endpoint decision (`premature_endpoint` is true or false). |
| `endpoint_unknown_turns` | All remaining attempted turns (no decision observed, or list not chronological). |
| `premature_endpoint_rate_over_scored` | `premature_endpoint_count / endpoint_scored_turns`; `null` when nothing was scored. |

`endpoint_scored_turns + endpoint_unknown_turns == attempted` always holds.

### Summaries and splits

One reducer produces the all-case summary (top-level fields, unchanged order,
plus `failure_count` and `wer_sample_count`) and `by_split.train` /
`by_split.test`. Each row carries a `split` derived from the fixture's
`speaker_split`; evaluation rejects train/test overlap and any case speaker
outside both sets, also for a `Fixture` built in code (an unknown speaker is
never defaulted to test). An empty split reports zero counts and `null`
rates/percentiles. The split view is a speaker-holdout of hand-labeled
timelines: it is not a training claim and not real-speech evidence.

Intent exact-label match (observed vs. truth label) is reported separately from
WER, which is computed only on usable output with the same normalization as
`benchmark/audio_harness/score.py`.

## Fixture

`benchmark/fixtures/streaming_stt_synthetic_v0.json` holds hand-authored
timelines covering pause, restart, correction, negation, name, cancel,
no-output, error, timeout, invalid timing and a premature endpoint. It carries a
`speaker_split` (train/test); loading a fixture whose split overlaps raises.
These are timing/behaviour labels, not ASR evidence and not a Parkinson's
speech cohort. Eleven cases are a contract check, not a statistic: do not read
the smoke's percentiles as a ranking or a speed claim. The test suite pins the
fixture's SHA-256 so measurement changes cannot quietly relabel it.

## Commands

From `backend`:

```bash
python -m app.audio_harness.streaming_stt_eval            # JSON smoke to stdout
python -m app.audio_harness.streaming_stt_eval --fixture path/to/other.json
python -m pytest tests/test_streaming_stt_eval.py -q
```

The smoke generates a local 440 Hz tone plus silence (no audio committed), paces
it through the real monotonic clock, then evaluates the fixture. Its output is
labeled `synthetic_timing_smoke` with `real_speech: false` and
`live_provider: false`.

## Not included (deliberately)

- No live/async provider adapter, cancellation or deadline handling for a
  socket stream. When one is added it must be tested for cancellation,
  deadline, and finals arriving after the endpoint before it is trusted.
- No vendor ranking. The public Cekura/pipecat numbers are not a Parkinson's
  cohort and are not reproduced as Parker results.
