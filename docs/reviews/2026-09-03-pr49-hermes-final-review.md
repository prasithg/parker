# PR #49 final Hermes review

Date: 2026-09-03 UTC

## Decision

**PASS for exact-head CI, packaging, and Pras's human/device test. Do not merge yet.**

Reviewed candidate: `26309965a5f57ac18516e3d89379c0480bc212cd`

Backend application tree: `ab6e9245241e69fb0e5c8313a6056bea231ceaca`

Baseline for the resumed review: `342edfe`

The candidate was clean when frozen. This review does not claim hosted CI, a final-SHA package, microphone/TCC behavior, real room acoustics, Dad's speech, or Reachy motion quality; those gates follow this artifact.

## Reviewer provenance

Claude/Fable Max credits were unavailable, and no Anthropic key path was used. Hermes therefore used isolated same-family GPT-5.6 SOL reviewers through the configured OpenAI Codex provider, split by concern:

1. power lifecycle and concurrency;
2. My Day, due-time, and spoken session-end correctness;
3. release evidence, public docs, and gate integrity.

The implementer verified each reported blocker and owned every fix and rerun. Reviewer self-reports were not treated as execution receipts without local reproduction.

## Review rounds

| Round | Frozen source | Power | My Day/session | Evidence/docs |
| --- | --- | --- | --- | --- |
| Initial resumed review | `0548113` | NEEDS_FIX | NEEDS_FIX | NEEDS_FIX |
| First fix review | `3a8833b` | PASS | NEEDS_FIX | PASS |
| Final narrow review | `2630996` | unchanged from prior PASS | PASS | PASS for the scoped delta |

The final narrow reviewer independently confirmed that the three new regressions pass on `2630996` and fail against `3a8833b`, then reran the exact six-file focused deck: 185 passed.

## Blocking findings and closure

| Concern | Confirmed blocker | Closure |
| --- | --- | --- |
| Power | ON persistence held the state lock, so OFF could not flip memory immediately | Transition tickets; persistence serialized outside the state lock; blocked and queued claim pins |
| Power | Superseded bridges disappeared from a later OFF's accounting | Retired registrations remain power-owned until actual quiescence |
| Power | Revoke could strand the realtime supervisor in startup awaits | Separately owned/cancellable bridge supervisor; four startup-phase pins |
| Power | Worker timeout could report provider quiescence while its thread still ran | Provider computation tracked separately from cancellable delivery |
| Power | Suspended result delivery could resume after revoke | Delivery task cancellation plus closed-state fences after awaited boundaries |
| Session end | Cancelled, incomplete, failed, or silent responses retired result obligations | Obligations requeue until completed observable speech; browser Stop remains silent |
| My Day | Recent undated pending reminders were omitted | Same two-day policy as undated staged reminders; honest recorded/not-set wording |
| My Day | Medication defaults compared UTC digits with local schedules | Named home-zone normalization in the shared tracker, including aware inputs |
| My Day | Nested summaries made global omission totals false | Structured represented-record cardinality |
| My Day/realtime | Arbitrary search text could alias the reserved My Day result key | Structurally disjoint `search:` and My Day namespaces |
| My Day | Safety-filtered records vanished from counts and could mint a false empty-day claim | Generic counted blocked-record presence, without exposing filtered content |
| Evidence | The older `ea0ef7c` package could be mistaken for final-head evidence | Ledger explicitly invalidates it for final testing |
| Evidence | Wake-soak miss attribution contradicted the committed payload | Five Fred misses split accurately across 120/175 wpm, plus one TV false wake |
| Public docs | Shipped wake/realtime behavior was also listed as future | Stack table now distinguishes current opt-in lanes from later provider/household evidence |

## Verification receipts

- `make test`: **1383 passed**, with the two known deprecation warnings.
- Exact focused deck: **185 passed in each of five consecutive runs**:
  `backend/.venv/bin/python -m pytest -q backend/tests/test_scenarios_concurrency.py backend/tests/test_companion_power.py backend/tests/test_realtime_workers.py backend/tests/test_my_day_worker.py backend/tests/test_realtime.py backend/tests/test_scenarios_session_end.py`
- `cargo test --manifest-path desktop/src-tauri/Cargo.toml`: **17 passed**.
- `node --test backend/tests/js/*.spec.js`: companion **56/56**, lab **3/3**, expression **48/48**.
- `make eval-voice-scenarios`: **98 passed**.
- Scheduled-wrapper contract: **15/15**; inactive harness: **9/9**, one bounded worker, zero live activations.
- Canonical UTC report families: all ten dated JSON/Markdown pairs are byte-identical to their `latest` mirrors.
- Read-only release-readiness evaluator: **PASS**, no blocking failures.
- `make eval-repair`: executed but honestly skipped because `ANTHROPIC_API_KEY` was unavailable; it contributes no model-backed evidence.
- `git diff --check`: clean.

## Remaining human/device gates

- Packaged real-mic power OFF: microphone/TCC indicator, live audio, wake socket, realtime socket, and provider work all stop; restart remains OFF.
- Intended `OK, thanks` ends after completed work; mid-conversation thanks, a long pause, or thanks while a result is pending does not.
- Dad-like wake recall, paused greeting, same-breath request, ambient TV, Stop, and barge-in in the room.
- VoiceOver order, no-WebGL behavior, reduced-motion runtime, and physical Reachy motion quality.
- The checked-in wake soak remains **FAIL**: five Fred paused-greeting misses and one ambient-TV false wake. This is reported, not waived.

Hosted exact-head CI and the SHA-bound packaged probe must pass before this candidate is handed to Pras for those tests.
