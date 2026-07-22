# Parker Voice Benchmark

Local-only benchmark scaffold for PRA-43. (Earlier drafts used the legacy name ParkinsClaw.)

## Run evaluator

```bash
python benchmark/evaluate_v0.py \
  --gold benchmark/data/dev_v0.jsonl \
  --predictions benchmark/submissions/example_predictions.jsonl \
  --output benchmark/results/example_result.json
```

## v0 task

Transcript in → structured intent/slots/safety JSON out.

This legacy v0 task is deliberately transcript-first and synthetic-only. New audio-derived metadata fixtures live in `audio_repair_autodata_v0` below; raw public/private audio is not committed to the repo.

## Parker task taxonomy fixtures

`data/parker_tasks_v0.jsonl` holds synthetic fixtures for the broader Parker task taxonomy (speech repair, family messages, reminders, appointment prep, exercises, playlists, research, item search, non-response escalation, and unsafe/safety-red-team requests). Schema and consistency rules: `docs/task-taxonomy.md`, validated by `tasks_v0.py` and `backend/tests/test_parker_task_fixtures.py`.

These fixtures cover the Confirm/Act/Escalate stages of the product loop; the transcript benchmark above covers Understand.

## Run task-taxonomy evaluator

```bash
python3 benchmark/evaluate_tasks_v0.py                 # text summary, rule-based baseline
python3 benchmark/evaluate_tasks_v0.py --json          # machine-readable output
python3 benchmark/evaluate_tasks_v0.py --predictions my_preds.jsonl
python3 benchmark/evaluate_tasks_v0.py --write-report  # benchmark/reports/task_taxonomy_eval_*.{md,json}
make eval-tasks                                        # from repo root
```

Metrics: route accuracy, action-type accuracy, escalation precision/recall, refusal recall, clarify recall, repair-choice coverage. Safety-critical misses (gold `refuse`/`human_approval`/`escalate` predicted as anything else) are counted and listed case-by-case, never blended into aggregate accuracy. The current 24-fixture set includes red-team boundaries for medication changes, medical advice, emergency-service substitution, private credentials/sensitive notes, purchases, non-response escalation, and attempts to bypass the message-confirmation gate.

The shipped baseline is deterministic, safety-first keyword routing. As of the Night4 report-freshness cleanup, it keeps clear action keywords intact despite effortful filler and scores the 24-fixture synthetic task set at 100% route/action accuracy with 0 unsafe misses. It exists to prove the harness and freshness gates, not to claim product performance.

## Run interactivity evaluator

`data/parker_interactivity_v0.json` is a synthetic multi-turn trace eval tied to Parker and the Thinking Machines interactivity criteria. It covers repair under uncertain/effortful speech, changed-mind interruption handling, confirmation-before-action, exact readback-to-action binding, none-of-these interruption during confirmation, caregiver UI clarity, latency/turn count, unsafe-action suppression, and local outbox reversibility/cancel-only steering.

```bash
python3 benchmark/evaluate_interactivity_v0.py              # text summary, reference synthetic trace
python3 benchmark/evaluate_interactivity_v0.py --json       # machine-readable output
python3 benchmark/evaluate_interactivity_v0.py --predictions my_trace_predictions.json
python3 benchmark/evaluate_interactivity_v0.py --write-report
make eval-interactivity                                    # from repo root, reference trace
make eval-demo-interactivity                               # Parker-generated local demo trace
make eval-degraded-input-replay                            # degraded-input repair vs no-repair + one-shot baselines
make eval-caregiver-state-legibility                       # caregiver review-state proxy vs raw chat-only baseline
make eval-claim-metric-map                                 # public claim→metric overclaim guard
make eval-construct-validity                               # construct-validity matrix: citable evidence vs research gaps
make eval-repair-quality-rubric                            # repair-choice proxy rubric: generic fallback must stay non-citable
make eval-audio-autodata                                   # metadata-only audio-derived ASR/repair Autodata fixtures
make eval-release-readiness                                # one-command public-claim evidence/readiness rollup
```

The default `reference synthetic trace` is the ideal fixture trace; use `--predictions` to score Parker runs or other agents. Safety-critical misses for confirmation gates (including readback/action mismatches and none-of-these interruptions), local outbox reversibility, and unsafe-action suppression are counted separately from ordinary latency/UI failures.

`benchmark/demo_interactivity_predictions_v0.py` generates a current-product trace from Parker's deterministic local surfaces (repair tool, `TextSession`, capture/resolve/stage/confirm/execute pipeline, demo seed, caregiver review feed) and writes `benchmark/reports/parker_demo_interactivity_predictions_latest.json` plus demo-specific eval reports. As of the 2026-07-17 confirmation-interruption pass, this Parker-generated trace scores 9/9 synthetic current-product scenarios with 0 unsafe misses. In addition to changed-mind and local-outbox cancellation, it mutates a pending recipient after Parker's readback and proves the stale spoken “yes” is rejected; it also proves “none of these” cancels the read-back target, requests a bounded restatement, survives a stale execute attempt, leaves no local/external action, and is caregiver-legible as cancelled rather than pending.

## Run degraded-input replay evaluator

`data/degraded_input_replay_v0.json` is the Night4 Claw/adversarial-review smoke check for Parker's interactivity evidence: one pre-registered quantitative interaction metric, `intent_recovery_accuracy_delta_vs_non_interactive`, on synthetic held-out degraded/effortful-speech transcript inputs, plus a stronger secondary one-shot keyword comparator for caveating the weak no-repair baseline.

```bash
backend/.venv/bin/python benchmark/evaluate_degraded_input_replay_v0.py --json
backend/.venv/bin/python benchmark/evaluate_degraded_input_replay_v0.py --write-report
make eval-degraded-input-replay
```

It compares three baselines on the same cases:

- `non_interactive_no_repair`: no repair loop; degraded input stalls at “please repeat”.
- `one_shot_keyword_baseline`: no repair loop, but a one-shot explicit-cue transcript classifier tries to infer reminder/message intent; current smoke result recovers 2/3 synthetic cases.
- `parker_repair_protocol`: current deterministic Parker `TextSession` repair-choice path with a one-number user repair selection; current smoke result recovers 3/3 synthetic cases.

This is **not** real Parkinson's audio evidence and should not be overclaimed. It exists to keep public claims honest: no “Parker improves interactivity” sentence should survive unless it maps to an emitted metric, a baseline, a safety gate, and a caveat.

## Run audio Autodata repair evaluator

`data/audio_repair_autodata_v0.json` is the first repo-side bridge from the nightly audio loop into deterministic evals. It stores **metadata-only** fixtures derived from synthetic Parker command audio and public corpus ASR hypotheses; raw public audio remains in Operations artifacts and is not committed. Each case records source/provenance, ASR hypotheses, clean/oracle intent, weak/current behavior, expected repair choice, final confirmation/no-action target, safety label, and grading rubric.

```bash
python3 benchmark/evaluate_audio_repair_autodata_v0.py --json
python3 benchmark/evaluate_audio_repair_autodata_v0.py --write-report
make eval-audio-autodata
```

Current v0 coverage is 37 accepted fixtures, 6 explicitly held candidate notes, and 1 rejected-candidate ledger row: 10 synthetic audio-derived Parker command/control cases, 27 public corpus audio-derived ASR failure cases, 28 hard-negative/no-action cases, 5 source-oracle holds where the public source transcript/intent must be scored separately from runtime ASR, and 1 wake/addressed-to-me held row for ambient SLURP audio that currently causes nuisance generic repair choices. Held candidates remain outside the accepted denominator until their blocker is resolved. Rejected rows also stay outside that denominator, but retain the full reviewed data contract and a failure-mode label so near-duplicates and low-value episodes are counted instead of repeatedly rediscovered. Coverage includes safety-critical regressions for lost negation, no/go control phrases, no-context one-word controls, no-context cancel-message controls, device/media/settings controls without an approved room/TV/app/device context, EasyCall stop/speakerphone source-oracle control holds that require active context/alternate input, private-finance requests plus ASR erasure, public medical-ASR diagnosis/treatment/medication-instruction hard negatives, command-like/repetitive hallucinations, transcript-backed dysarthric read-sentence no-action, EasyCall emergency/cancel source-oracle no-action, health-adjacent mobility wording, real SLURP play-music clips that pin media-specific repair choices instead of generic reminder/message fallback, SLURP n-best named-track repair where a cleaner alternate ASR repairs a corrupted song title, a public SLURP concert-ticket case that separates read-only lookup from a human-approval purchase hold without checkout or capture, and a public SLURP weather query that pins n-best place/entity repair before a read-only answer without claiming a live fetch. This is pipeline/autodata fixture coverage only; it is not clinical evidence, patient evidence, public-data licensing approval, or ASR performance proof.

Before an accepted candidate is suggested for append, `benchmark/audio_autodata_promoter.py` emits an advisory diversity review against existing fixtures. Its deterministic score makes overlap in source, transcript tokens, intent/action family, safety label, confusion pairs, and weak-path failure mode visible, with the three closest fixture IDs and `accept_review`, `hold_review`, or `reject_review`. Hold/reject recommendations stop automatic append suggestions but do not replace human judgment. For reviewed scale/overlap rejections that should stay out of the repo ledger, a full `operations_rejected_candidate` contract is schema-checked and reported separately as `tracked_operations_only`, including normalized failure-mode counts, with no append or denominator delta. Duplicate rejection IDs or source/transcript rows within the same plan are reported as `duplicate` and excluded from those counts, so repeated packaging cannot inflate the local failure taxonomy. Scalar-only rejection notes remain blocked because they cannot prove provenance, repair choices, safety, or rubric completeness. This tooling protects denominator and rejection-history hygiene; it is not a quality, ASR, or clinical metric.

## Run wake/addressed-to-me audio-context evaluator

`data/wake_context_audio_v0.json` is the first metadata-only eval for the wake/addressed-to-me context seam. It uses public SLURP/DynamicSuperb/FSC/MInDS/EkaCare ASR hypotheses from the nightly audio loop and routes them through the actual `TextSession` with an explicit `UtteranceContext`: ambient room speech should be silent no-op, wake-confirmed conversation should route to the no-side-effect answer lane, wake-confirmed action requests should remain confirmation/repair gated, clear controls should require approved active context, and medical/private-finance boundaries should still refuse after wake. Raw public audio remains in Operations and is not committed.

```bash
python3 benchmark/evaluate_wake_context_audio_v0.py --json
python3 benchmark/evaluate_wake_context_audio_v0.py --write-report
make eval-wake-context
```

Current v0 coverage is 14 metadata-only fixtures: 13 public-audio-derived and 1 clearly synthetic audio-derived case. They include 3 ambient no-op cases, 2 direct wake-confirmed answer/conversation cases, 1 wake-confirmed media repair case, 2 selected wake-confirmed informational n-best repairs (`Orange, Texas` weather/place recovery and `Martin Jackson` vs `Michael Jackson` person-name choice), 1 explicitly user-confirmed local research handoff from the person-name case, 1 wake-confirmed settings/device context-required case, 2 wake-confirmed safety-boundary refusal cases (medical instruction and private finance), 1 wake-confirmed local reminder capture that still requires the normal confirmation pipeline before execution, 1 read-only ticket lookup, and 1 public-audio ticket-acquisition request held at the family/human-approval boundary with no capture or purchase. Informational repair selections are evaluated through the answer turn; the person-name case also evaluates the separate handoff opt-in and resulting local `ready` card. Query selection alone creates no card. Cards expose only query, selected interpretation, provenance status, local state, and a read-only risk label on the authenticated caregiver surface, with complete/cancel controls and no browser/fetch/send/purchase/submission/account-change capability. The keyless local answer remains an honest stub with no live-fetch claim. Every wake fixture requires a non-empty weighted rubric whose weights sum to 1.0. This is a routing/repair/follow-up-seam check only; it is not wake-word detection accuracy, generic entity resolution, real-world UX proof, clinical evidence, ASR-performance proof, or licensing approval.

## Run claim→metric map evaluator

`data/parker_claim_metric_map_v0.json` binds Parker's current public claims (README, launch post) to concrete report paths, metric IDs, baselines, safety gates, and caveats. It is a release overclaim guard, not a new performance claim.

```bash
python3 benchmark/evaluate_claim_metric_map_v0.py --json
python3 benchmark/evaluate_claim_metric_map_v0.py --write-report
make eval-claim-metric-map
```

The evaluator currently checks four release-critical claims: real-audio repair recovery (58.3% → 76.3% with repair, 82.0% with n-best, on the 333-clip manifest with reality-grounded degradations, 0 unsafe), brain-lane keyless red-team safety (10/10 routed, 0 unsafe), the audio-autodata fixture pipeline (37/37 accepted, 0 unsafe), and caregiver state legibility (10/10 vs 0/10, including four local research-card lifecycle/privacy tasks grounded in sanitized metadata from one reviewed public-audio repair episode). A claim only passes if its referenced reports exist, every required metric assertion passes, and the claim remains caveated with no raw audio or private data.

## Run construct-validity matrix evaluator

`data/parker_construct_validity_matrix_v0.json` separates what public release copy may cite now from open research gaps. Current citable constructs must point to emitted synthetic/local reports, baselines, safety gates, caveats, known limitations, and upgrade paths. Research-gap rows are intentionally non-citable: they keep realtime audio/latency and human-graded repair quality out of current proof claims.

```bash
python3 benchmark/evaluate_construct_validity_matrix_v0.py --json
python3 benchmark/evaluate_construct_validity_matrix_v0.py --write-report
make eval-construct-validity
```

The evaluator currently reports 6 constructs: 4 citable with caveats, 2 explicit research gaps, 14 report-backed assertions, and 0 failures. Passing means only that public release copy distinguishes synthetic/local evidence from open research gaps; it is not real-world clinical, audio, or patient proof.

## Run caregiver-state legibility proxy

`data/caregiver_state_legibility_v0.json` defines ten review-state tasks for pending actions, queued local outbox, approved-local outbox, cancelled audit rows, review-only non-response candidates, the visible demo safety contract, and ready/completed/cancelled/redacted-query local research cards. The four research-card tasks use synthetic lifecycle/privacy observations while retaining the full sanitized source transcript, same-clip ASR disagreement, scenario/intent, weak-vs-strong path, repair choices including none-of-these, expected confirmation/local transition, safety label, and weighted rubric from the reviewed public SLURP person-name episode. The evaluator compares Parker's structured review UI/state-card observation against a raw chat-only baseline.

```bash
python3 benchmark/evaluate_caregiver_state_legibility_v0.py --json
python3 benchmark/evaluate_caregiver_state_legibility_v0.py --write-report
make eval-caregiver-state-legibility
```

This is a construct-validity bump for Parker's caregiver/operator legibility claim: current proxy result is Parker review UI 10/10 tasks vs raw chat-only 0/10, with 0 unsafe misses and four audio-grounded lifecycle/privacy tasks. The redaction contrast requires both query and selected ASR interpretation to be absent while lifecycle and redaction audit remain visible. It is **not** a caregiver usability study, human-graded evidence, ASR-performance proof, or real family data; no raw audio is committed.

## Run release-readiness rollup

`benchmark/evaluate_release_readiness_v0.py` is the one-command briefing layer above the individual honesty-guard evals. It fails closed on missing/malformed required reports, stale source-report dates, re-runs the claim→metric overclaim guard and construct-validity matrix guard, and emits the exact safe claim line plus required caveat that public release copy (README, launch post) can carry.

```bash
python3 benchmark/evaluate_release_readiness_v0.py --json
python3 benchmark/evaluate_release_readiness_v0.py --write-report
make eval-release-readiness
```

`make eval-release-readiness` refreshes the task taxonomy, Parker-generated demo interactivity, degraded-input replay, caregiver-state legibility proxy, claim→metric map, construct-validity matrix, and repair-quality rubric reports before writing the rollup, so public-claim metrics do not silently survive from an older run. (Reports are written under `benchmark/reports/release_readiness_eval_*`; historical dated `grant_readiness_eval_*` reports from the retired grant lane remain in place as records but are no longer read.)

Passing means only that the current synthetic/local reports are safe to cite with caveats. It does **not** establish real-world, clinical, patient, audio, emergency-readiness, or private-data proof.

## Verify scheduled-reality provenance

`backend/app/parker/scheduled_reality.py` is a fail-closed verifier for a
nightly Operations receipt. A narrow, no-agent scheduler wrapper mints an HMAC
envelope containing its job ID, fire time, nonce, and expected checkout SHA;
the report/agent process must never receive that key. A trusted post-run wrapper
then calls `verify_scheduled_reality` and supplies the key only to the verifier.

The verifier independently checks a clean checkout against both the
scheduler-authorized SHA and an independently pinned approved SHA, a valid
scheduler envelope in its fire window, an atomic single-use nonce claim in a
trusted ledger outside the repository and inbound tree, a recent input under an
allowlisted inbox outside the repository, coherent wall/monotonic clock
movement, and a successful one-step state delta tied to the input hash. Checkout
identity and cleanliness observations share a 2-second deadline and a 64 KiB
combined stdout/stderr budget; output is captured incrementally, and timeout,
overflow, malformed output, or process failure emits a named unverified git
observation instead of blocking or buffering without limit.
Envelope, input, pre/post state, and nonce-ledger paths are traversed
component-by-component with descriptor-relative `O_NOFOLLOW`; regular evidence
is read once from a stable descriptor, capped at 1 MiB, and hashed/timestamped
from that same read. State evidence must expose bounded scalar status/error,
SHA-256, and signed-64-bit sequence fields before any of those values can be
reflected in a receipt; malformed giant integers fail closed rather than raising.
Scheduler job IDs and nonces are bounded ASCII identifiers. The receipt emits a
nonce fingerprint instead of the raw nonce and never emits the inbound filename.
Nonce consumption is the final verifier acknowledgement: malformed or transient
evidence stays unverified without advancing the ledger, so the same signed run
can be retried after its evidence is repaired. Only a run that passes every
other assertion may atomically claim the nonce. The verifier fsyncs both the
tombstone and its containing ledger directory before reporting success, so a
completed claim is durable across a crash; later replay still fails closed.
Missing, malformed, symlinked, unstable, hard-linked, or oversized evidence emits
`verdict: unverified` and `provenance_complete: false`. The receipt omits absolute
paths, inbound filenames, raw nonces, HMAC tokens, keys, and unvalidated state
values.

```bash
backend/.venv/bin/pytest backend/tests/test_scheduled_reality.py -q
```

The tests are the mandatory negative controls: old SHA, absent scheduler
envelope, replay of the same signed envelope, an oversized signed nonce, a nonce
ledger inside the repo, repo fixture input, symlinked or hard-linked final input,
a symlinked parent input path, symlinked envelope, state, or nonce-ledger paths,
oversized input, malformed giant state integers, oversized reflected state
status, frozen wall clock, missing state delta, eager nonce consumption on failed
evidence, a nonce claim without directory durability, dirty checkout, and
bounded git observation failures must all remain
unverified, while one isolated synthetic
Operations-shaped run qualifies once without reflecting its nonce or inbound
filename. This proves the verifier contract only. Until a trusted wrapper owns a
protected nonce ledger and scheduler key and an actual scheduled event passes it,
Parker still has no genuine scheduled-production provenance receipt.

### Evaluate the no-agent wrapper contract

`make eval-scheduled-wrapper` checks three synthetic control-plane traces around
the verifier: one final-ack success, one worker failure that retains pending
state, and one verifier rejection that also remains retryable. Five checks per
trace fail closed on scheduler-key or envelope exposure to the worker,
pre-completion verifier handoff, worker-writable nonce-ledger scope, eager
nonce/ack advancement, forged lifecycle actors/targets/fields, and receipts that
reflect keys, tokens, raw nonces,
paths, URLs, command output, or more than 16 KiB.

The fixture and evaluator are public-safe contract evidence only. They use no
live key, do not configure or invoke cron, do not deploy an OS-account wrapper,
and do not turn any historical job status into trusted provenance. A real
deployment still requires separate scheduler/verifier ownership, a protected
external pending/nonce/ack store, an unprivileged worker identity, and one
actual verifier-passing scheduled fire.

```bash
make eval-scheduled-wrapper
```

### Run the inactive subprocess/ownership harness

`make eval-scheduled-wrapper-harness` executes one real synthetic worker under
the current unprivileged account with an exact scrubbed environment, closed
extra descriptors, a one-second deadline, and a 16 KiB combined-output cap. In
an OS temporary directory it creates synthetic pending, ledger, and
acknowledgement state, then validates current-owner identity, exact `0600`/`0700`
modes, regular file types, and descriptor-relative no-follow opens. Symlinked
state, group-readable acknowledgement state, a hanging worker, and an output
flood are pinned as negative controls. Timeout/overflow signals the original
worker process group and reaps the direct worker. This inactive same-account
harness does not contain a descendant that deliberately creates a new session;
the fixed synthetic worker does not spawn one.

The harness does not inspect or change cron, read a credential, mint an
envelope, call the production verifier, or enforce a distinct scheduler/wrapper
OS account. It emits only bounded synthetic metadata and reports zero live
activations. Separate production identities, protected runtime state, complete
stack review, OS-enforced descendant containment, and one genuine
verifier-passing scheduled event remain blocked.

```bash
make eval-scheduled-wrapper-harness
```
