# Capability brief: Communication / Perception

*Can he successfully communicate with Parker?* This is the wedge: if Parker understands him significantly better than existing assistants, there is immediate value.

## Mission

Dad is understood — on hard days, from the recliner, near a TV — and when Parker isn't sure, it repairs with concrete choices instead of guessing or making him repeat himself. Communication success matters more than transcription accuracy.

## Sub-capabilities

- Impaired/effortful speech recognition (local ASR + personal-lexicon biasing).
- Uncertainty estimation and repair: n-best hypotheses → 2–3 typed choices + "none of these".
- Addressed-to-me detection: directed speech vs ambient room/TV audio.
- Confirmation before side effects; changed-mind handling.
- Response delivery: spoken answers, appropriate length, big-type dad screen.

## Baseline (2026-08-17)

- **Real-audio harness** (`make eval-audio-real`, report 2026-07-03, base model, 333 synthetic + public-corpus clips): intent recovery 58% raw → 76% with repair → **82% with repair + n-best**. Zero unsafe captures. **Zero pilot clips** — every number is measured on strangers' voices (TORGO, EasyCall, SJTU, synthetic commands).
- **Repair machinery is the strongest link:** typed candidates with safety screening, none-of-these, two bounded informational n-best repairs (place and person-name disagreement), model-backed with deterministic keyless fallback.
- **Live defects (from the author's own dogfood run):** ambient speech draws nuisance repair choices because wake/addressed-to-me gating is deferred; bare "No" with a stale draft routes to changed-mind revision instead of no-op/cancel.
- Oracle caveat: harness scores route-agreement against the oracle transcript, not human-judged understanding; repair-choice *quality* is explicitly non-citable (rubric eval: `Quality proof claim allowed: False`).

## Maturity

**Level 2 (Reliable)** on synthetic/public evidence; **Level 1 (Functional)** as experienced by the actual person — personalization today is a hand-typed lexicon, and nothing is measured on his voice. **Target: Level 3 (Personalized)** — performance measurably improves for him specifically.

## Metrics

| Metric | Instrument | Today |
| --- | --- | --- |
| Understood first try or after one repair (**target ≥90%**) | Real-audio harness, on pilot clips once they exist | 82% on synthetic/public clips |
| First-attempt understanding rate | Interaction outcome layer (EXP-001 Phase A) | not measurable |
| Repair success rate (clarify → confirmed intent) | Outcome layer + repair events | not measurable |
| Nuisance-choice rate on ambient audio | Outcome layer (`ambient_noop` vs spurious clarify) | known bad, unquantified |
| Per-turn latency (ASR + routing → speech start) | talk-loop latency line | printed, not tracked |

## Current weaknesses

1. No measurement on the person this is for (pilot protocol written, consented, never executed).
2. 8-point gap to target even on strangers' voices; only one ASR model size evaluated.
3. Ambient audio poisons the experience without wake gating — blocks always-on deployment.
4. Personalization limited to a static hand-typed lexicon.

## Experiment backlog

- **EXP-001 Phase A** (active): wake gating, live-defect fixes, outcome instrumentation, pilot recording → personalized baseline.
- Lexicon ablation on pilot clips (with/without — never computed).
- ASR model-size matrix (`MODELS`/`NBEST` beyond `base`) for the accuracy/latency trade.
- Realtime speech model opt-in trial (family-administrator choice) once the local baseline is measured.

## Evidence

- `benchmark/reports/audio_real_eval_latest.md` (2026-07-03).
- Deterministic suite: wake-context 14/14, autodata repair fixtures, degraded-input replay 3/3 (n=3) — harness proofs, not product performance claims.

## Open questions

- What does *he* consider "understood"? (Success = got what he wanted with less frustration, not WER.)
- Is repair-question fatigue a real cost? (Clarification rate needs a ceiling, not just a success rate.)
- Where does the ≥90% target actually bind on his voice — ASR, intent routing, or repair-choice quality?
