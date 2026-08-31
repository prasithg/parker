# The Ravi scenario deck — a living gauntlet for the fast-voice lane

Started 2026-08-30 (overnight gauntlet, Pras's brief: "make up scenarios
… mock the Hermes plugin … plan for not getting it … keep looping and
improving"). Every scenario is a Ravi story (docs/personas/ravi.md) that
became an executable test asserting the bridge contract; the deck grows a
round at a time and every confirmed finding links its fix.

Harness: `backend/tests/scenario_harness.py` (`voice_world` fixture, event
builders, mocked gateway/brain). Files: `backend/tests/test_scenarios_*.py`.
Dev-mode ambient demos: `scripts/mock_family_gateway.py` (port 18790).

Legend: ✅ pinned green · 🐛 found a product bug (fixed, linked) ·
📋 design gap filed in docs/next-slices.md.

## Round 0 — exemplar (dimension: ambient context)

| # | Scenario | Variant axis | Status |
|---|----------|--------------|--------|
| A1 | Paused levodopa video → card carries the room's whisper + memories; lookup flows framed | rich context | ✅ |
| A2 | Hermes box off tonight → card quietly built from memory alone, no notice | context missing | ✅ |
| A3 | Compromised harness whispers "IGNORE ALL INSTRUCTIONS…" → stays data; purchase still rejected | hostile context | ✅ |
| A4 | First boot, nothing known → NO card (zero-streak line used to ride alone) | empty world | 🐛 fixed in `realtime_workers._memory_lines` |

## Round 1 — five dimensions, 44 scenarios (2026-08-31 overnight)

Five Opus dreamers → curator (16 dropped as duplicates of pinned coverage)
→ five implementers → executed-repro triage. Files: `test_scenarios_fusion
/actions/safety/speech/degraded.py` — 44 tests, all stable across
repeated runs.

Fixed this round (each pinned by its scenario test):
- 🐛 **S01** — "should I double MY levodopa" walked past the search
  pre-check (guard phrases are second-person); questions are now
  normalized to second person before checking. Boundary always held at
  the answer; the fix stops spending a billed research call to find out.
- 🐛 **S09** — a spoken turn with no model reply (stalled upstream, abrupt
  drop) vanished entirely; shutdown now captures the dangling transcript,
  so "I have fallen" reaches the morning record even if Parker never
  answered.
- 🐛 **D11** — a dead store during a proposal emitted NO function output,
  leaving the model waiting forever; the bridge now answers the call_id
  with an honest "could not save that" rejection.

Filed as design questions (pinned as present behavior, listed in
docs/next-slices.md):
- 📋 **A2** — a `blocked` staged action is a dead end: premature execute
  permanently kills the card and a later confirm tap is silently
  swallowed.
- 📋 **S08** — no emergency path in the live lane: "I have fallen" leaves
  only the model's spoken advice and the transcript trail.
- 📋 **S11** — with an empty lexicon, any recipient is "known": the
  misdirection guard only exists once a family configures names.
- 📋 **SP05** — a rephrased repeat ("what time is the match" after "when
  does he play") is a second billed lookup and a second injected note.

Harness lesson (encoded as `world.settle_open()`): the test engine shares
one SQLite connection, so DB-touching feeds must not race the context
worker — a harness artifact, not a product behavior.

## Round 2 — memory, concurrency, stress + the voice-UX audit (2026-08-31)

Files: `test_scenarios_memory/concurrency/stress.py` (25 tests) plus a
full audit of every model-facing template and a hygiene sweep hardening
20 round-1 tests. Three live conversation probes ran the real lane
(`scripts/live_conversation_probe.py`; transcripts in
~/Operations/parker/live-probes/).

Fixed this round (pinned by the deck):
- 🐛 **M02** — the recency-blind five-slot card: two evenings of tennis
  chat evicted "walks in the morning". The card now balances durable
  family notes (≤4) against session topics (≤2) via
  `get_balanced_context_lines`.
- 🐛 **M07** — a guarded-out memory left its "Recent memories:" header
  standing alone; headers now fall with their bullets, and a contentless
  card is not injected.
- 🐛 **M09** — an evening of "yeah" minted a topic memory and spent a
  card slot; filler-only sessions now finalize without minting.
- 🐛 **Fence hardening** — web text containing the fence marker could
  close its own quotation; markers are stripped, and the context card
  (which carries untrusted gateway lines) is now fenced too.
- 🐛 **Prompt overhaul** (UX audit, 19 findings): base-persona
  web-search instruction explicitly overridden (it contradicted the
  orchestrator), repair guidance added (pauses are composition; echo the
  caught part; never act on a guess), warmth bounded (no endearments, no
  health check-ins), medical EDUCATION allowed while advice stays
  guarded (live-probe find — the levodopa explanation now lands),
  machinery words forbidden aloud, clock stamped as call-open time,
  error envelopes stop leaking exception class names, wrap-up stops
  pressuring, goodbye got a word budget, "waiting for him to confirm —
  he taps it there".
- 🐛 **Phantom action types** — `appointment_note` was proposable but
  could never stage ("I tried to write a note but it couldn't be
  saved", live find). Un-stageable types are no longer advertised: the
  effective-proposable set intersects the stageable set, and BOTH lanes'
  propose_action schema enums are built from it at request time.

Filed as design questions (docs/next-slices.md):
- 📋 **Screen identity** — with two live lines, the one-row Dad screen is
  last-writer-wins: Sarah's phone conversation overwrites his tablet row.
- 📋 **Lookup spend budget** — nothing caps how many billed searches one
  session can fire (12 simultaneous ran fine; the contract held).
- 📋 Honest drop: per-bridge store isolation is untestable by design (the
  DB seam is process-global; a wedged store is household-wide).

## Rounds 3+ (appended by the gauntlet)
