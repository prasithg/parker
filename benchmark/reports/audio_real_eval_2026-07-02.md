# Real-audio eval — ASR -> TextSession route equivalence

Date (UTC): 2026-07-02  
Clips scored: 250 (excluded: {'no_oracle_label': 1, 'unknown_provenance': 0, 'missing_file': 0, 'private_excluded': 0})  
Oracle: self-referential — route(oracle transcript) vs route(ASR transcript).

| model | intent clips | recovery (no repair) | recovery (repair) | recovery (repair+n-best) | unsafe (worst mode) | median WER | mean WER | s/clip |
|---|---|---|---|---|---|---|---|---|
| base | 91 | 0.4945 | 0.8242 | 0.8242 | 0 | 0.25 | 2.1486 | 0.888 |

Recovery is measured only on the intent lane (clips whose clean-path
routing captures an action). Refusal/no-op lanes gate on unsafe
captures instead. Per-condition, per-language, and per-dataset
breakdowns are in the JSON report. Synthetic and public-corpus clips
are both included; treat dysarthric-English coverage caveats in the
breakdowns as binding when citing numbers.

Gate (0 unsafe captures in every mode, all models): PASS
