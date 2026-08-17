# EXP-001 — Can Parker understand Dad better than Google Home, and learn from its mistakes?

- **Status:** active (Phase A) — opened 2026-08-17
- **Capabilities:** Communication/Perception (primary), Learning (primary), Memory (touchpoints)
- **Decision:** pending evidence

## Hypothesis

Parker can understand Parkinson's-affected speech more reliably than Google Home by asking clarification questions when uncertain and learning from the resulting corrections — and that learning is measurable within weeks of daily use.

## Why phased

Every metric in this experiment requires real daily usage, and Parker is pre-deployment (acceptance-tested only by its author). Phase A makes the experiment runnable; Phase B runs the learning intervention. One experiment, because "everything required to run it becomes the first implementation plan" — deployment is part of everything required.

## Phase A — In the room, instrumented

**Intervention:** deploy Parker into daily use and label every interaction with an outcome.

Slices (dependency order):

1. **Wake/addressed-to-me gating.** Ambient room/TV speech must silent-no-op; directed speech must not require ceremony. Blocks always-on use (known live defect: ambient nuisance repair choices).
2. **Live-defect fixes.** Bare "No" with a stale draft → no-op/cancel, not changed-mind revision. Sweep the author's dogfood notes for others.
3. **Interaction outcome layer.** Per-interaction outcome derived from existing pipeline/repair/audit rows, one of: `understood_first_try` / `repaired_success` / `repair_abandoned` / `wrong_action` / `refused_safety` / `no_response` / `ambient_noop`. Local weekly rollup artifact (aggregates only; rollups in home-local timezone, not UTC).
4. **Consent + learning-corpus retention design.** With dad and the family, at install: repair-event capture on, retention terms for confirmed correction pairs decided deliberately (distinct from the 30-day research-card redaction, which currently also clears linked repair-event text). Documented in the runbook and adaptation ladder.
5. **Home deployment.** Install on the home machine (unsigned-beta path documented), room placement, family walkthrough, Google Home coexistence (both available; no forced switch).
6. **Pilot recording session.** Execute [pilot-recording-protocol.md](../../pilot-recording-protocol.md) (20 utterances, consent-scripted, never yet run) → personalized manifest for the real-audio harness.

**Phase A exit criteria:**

- Parker available and listening ≥5 days/week in the living room.
- ≥20 directed interactions/week captured with outcome labels.
- Baseline computed: first-attempt understanding, repair success, nuisance-choice rate, unassisted-success rate.
- Pilot-clip manifest (≥20 clips) scored through the harness — the ≥90% capability target measured on his voice for the first time.

## Phase B — Close the learning loop

**Intervention:** stored corrections start changing future interpretation, and the change is measured.

7. **Adaptation-ladder rung 4:** mine repair events for words recurring in confirmed intents but missing from ASR output → suggest lexicon additions → family administrator approves. Nothing self-modifies.
8. **Lexicon ablation:** with/without-lexicon column in the harness on pilot clips (supported, never computed).
9. **Rung 5:** few-shot exemplars from his confirmed correction history in the repair-candidate prompt.
10. **Learning-velocity + repeated-error trend report:** weekly; per learned correction, exposures from first failure to stable success; regression check re-runs pilot clips on every adaptation change.

**Phase B exit criteria (after ~4 weeks of Phase B usage):**

- Repeated-error rate trending down across weeks.
- ≥5 distinct learned corrections demonstrably reused (correction applied without re-asking).
- Measured lexicon/exemplar delta on pilot clips, with no regression on the rest of the set.
- First learning-velocity number established (no external baseline exists — establishing it is a result).

## Evaluation

| Metric | Source |
| --- | --- |
| First-attempt success; repair success; unassisted-success rate | Outcome layer weekly rollup |
| Clarification rate + repair-fatigue ceiling | Outcome layer |
| Repeated-error rate; corrections reused; learning velocity | Correction tracking (Phase B) |
| ≥90% understood first-try-or-one-repair | Real-audio harness on pilot clips |
| **Google Home comparison** | Revealed preference: family notes which assistant he addresses (displacement over weeks) + monthly structured side-by-side — the same 10 everyday requests to both devices, scored understood/succeeded. No lab ceremony. |

**Success (his terms, not WER):** Dad gets what he wanted with less frustration — and week 6 Parker is measurably better *for him* than week 1 Parker.

## Boundaries

- Aggregates only in anything public; transcripts and audio stay in the house (existing policy; pilot clips live in the Operations workspace).
- No medical claims or inferences from trends; declining metrics are an engineering signal, never a health observation.
- All existing safety gates unchanged: confirmation before side effects, no send path, prohibited types refused.
- Adaptation stays administrator-approved (rung 4/5); nothing self-modifies.

## Evidence

*(append as it lands — dates, aggregate numbers, report links)*

- 2026-08-17: pre-experiment audit — 82% repair+n-best on 333 synthetic/public clips (2026-07-03 report), 0 pilot clips, repair events write-only, Parker pre-deployment.

## Decision

*(keep / modify / abandon / investigate further — with the learning, when evidence is in)*
