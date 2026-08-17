# Capability brief: Learning

*Does Parker measurably improve because he uses it?* This tests the deepest Parker thesis: the important property is not day-one capability but the slope of improvement.

## Mission

Turn interactions and outcomes into persistent improvement for this specific person — learn from corrections, reuse what was learned, prove the reuse worked, and never regress silently.

## Sub-capabilities

- Correction capture: degraded input → offered choices → confirmed intent (naturally labeled data).
- Correction reuse: learned pairs biasing future ASR, interpretation, and repair choices.
- Outcome feedback: every interaction labeled, so learning has a reward signal.
- Self-measurement: learning velocity, repeated-error tracking, regression detection.

## Baseline (2026-08-17)

**The loop is a C-shape, not a circle.** Following the [adaptation ladder](../../adaptation-ladder.md):

| Rung | Status |
| --- | --- |
| 1. Repair-event capture | Built — consent-gated, **default off**, so a stock install captures nothing |
| 2. Personal lexicon → ASR biasing | Built — but **hand-typed** by the family, never mined from data |
| 3. N-best repair choices | Built — the strongest link; recent months of work all improved this rung |
| 4. Lexicon mined from repair events | **Not built** (the cheapest loop-closing move, already specified) |
| 5. Few-shot exemplars from his history | Not built |
| 6. Per-user fine-tune corpus | Not built (needs its own consent conversation) |

- **Stored corrections are write-only.** No code path reads repair events back into interpretation; the only reader is the privacy redactor. The flywheel's fuel accumulates (when consented) and dead-ends.
- **Reuse has never been measured.** The harness accepts `--initial-prompt` for lexicon ablations; no report contains a with/without-lexicon delta. The ladder's own rule — "a rung earns its place only if the harness shows a delta" — has never been exercised.
- **Retention works against the corpus:** recent privacy slices added 30-day expiry/redaction that clears linked repair-event text. Right call for research cards; unexamined for learning data.

## Maturity

**Level 0–1** — Parker can collect the raw material but cannot yet improve from it. **Target: Level 3 (Personalized)** — improvement specifically for him, evidenced by trend, with **Level 5 (Self-improving)** as the long-term thesis.

## Metrics

| Metric | Instrument | Today |
| --- | --- | --- |
| **Learning velocity**: exposures from first failure → reliable success | Outcome layer + correction tracking (EXP-001 Phase B) | not measurable |
| Repeated-error rate (same miss recurring) | Outcome layer, weekly trend | not measurable |
| Corrections reused successfully (count, per week) | Reuse audit (rungs 4–5) | 0 by construction |
| Lexicon/exemplar delta on pilot clips | Real-audio harness ablation | never computed |
| Regression guard: new errors introduced by adaptation | Harness re-run per adaptation change | not in place |

## Current weaknesses

1. No reuse path — the defining gap; everything downstream of it is unmeasurable.
2. No outcome signal — without per-interaction outcomes there is no reward, only anecdotes.
3. Consent default-off + retention expiry mean even a deployed Parker would starve the flywheel unless the policy is deliberately set at install.
4. Adaptation can regress (a learned correction misfiring in new contexts); no guard exists.

## Experiment backlog

- **EXP-001 Phase B** (the capability's proving ground): rung 4 (mine → suggest → administrator approves), lexicon ablation, rung 5 (few-shot exemplars), learning-velocity trend report.
- Regression harness: every adaptation change re-runs pilot clips; a rung that helps one phrase but hurts others fails.
- Implicit-signal mining (abandonment, rephrasing) as secondary feedback — explicit corrections first.
- Rung 6 fine-tune corpus — gated on volume (hundreds of consented events) and a separate consent conversation.

## Evidence

None yet. This capability has never had a true experiment — by design it goes second, since it needs Communication/Perception's instrumentation to exist.

## Open questions

- Learning-corpus retention terms: what do dad and the family agree to keep, for how long, in what form (confirmed pairs vs full transcripts)?
- Human in the loop: rung 4 keeps the administrator approving every adaptation — at what maturity does Parker earn auto-apply for low-risk learning (Trust capability interaction)?
- What is a good learning velocity? No baseline exists anywhere — establishing the first number is itself the result.
