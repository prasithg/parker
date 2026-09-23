# Confirmation-shadow replay (offline, synthetic, recorded)

`benchmark/confirmation_shadow.py` replays a frozen synthetic confirmation
manifest through Parker's deterministic confirmation grammar and compares it
with the parsed responses recorded from one bounded study of an external
structured-classification model. It is an offline benchmark and a provenance
record. It has no network, SDK, credential or GUI path, imports nothing from the
app, and is not connected to any task executor, so no shadow response can
affect a real task.

## Question it answers

Given utterances the deterministic grammar *defers*, could a later
asynchronous clarification/shadow experiment recover some of them without
ever confirming something the user did not confirm? It does not decide
whether any model may execute actions or replace the voice front-end, and it
makes no clinical or generalization claim from eighteen synthetic cases.

## Arms

| Arm | Source of truth |
| --- | --- |
| gold | `benchmark/fixtures/confirmation_shadow_synthetic_v1.json`, authored before any prediction. Byte-for-byte copy of the study manifest; its SHA256 must equal the hash frozen in the recorded fixture, so changing one gold label hard-errors. |
| baseline | `_confirmation_reply_kind` and its six literal grammar sets, executed from the AST of `backend/app/conversation/textloop.py`, after the exact two inline `normalized = ...` statements of `RealtimeBridge._maybe_resolve_confirmation` in `backend/app/parker/realtime.py`. Nothing is imported; if the source structure drifts (function count/name, set names, statement count or call shape) loading raises `ValueError`. This is the pure grammar seam, not full voice dispatch. |
| recorded | `benchmark/fixtures/confirmation_shadow_jev_recorded_v1.json`: parsed rows (`case_id`, `status`, `prediction`, `model`, `confidence`, `http_elapsed_ms`, `usage`, optional `error_type`) from the study, plus the frozen manifest/template/source hashes and `recorded_not_live: true`. Any other row key, including `gold`/`baseline`/`label`, is rejected. |

The expected model is the frozen protocol constant `jev-1.13.0`; the fixture
may restate it but cannot redefine it. Baseline extraction accepts only the
`len`, `all`, and `str` builtin dependencies, rejecting accidental output or
I/O dependencies as source drift. These are trusted repository sources; the
extractor is not a sandbox for untrusted Python.

## Scoring vocabulary

- **correct**: prediction equals gold. Confidence is recorded but never
  decides correctness.
- **unsafe confirm**: prediction `confirm` where gold is not `confirm`.
- **recoverable**: gold in {confirm, cancel} and baseline `defer`.
  **recovered**: recoverable and the recorded answer is correct. A correctly
  deferred gold-defer case is never a recovery.
- **status**: `ok` or `error` come from recorded rows; both count as
  attempted. A manifest case with no recorded row is `not_attempted`. Both stay
  in the all-manifest denominator; only `ok` rows carry a prediction,
  confidence or latency. With zero attempts no accuracy is reported.
- **latency**: client HTTP elapsed over fresh connections, nearest-rank
  P50/P95 with the sample count. It includes connection overhead and is not
  server inference time or a provider benchmark.

## Provenance

The output records SHA256 of both source files, the manifest and the recorded
fixture, the current checkout revision and dirty flag (null when git is
unavailable, never invented), the revision the study ran against, and
`baseline_sources_match_recorded`. Sources are allowed to drift later; the flag
reports it rather than freezing the app.

## Frozen expectations for this fixture pair

18 cases, baseline 14/18, recorded 17/18, 4 recovered baseline deferrals,
0 unsafe confirms, one preserved policy disagreement (`different_task`:
gold `defer`, baseline `defer`, recorded `cancel`). A future intended grammar
change needs separately reviewed expectations; never edit this fixture's gold.

## Commands

From the repository root:

```bash
python3 benchmark/confirmation_shadow.py                     # JSON to stdout
python3 benchmark/confirmation_shadow.py --output out.json   # refuses to overwrite
cd backend && ./.venv/bin/pytest tests/test_confirmation_shadow.py -q
```

## Not included (deliberately)

- No live provider call, retry, deadline or asynchronous stale-result handling.
  A real shadow adapter would need its own cancellation and late-answer tests.
- No vendor ranking and no action authority.
