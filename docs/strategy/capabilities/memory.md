# Capability brief: Memory

*What does Parker know about this person?*

## Mission

Parker maintains an accurate, evolving model of dad and his life — people, routines, preferences, corrections, history — and recalls the right piece at the right moment. Storage is not the goal; useful recall is.

## Sub-capabilities

- Person model: identity, relationships, preferences, routines, goals, important events.
- Communication memory: learned speech patterns, confirmed corrections, vocabulary.
- Episodic memory: what happened, what was asked, what worked.
- Retrieval into the live loop: memory shaping interpretation, repair choices, and timing.
- Provenance: family-stated vs observed vs inferred, visible and correctable by the family.

## Baseline (2026-08-17)

- **What exists is legacy-shaped:** `backend/app/memory/` is a call-era store — type-tagged text memories (`fact/preference/event/topic`) with `ilike` search and a "context for next call" builder wired to call logs and dose logs. Effectively inert in the Parker v0 loop.
- **Real memory-like state lives elsewhere:** family contacts allowlist (`PARKER_FAMILY_CONTACTS`), hand-typed personal lexicon, consent-gated repair events (write-only — see the [Learning brief](learning.md)), staged-action/audit history.
- **No person model.** No relationships, routines, or preferences shape any live decision.
- **Retention tension:** the 30-day research-card redaction also clears linked repair-event text — privacy policy currently deletes what memory would need, without a deliberate trade-off decision.

## Maturity

**Level 1 (Functional)** — things can be stored and found by substring; nothing is recalled at the right moment. **Target: Level 2–3** — a person model that demonstrably changes live behavior for him.

## Metrics

| Metric | Instrument | Today |
| --- | --- | --- |
| Useful memory retrieval rate (recall that changed an outcome) | Outcome layer + retrieval audit (EXP-002) | not measurable |
| Reference resolution ("call the one with the garden" → the right person) | Fixtures + outcome layer | no capability |
| Person-model coverage with provenance (fields filled, family-reviewed) | Caregiver review surface | no person model |
| Correction retention (confirmed pairs preserved per policy) | Repair-event store | at risk (30-day redaction) |

## Current weaknesses

1. Memory store is disconnected from the loop that needs it.
2. Zero structured knowledge of the person; everything personal is env-var config.
3. Retention policy inherited from a privacy slice, not designed for a learning companion.
4. No relationship-formation process — nothing initializes or grows the person model.

## Experiment backlog

- **EXP-002 (next up after EXP-001):** person-model v0 — "his file": family-seeded (relationship formation starts as a family interview, not an interrogation of dad), provenance-tagged, editable on the caregiver page; retrieved into interpretation for reference resolution and repair candidates.
- Micro-interview gap-filling ("You mention X often — who is that?") — only after trust in basic recall.
- Routine priors: use observed timing (evening loop, reminder confirmations) to time resurfacing.
- Episodic digest memory: does the family digest double as Parker's own episodic record?

## Evidence

None yet beyond code inspection. The capability has no eval lane — deliberately deferred until EXP-001's outcome layer exists to measure retrieval against.

## Open questions

- Schema: structured person model vs memory-file-with-conventions (OpenClaw-style)? Decide in EXP-002 design, biased toward whichever the family can read and correct.
- Consent framing for remembering (distinct from repair-capture consent): what does dad want Parker to know?
- How does memory stay honest — who corrects a wrong inference, and how fast does a stale fact decay?
