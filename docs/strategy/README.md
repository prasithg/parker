# Parker strategy

This directory is the operating system for Parker's development. It exists so that every session — human or agent — can answer "what are we trying to make Parker better at, and how will we know?" without re-deriving it.

Parker's organization model is [chairman + acting AI CEO](operating-model.md): Pras owns mission and reserved decisions; Hermes owns strategy, product/research operations, routine releases, and follow-through. Executive titles are accountable to shipped work and evidence, not agent theater.

## The model

Parker is developed as an entity leveling up **capabilities**, not software accumulating features:

```text
Vision → Capabilities → Experiments → Work → Evidence → better Experiments
```

- **Vision** — [parker-strategy.md](parker-strategy.md) (2026-08-17 baseline, stored verbatim). Changes rarely.
- **Operating model** — [operating-model.md](operating-model.md): nonprofit-minded mission, chairman/AI-CEO decision rights, product/data/model flywheel, programs, and cadence.
- **Capabilities** — living briefs in [capabilities/](capabilities/). Each has a mission, honest baseline, maturity level, metrics, weaknesses, and an experiment backlog. Only the three priority capabilities have briefs today; the other five (Reasoning, Coaching, Agency, Trust) ride on existing models and policies until they earn a program.
- **Experiments** — specs and evidence in [experiments/](experiments/). Experiments are the unit of progress; they run days-to-weeks and end in a keep/modify/abandon decision.
- **Work** — implementation slices, tracked as delegated coding sessions and logged in [../next-slices.md](../next-slices.md) as they ship.
- **First-user problem/value hypothesis** — [2026-08-29-problem-first-value-proposition.md](2026-08-29-problem-first-value-proposition.md): Dad's actual voice-assistant job, the Uri Levine problem-first lens, the truthful introduction, and the Patient Curiosity Loop that should earn the next home-use evidence.
- **Evidence** — eval reports in [../../benchmark/reports/](../../benchmark/reports/), experiment evidence sections, and the weekly log.

## Metric hierarchy

- **North Star:** successful interactions without human assistance — from real home usage, weekly.
- **Thesis metrics:** learning velocity (exposures from first failure to reliable success) and revealed preference (which assistant the person actually chooses to talk to).
- **Capability targets:** each brief carries its own, e.g. Communication/Perception's "understood first try or after one repair ≥90%".

Full hierarchy and sequencing: [roadmap.md](roadmap.md).

## Weekly operating system

Every week, the AI CEO answers six questions in [experiments/LOG.md](experiments/LOG.md):

1. **Capability** — what are we trying to improve?
2. **Hypothesis** — what do we believe will improve it?
3. **Experiment** — what are we doing to test that belief?
4. **Evidence** — what happened in reality?
5. **Learning** — what changed about our understanding of Parker?
6. **Chairman decision** — is there a mission, data, reputation, capital, or public-claim decision only Pras should make?

Fifteen minutes, honest answers, then pick the next experiment. This matters more than ticket completion.

## Guardrails on the framework itself

The real person comes first; the framework serves the experience (§16 of the vision doc). Concretely: three briefs not eight, one log file, no capability agents until more than one experiment runs concurrently, and no strategy artifact grows unless an experiment needed it.
