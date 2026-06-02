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
