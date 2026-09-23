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

## Round 3 — the verify round verified the verifiers (2026-08-31)

A fresh-context attacker ran executed repros against round 2's own fixes
and confirmed three defects IN THE FIXES (all fixed and pinned in
`test_realtime_workers.py`):

- 🐛 the balanced card partitioned one 20-row window, so twenty chatty
  sessions still evicted every durable fact — durable and episodic now
  have separate queries;
- 🐛 marker-stripping ran one pass, so "LOOKUP RES&lt;marker&gt;ULT>>>" could
  reassemble its own fence — now stripped to a fixpoint;
- 🐛 the first-person medical swap tripped on ordinary life ("increase my
  step count" burned the redirect) — the swap now applies only when the
  question names medicine; the answer-side guard backstops the rest.

Also confirmed clean: the text lane already used the effective-proposable
set; a gateway-enabled appointment_note skill stages end-to-end; the tool
schemas stay unmutated (deepcopy). Live re-probes: the levodopa education
scenario now lands (plain-words explanation, boundary intact, and the
model routed "save a question" through a stageable reminder that staged).

Residual: one unreproduced full-suite blip in ~7 marathon runs (log lost;
6 consecutive clean runs after) — if it resurfaces, apply the hygiene
pattern (drain waits / right observable).

## Round 4 — the review dimension: seeing the session (2026-08-31)

The human-testing flywheel slice added the deck's first read-side
dimension: `test_scenarios_review.py` (5 scenarios) drives full fake
sessions and then pins what the review surface (`/parker/sessions/ui`)
serves back — the journal timeline with ack/inject latencies, the
staged actions, the guard-trip record, tomorrow's card preview with the
minted memory named, and one-tap feedback filed against a turn.

Building the dimension flushed three latent harness/product races (all
fixed and pinned):

- 🐛 **Silent-rollback write loss** — on the harness's shared StaticPool
  connection, a concurrent session's rollback could discard another
  thread's committed-looking write with NO exception (traced live:
  `record_event_sync` printed count=0 immediately after its own commit).
  Retry wrappers never fired because nothing raised. All realtime local
  writers now verify-after-commit and raise so the bounded idempotent
  retries actually retry. This is the likely mechanism behind the
  original CI finalize flake class, not just the journal's.
- 🐛 **Cancelled threadpool awaits ABANDON their thread** (measured:
  anyio to_thread under plain asyncio returns from cancellation
  immediately while the thread runs on) — so `_active_bridges == 0`
  never proved quiescence and fixture `drop_all` could race a live
  thread ("database table is locked"). The bridge now counts in-flight
  DB threads with an atomic pending→running/aborted handoff
  (`_tracked_thread`), and both harness teardowns wait for
  `_inflight_db_threads == 0` too.
- 🐛 **S09 double-count window** — `end` could cancel response handling
  between the screen-mirror await and the per-turn state reset, so
  shutdown's dangling-turn capture re-appended an already-recorded turn
  (surfaced as "50 exchange(s)" in the 49-turn cap scenario once the
  journal write widened the window). Turn state is now consumed
  synchronously before any await; a mid-recording turn can never look
  unanswered to shutdown.

### Round 4's verify round (four fresh-context attackers, executed repros)

- 🐛 **Duplicate journal rows under retry** — a transient failure of the
  verify-after-commit READ, after a successful commit, made the retry
  re-insert the same (call, seq) row (executed repro: two identical turn
  rows). `record_event_sync` is now idempotent per (call, seq).
- 🐛 **The last answered turn could vanish from the timeline** — a
  hang-up cancelling the screen-mirror await ate the turn's journal
  write while the summary still counted the exchange (executed repro:
  "1 exchange(s)" beside an empty timeline). The turn's journal writer
  is now stashed before any await and flushed by shutdown.
- 🐛 **The dangling-turn journal was gated behind the 50-exchange cap**
  — in long sessions, his unanswered last words were exactly the record
  that got dropped. The journal write now runs outside the cap.
- 🐛 **"Live" forever** — accidental-tap/mumble-only sessions never got
  an `ended_at`, so the feed badged them live permanently. Finalize now
  always closes the session; it still invents no summary and mints no
  memory (the two cross-session invisibility pins updated to say so).
- 🐛 **The detail endpoint could block ~30 s** on the inline gateway
  probe; the next-card preview now runs the same builder over the
  DB-backed sources only, and says so on the page.
- Accepted, not fixed: past the cap the feed's turn count (uncapped
  journal) sits beside the summary's "50 exchange(s)" (capped memory
  bound) — both numbers are honest about different things; and `ack_ms`
  measures function-call→ack-item (sub-ms by design) — "asked →
  injected" is the number that means something to a human.

## Rounds 5+ (appended by the gauntlet)

### Contract change — one owner, one line (2026-09-01)

Companion power is now server-authoritative and single-owner
(`app/parker/companion_power.py`): a second screen's claim is refused
409 `elsewhere` while the owner is listening; the same owner's reconnect
supersedes its old line (`revoked`/`superseded`); power off from any
screen revokes every line (`revoked`/`power_off`). `MAX_LIVE_BRIDGES = 2`
exists only for the handover overlap. `test_scenarios_concurrency.py`
was rewritten from "two simultaneous lines" to handover, refusal, and
per-line isolation across sequential lines (8 scenarios, C01–C08).
Round 2's screen-identity design question (Sarah's phone overwriting
Ravi's tablet row) is closed by the contract — there is no second live
line to overwrite it.
