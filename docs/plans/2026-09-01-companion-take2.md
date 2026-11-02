# Plan: Companion take 2 — the virtual Reachy embodiment

Date: 2026-09-01

Status: chairman-directed (Pras, after first real-mic Live test of PR #37);
implementation owner Claude Fable; stacks on `fable/reachy-mini-converse-3d`.

## Chairman feedback (verbatim intent, logged)

Pras tested the real-microphone Live session on PR #37 (`b84f704`) and
directed, with screenshots:

1. **The Converse UI must be a virtual embodiment of the Reachy** — an
   on/off switch and a CC option, nothing else. No button forest, no
   typing. "Philosophically think of this as a simulation of the Reachy
   Mini which will power this experience in a living room for my dad."
2. **Redo the Reachy design and interactions to be more cute and more
   lifelike** — "really reach into 3D modeling here with subagents and
   learn from game dev."
3. **No tap-to-confirm for dad.** In the test session Parker said "I can
   only write the choice on the screen… if tapping is hard, you could ask
   a family member to help" — wrong product. Confirmation must be spoken
   with **visual confirmation** (the screen shows the action and the
   outcome), with guardrails set up and persisted by family on his
   computer/profile. This replaces Google Home-style assistants for a
   person with Parkinson's — no buttons in real life.

This accelerates R1 of
[`2026-09-01-always-available-reachy-companion.md`](2026-09-01-always-available-reachy-companion.md)
and supersedes the "Live-primary + Start/Done fallback" presentation of
[`2026-08-31-reachy-mini-converse-ui.md`](2026-08-31-reachy-mini-converse-ui.md)
for the person-facing surface. The semantic expression contract, live
lane, and lifecycle fixes from PR #37 remain the foundation.

## Decisions (made now, logged for review)

- **Route split.** `/parker/converse` = companion (dad's screen): Reachy
  full-viewport + power switch + CC toggle + action/error cards only.
  The current Start/Done/type/choices page moves to
  `/parker/converse/lab` as the developer/accessibility harness. The
  **live lane moves entirely to the companion** (the lab keeps only the
  push-button turns lane) — no duplicated live/Stop code between pages.
- **Power semantics v1.** Off = mic stopped, socket closed, TTS
  cancelled, playback flushed, wake nothing; state **persisted
  server-side** so a restart never silently re-enables listening. On =
  the live conversation line is open (continuous). Local wake-word
  dormancy ("Hey Parker", no cloud audio while dormant) remains the R2
  follow-up — explicitly NOT claimed here.
- **Spoken confirmation in the live lane.** propose_action still stages
  through the unchanged pipeline; the bridge then holds a
  contract-bound pending confirmation: dad's next transcript is parsed
  by the SAME deterministic `_confirmation_reply_kind` grammar the turns
  lane executes on today — spoken "yes" → contract re-verified →
  confirm + execute; "no" → cancel; anything else defers (never
  cancels, never executes). The model never decides execution. The
  session instructions stop saying "he taps it there."
- **`action: executed/failed` gets a real entry path.** The bridge emits
  an `action_result` browser frame from the actual pipeline outcome —
  the real signal the 2026-08-31 brief required before the expression
  machine may ever claim execution. The "no entry path" pin is updated
  deliberately to "entry only from the real outcome frame."
- **CC option.** Off by default; when on, TV-style captions show what
  Parker heard and what it is saying. Action cards and honest error
  lines show regardless of CC (action truth outranks the avatar).
- **Reachy v2.** Original primitive-built character (no downloaded
  assets, license posture unchanged), redesigned for cuteness and life:
  faithful-Reachy silhouette, animation-principles motion
  (squash/stretch, anticipation, secondary action), procedural idle life
  (blinks, gaze saccades, breathing), toy-like materials/lighting.
  Built as competing variants by subagents; judged visually; best one
  grafted.

## Slices

| # | Slice | State |
|---|---|---|
| 1 | Log + plan (this doc), branch `fable/reachy-companion-take2` | verified |
| 2 | Research fan-out (4 agents): Reachy reference, game-dev animation, Three.js look-dev, zero-UI patterns | verified |
| 3 | Route split + companion shell + persisted power/CC + teardown | verified (`5363b20`) |
| 4 | Spoken confirmation + action_result truth in the bridge + instructions rewrite + pins | verified (`5363b20`) |
| 5 | Reachy v2: 3 subagent variants → browser judging → graft + iterate | verified (`6deb395`) |
| 6 | Test migration: lab/companion Node specs, deck assert_staged, confirm pins | verified |
| 7 | Full verification + evidence + handoff | verified — see below |

## Evidence (2026-09-01, this branch)

- `make test`: **1155 passed**; `git diff --check` clean.
- Node: companion page spec **13/13** (power semantics, persisted restore,
  stale fences, guard TTS vs power-off, drain truth, spoken-confirm cards
  with no-tap copy pinned, CC, drop-retry, page-hide, receipts, Escape);
  lab spec 3/3; expression spec **44/44** (executed reachable ONLY via the
  real outcome frame).
- Bridge pins: spoken yes executes / no cancels / ambiguity defers /
  window expires / mutated contract fails closed / newest offer wins /
  companion mode never fires the idle chatter ladder.
- Real browser (dev server, this branch): companion boots powered-off
  ("Parker is off. Nothing is listening."), scene mounted, zero console
  errors; Reachy v2 judged and iterated over live screenshots — listening
  / talking / staged-amber / asleep (head sinks, floppy-ear antennae) all
  captured; 8/8 create/dispose cycles clean.
- Reviewer escape closed en route: the receipts route's Pydantic model
  silently dropped `expression` transitions (route-level test added).
- Known v1 semantics (logged, deliberate): power-on streams continuously
  (no local wake word yet — R2); the idle ladder is off in companion mode
  so the line stays open until power-off/error; a gesture-less restore
  shows "Turn the switch to wake Parker" instead of silently half-waking.

## Open human gates (unchanged)

Real-microphone companion session (power on → talk → lookup → staged
offer → spoken yes → executed card → barge-in → power off) and packaged
Tauri/WKWebView capture. Merge only after fresh independent review.

## Non-goals

- Wake-word/local dormancy (R2), session-lab correction UI (R3),
  physical Reachy (R4), CSP work, purchases/new action types. The
  medical/refusal boundaries and confirmation-before-action gate are
  unchanged — only the confirmation *modality* changes (tap → spoken),
  matching what the turns lane already ships.

## Verification

- Full suite + scenario deck + Node specs (lab + companion).
- Bridge confirm flow: yes/no/defer/mismatch/timeout/replaced pins.
- Real browser: companion boot, power off truly silent (no mic, no
  socket), CC captions, staged card → spoken yes → executed card.
- Real-mic Live session (Pras) and packaged WKWebView remain the human
  gates before merge.
