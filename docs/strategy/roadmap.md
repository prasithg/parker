# Parker roadmap

Not a feature Gantt. A rolling, evidence-gated sequence of experiments over the three priority capabilities ([Communication/Perception](capabilities/communication-perception.md), [Memory](capabilities/memory.md), [Learning](capabilities/learning.md)), re-planned by the weekly operating system as evidence lands.

## Metric hierarchy

| Tier | Metric | Instrument | Status |
| --- | --- | --- | --- |
| **North Star** | Successful interactions without human assistance (weekly rate + count) | Interaction outcome layer (to build — EXP-001 Phase A) | Not yet measurable: Parker is not deployed |
| **Thesis** | Learning velocity: exposures from first failure → reliable success | Outcome layer + correction-reuse tracking (EXP-001 Phase B) | Not yet measurable |
| **Thesis** | Revealed preference: which assistant he chooses (displacement vs Google Home) | Family observation + monthly structured side-by-side | Not yet measurable |
| **Capability (P1)** | Understood first try or after one repair ≥90% | Real-audio harness; personalized once pilot clips exist | 82% (repair+n-best, base model, 333 synthetic/public clips, 2026-07-03 — zero pilot clips) |

The ≥90% figure was previously framed as the North Star. It is now the Communication/Perception capability target: necessary, not sufficient. A Parker that understands 90% of utterances but never becomes more useful has failed the thesis.

## The strategic situation (2026-08-17)

Three facts dominate sequencing:

1. **Parker is pre-deployment.** The app is built and acceptance-tested — by its author, on the author's laptop. Dad has never used it. Every relationship metric needs daily real usage first.
2. **The learning loop is a C-shape, not a circle.** Repair events are captured (consent-gated, default off) but never read back; the lexicon is hand-typed, never mined; the with/without-lexicon delta has never been computed. Rungs 4–6 of the [adaptation ladder](../adaptation-ladder.md) are designed but unbuilt.
3. **The evidence layer is saturated while the product layer is idle.** Nine deterministic evals sit at 100%/PASS; the last month of commits was eval/privacy polish. The correction already named in [next-slices.md](../next-slices.md) — "usefulness first; evidence as guardrail" — now has a concrete owner: EXP-001.

## Now — EXP-001 Phase A: get in the room, instrumented

Goal: Parker running daily in front of dad, every interaction labeled with an outcome. Spec: [experiments/EXP-001-understand-and-learn.md](experiments/EXP-001-understand-and-learn.md).

Slices, in dependency order:

1. **Wake/addressed-to-me gating** — ambient speech currently draws nuisance repair choices (known live defect). Blocks daily always-on use.
2. **Live-defect fixes** — bare "No" with a stale draft routes to changed-mind revision instead of no-op/cancel (observed live).
3. **Interaction outcome layer** — per-interaction outcome row (understood first try / repaired success / repair abandoned / wrong action / refused / no response / ambient no-op) derived from existing pipeline + repair audit rows, plus a local weekly trend report. Powers every metric above.
4. **Consent + learning-corpus retention design** — family conversation; flip repair-event capture on at install; define retention for correction pairs deliberately (today's 30-day research-card redaction also clears linked repair-event text — a privacy win that quietly starves the flywheel).
5. **Home deployment** — install on the home machine (unsigned beta path documented), Google Home coexistence protocol, family walkthrough.
6. **Pilot recording session** — execute [pilot-recording-protocol.md](../pilot-recording-protocol.md) (written, consented, never run) → personalized harness manifest, so the ≥90% target is finally measured on the person it's for.

Exit: ≥5 days/week running, ≥20 directed interactions/week with outcomes, baseline metrics computed, pilot-clip manifest live.

## Next — EXP-001 Phase B: close the loop

7. **Ladder rung 4** — mine repair events → lexicon suggestions → administrator approves (nothing self-modifies).
8. **Lexicon ablation column** — with/without-lexicon delta in the real-audio harness (supported, never computed).
9. **Ladder rung 5** — few-shot exemplars from his confirmed corrections in the repair-candidate prompt.
10. **Learning-velocity + repeated-error trend report** — the thesis metric, weekly.

Exit: repeated-error rate declining; ≥5 distinct learned corrections reused successfully; measured lexicon delta on pilot clips.

## Later — candidates, chosen by evidence

- **EXP-002 (Memory):** person-model v0 ("his file": family-seeded, provenance-tagged, caregiver-reviewable) + retrieval into interpretation — reference resolution ("call the one with the garden"), routine priors for timing.
- **EXP-003 (Agency):** connect the real OpenClaw gateway — `media_playlist` on the TV and `open_links`, the first "Parker feels alive" skills. Architecturally complete, never called for real.
- **EXP-004 (Coaching):** evening recliner/TV loop value — does the offer/decline loop earn engagement or annoy?
- Realtime speech model opt-in trial; app signing/notarization when the Developer ID arrives.

## Explicit non-goals right now

- New eval lanes or fixture polish beyond what an active experiment needs.
- Capability agents, briefs for the other five capabilities, or any strategy artifact an experiment didn't demand.
- New action types past the graduation rule; smart-home/purchases/calendar writes; multi-tenant anything; voice cloning.

## Cadence

Weekly OS ([experiments/LOG.md](experiments/LOG.md)) → pick/adjust the active experiment → slices run as delegated sessions → evidence updates the briefs → merge to `main` on green milestones.
