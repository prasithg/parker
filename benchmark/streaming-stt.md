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
| invalid timing | Last final precedes speech end (negative TTFS). Reported as a failure status, never clamped to zero. |

Statuses per turn: `ok`, `no_output`, `error`, `timeout`, `invalid_timing`.
A final after the drain deadline makes the entire turn `timeout`, even if an
earlier segment arrived on time; incomplete transcripts are not fast success.
Finals after a premature endpoint still contribute to transcript/TTFS: these
are separate measurements, so an `ok` timing can carry a premature flag.
The offline fixture parser rejects unknown event fields via `TypeError`.
Only `ok` turns carry a TTFS. Rates use **all attempted** turns; TTFS
percentiles (nearest-rank P50/P95/P99) report the count of valid timings they
were computed from.

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
the smoke's percentiles as a ranking or a speed claim.

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
