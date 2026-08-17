# Weekly operating-system log

Newest first. Five questions per week: capability / hypothesis / experiment / evidence / learning. Public-safe: aggregates and decisions only, no transcripts or private family detail.

## Week of 2026-08-17

- **Capability:** all (strategy reset).
- **Hypothesis:** modeling Parker as capabilities-with-slopes, driven by experiments, will produce more user value than continuing feature/eval slices.
- **Experiment:** adopted the capability model ([parker-strategy.md](../parker-strategy.md)); wrote briefs for the three priority capabilities; specced EXP-001 (phased: deploy+instrument, then close the learning loop).
- **Evidence (state audit):** North Star instrument reads 82% repair+n-best on 333 synthetic/public clips (2026-07-03) — zero pilot clips of the actual user. Repair events are write-only (no code reads them back); lexicon hand-typed, never mined; with/without-lexicon delta never computed. App acceptance-tested by its author only; wake gating deferred (ambient nuisance choices are a known live defect). Parker is pre-deployment.
- **Learning:** the binding constraint is real usage + instrumentation, not model capability. Metric hierarchy adopted: successful unassisted interactions (North Star), learning velocity + revealed preference (thesis), ≥90% understood (P1 capability target). Decisions: experiments live in-repo; EXP-001 is one experiment with two phases.
- **Next:** run EXP-001 Phase A slices (wake gating → live-defect fixes → outcome layer → consent design → deploy → pilot recording).
