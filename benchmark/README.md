# ParkinsClaw Voice Benchmark

Local-only benchmark scaffold for PRA-43.

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

`data/parker_tasks_v0.jsonl` holds synthetic fixtures for the broader Parker task taxonomy (speech repair, family messages, reminders, appointment prep, exercises, playlists, research, item search, non-response escalation, unsafe requests). Schema and consistency rules: `docs/task-taxonomy.md`, validated by `tasks_v0.py` and `backend/tests/test_parker_task_fixtures.py`.

These fixtures cover the Confirm/Act/Escalate stages of the product loop; the transcript benchmark above covers Understand.

## Run task-taxonomy evaluator

```bash
python3 benchmark/evaluate_tasks_v0.py                 # text summary, rule-based baseline
python3 benchmark/evaluate_tasks_v0.py --json          # machine-readable output
python3 benchmark/evaluate_tasks_v0.py --predictions my_preds.jsonl
python3 benchmark/evaluate_tasks_v0.py --write-report  # benchmark/reports/task_taxonomy_eval_*.{md,json}
make eval-tasks                                        # from repo root
```

Metrics: route accuracy, action-type accuracy, escalation precision/recall, refusal recall, clarify recall, repair-choice coverage. Safety-critical misses (gold `refuse`/`human_approval`/`escalate` predicted as anything else) are counted and listed case-by-case, never blended into aggregate accuracy.

The shipped baseline is deterministic keyword rules. It intentionally over-clarifies on disfluent-but-clear requests (~80% route accuracy, 0 unsafe misses); it exists to prove the harness, not to claim product performance.
