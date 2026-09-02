# Plan: Reachy motion vocabulary — beats, not busyness

Date: 2026-09-02 (overnight; backlog item 10 — Pras: "still not as
interactive or expressive as the real-life Reachy"; source packet:
[Reachy Mini motion and expression reference](../references/2026-09-01-reachy-mini-motion-reference.md)).

## What changes (renderer + one expression event; no policy, no page chrome)

The reference's principles, applied to `static/converse/reachy.js`:

1. **A beat layer.** Short timed offsets ("beats") layered ABOVE the pose
   springs and below the antenna physics — one owner per degree of
   freedom stays true (the spring owns the target, the beat adds a bounded,
   self-decaying offset, the physics lags). Beats never snap: a phase
   change into an asleep state clears them, and a replacement beat blends
   through the springs. Reduced motion disables beats entirely.
2. **The staged wake beat** (dormant/offline → connecting): anticipation
   (a 120 ms compression — head sinks a touch, antennae dip), then the
   head rises on its spring, then the antennae perk ~150 ms later with one
   overshoot, then settle. Repeated wakes replace, never stack.
3. **Acknowledgment** (hearing → listening: he stopped talking): one small
   nod and an antenna dip — "I heard you", not a celebration.
4. **Phrase beats while talking.** The page marks sentence boundaries in
   Parker's REAL transcript deltas (`.`, `?`, `!`) with a
   `phrase_boundary` expression event; the controller counts them only
   while talking; the renderer turns each into a micro-nod and a tiny
   alternating antenna punctuation. No beat comes from raw waveform
   amplitude — the voice envelope still only drives the speaker glow and
   the existing low-amplitude wobble.
5. **Outcome beats from the real action frames only**: executed keeps its
   antenna hop and gains a small head-up bounce; failed/expired/cancelled
   get one restrained beat each (a slow chin-down for failed, a settle for
   cancelled) — never theatrical sadness.
6. **Idle life while listening**: a slow weight shift (body yaw drift over
   ~8 s) so waiting reads as alive; thinking stays a held pose (stillness
   is expression).
7. **A deterministic test hook**: `scene.advance(ms)` steps the frame loop
   by a virtual interval and renders, so beats can be verified as
   readouts over time (`debug().beatOffset`), not eyeballed.

## Non-goals

Face/sound-source tracking (no signal exists), importing Pollen
trajectories (license unverified), physical motor control, changes to the
semantic phases or their entry paths, any new visible control.

## Verification

Expression spec: `phrase_boundary` counts only while talking and never
changes phase. Companion Node spec: a transcript delta ending a sentence
emits exactly one `phrase_boundary`. Renderer, in a real browser via
`advance()`: wake beat readouts show compress → rise → antenna overshoot →
settle within ~900 ms; a second wake within the beat replaces it (no
stack); a phrase beat produces one nod that decays; asleep states carry no
beat offset; `reducedMotion` renders no beats. Reduced-motion and no-WebGL
paths unchanged. Human gate: clips judged side by side with the official
reference material and, when it arrives, Pras's physical Reachy Mini.

## Evidence (2026-09-02, real browser, scene `debug()` readouts via `advance()`)

- **Wake beat** (dormant → connecting): head compresses at 60 ms (drop
  +0.029, chin +0.015), rises past neutral by 200 ms (drop −0.036), antennae
  dip at 300 ms (−0.147) then overshoot to perked at 450–600 ms (+0.11),
  everything settled and the beat gone by 1 000 ms. A second wake mid-beat
  replaces it (one `wake` entry, never two).
- **Acknowledgment** (hearing → listening on real user energy, fake-clock
  controller): nod +0.06 at 120 ms with an antenna dip, gone by 600 ms.
- **Phrase beats**: one `phrase_boundary` while talking → nod +0.035 at
  100 ms with a single-antenna tick, decayed by 200 ms; the event is ignored
  while listening (`beats` unchanged) and never changes the phase.
- **Idle life**: body yaw drifts +0.029 → −0.028 over five seconds while
  listening (≈8 s period), no fidget.
- **Asleep states carry no beat offset**; **reduced motion**: `advance()` is
  a no-op, no beats, lights at full when awake. Zero console errors.
- Specs: expression spec pins the event's gating; the companion Node spec
  pins that a sentence-ending transcript delta is exactly one beat. Full
  suite on the branch green.

## Fix round (2026-09-02, after the fresh review of `8f52bb1`)

Four contract gaps closed: a phrase beat no longer produces an expression
receipt (the subscriber skips `phrase_boundary`, pinned in the companion
Node spec); the reduced-motion path no longer re-renders per sentence;
`advance()` stops the live loop first and `dt` is floored at 0, so a real
frame after a verification step can never see a negative interval; the
cancelled beat now fires on the real lapse (`staged → none` — his "no" or
the expiry), verified in the browser. Side finding fixed: the head-drop
spring was never stepped in the live loop, so the animated scene never
actually sank the head when dormant (only the reduced-motion static
render did) — now `headDrop` reaches 0.99 dormant and 0.05 awake in the
live loop (browser readouts via `advance()`).
