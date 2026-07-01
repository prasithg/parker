# Real-audio eval — ASR -> TextSession route equivalence

Date (UTC): 2026-07-01  
Clips scored: 136 (excluded: {'no_oracle_label': 1, 'unknown_provenance': 0, 'missing_file': 0})  
Oracle: self-referential — route(oracle transcript) vs route(ASR transcript).

| model | intent clips | recovery (no repair) | recovery (repair) | recovery (repair+n-best) | unsafe (worst mode) | median WER | mean WER | s/clip |
|---|---|---|---|---|---|---|---|---|
| tiny | 11 | 0.6364 | 0.8182 | 0.8182 | 0 | 0.4 | 4.7599 | 0.735 |
| base | 11 | 0.7273 | 0.9091 | 0.9091 | 0 | 0.2857 | 3.7973 | 1.263 |
| small | 11 | 0.7273 | 0.9091 | 0.9091 | 0 | 0.2857 | 3.4092 | 3.861 |

Recovery is measured only on the intent lane (clips whose clean-path
routing captures an action). Refusal/no-op lanes gate on unsafe
captures instead. Per-condition, per-language, and per-dataset
breakdowns are in the JSON report. Synthetic and public-corpus clips
are both included; treat dysarthric-English coverage caveats in the
breakdowns as binding when citing numbers.

Gate (0 unsafe captures in every mode, all models): PASS
