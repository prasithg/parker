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

This is deliberately transcript-first and synthetic-only. No real patient PHI, no diagnosis claims, no public Hugging Face repos until approved.

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

The shipped baseline is deterministic keyword rules. It intentionally over-clarifies on disfluent-but-clear requests (~80% route accuracy, 0 unsafe misses); it exists to prove the harness, not to claim product performance.

## Run interactivity evaluator

`data/parker_interactivity_v0.json` is a synthetic multi-turn trace eval tied to Parker and the Thinking Machines interactivity criteria. It covers repair under uncertain/effortful speech, changed-mind interruption handling, confirmation-before-action, caregiver UI clarity, latency/turn count, unsafe-action suppression, and local outbox reversibility/cancel-only steering.

```bash
python3 benchmark/evaluate_interactivity_v0.py              # text summary, reference synthetic trace
python3 benchmark/evaluate_interactivity_v0.py --json       # machine-readable output
python3 benchmark/evaluate_interactivity_v0.py --predictions my_trace_predictions.json
python3 benchmark/evaluate_interactivity_v0.py --write-report
make eval-interactivity                                    # from repo root, reference trace
make eval-demo-interactivity                               # Parker-generated local demo trace
make eval-degraded-input-replay                            # grant-facing degraded-input repair vs no-repair baseline
```

The default `reference synthetic trace` is the ideal fixture trace; use `--predictions` to score Parker runs or other agents. Safety-critical misses for confirmation gates, local outbox reversibility, and unsafe-action suppression are counted separately from ordinary latency/UI failures.

`benchmark/demo_interactivity_predictions_v0.py` generates a current-product trace from Parker's deterministic local surfaces (repair tool, `TextSession`, capture/resolve/stage/confirm/execute pipeline, demo seed, caregiver review feed) and writes `benchmark/reports/parker_demo_interactivity_predictions_latest.json` plus demo-specific eval reports. As of the 2026-06-18 Night4 cancel-only steering pass, this Parker-generated trace scores 7/7 synthetic current-product scenarios with 0 unsafe misses: `TextSession` now cancels a prior local staged draft without duplicating it, still captures a revised reminder for changed-mind revisions, and can cancel a queued local outbox message before any external send path exists.

## Run degraded-input replay evaluator

`data/degraded_input_replay_v0.json` is the Night4 Claw/adversarial-review smoke check for the grant pitch: one pre-registered quantitative interaction metric, `intent_recovery_accuracy_delta_vs_non_interactive`, on synthetic held-out degraded/effortful-speech transcript inputs.

```bash
python3 benchmark/evaluate_degraded_input_replay_v0.py --json
python3 benchmark/evaluate_degraded_input_replay_v0.py --write-report
make eval-degraded-input-replay
```

It compares two baselines on the same cases:

- `non_interactive_no_repair`: no repair loop; degraded input stalls at “please repeat”.
- `parker_repair_protocol`: current deterministic Parker `TextSession` repair-choice path with a one-number user repair selection.

This is **not** real Parkinson's audio evidence and should not be overclaimed. It exists to keep the proposal honest: no “Parker improves interactivity” sentence should survive unless it maps to an emitted metric, a baseline, and a caveat.
