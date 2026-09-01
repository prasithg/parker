# Overnight session handoff — Reachy Mini 3D + "yes one" (2026-08-31 → 09-01)

Fable ran the overnight implementation of
[2026-08-31-reachy-mini-converse-ui.md](2026-08-31-reachy-mini-converse-ui.md)
plus the "yes one" spoken-selection finding. Two PRs — #36 (yes one) and
#37 (Reachy 3D) — both with green local suites; the real-microphone pass
is the one gate left for a human.

## The adversarial review round

A fresh-context 4-lens workflow panel (brief compliance / JS correctness /
truthfulness+safety / test integrity; 28 agents, every finding
adversarially verified) reviewed both diffs against the brief: 19
confirmed findings, all fixed the same night — the biggest: overlay TTLs
died with no WebGL renderer (the page now owns the `expr.tick()`
heartbeat), a dropped live line was presented as the user's own Stop (now
an error with a reconnect line; a post-goodbye close stays a normal end),
"you two"/"the one" over-selected in the yes-one grammar, and the
never-claims-execution test was vacuous (now proven over the machine's
introspected event vocabulary). Refuted findings: 5.

## What shipped

### PR: `fable/spoken-selection-yes-one` — "yes one" selects choice 1

- `_spoken_selection_position` in `textloop.py`: strict whole-utterance
  grammar (affirmations/fillers/tails around exactly one number/ordinal).
  "yes one", "one please", "the first one", "number two", "1st" select;
  "one two", "five", "remind me at one", "no one", counting sequences never
  do. Confirmation yes/no grammar deliberately untouched (safety surface).
- Tests: table-driven positives/negatives + the tester's exact converse-lane
  journey. Suite 1079 passed. CI green.

### PR: `fable/reachy-mini-converse-3d` — the 3D Reachy Mini presence

- **Expression state** (`backend/app/parker/static/converse/expression.js`)
  — the durable contract: real signals → `{phase, work/action/guard,
  energies}` → any renderer. 33 Node unit tests run under pytest.
- **Renderer** (`reachy.js`) — original stylized low-poly Reachy Mini from
  Three.js primitives (no downloaded model → no asset-license exposure).
  Spring-damped poses, audio-reactive motion, work-glow antenna, guard/
  staged eye colors, page-hide pause, reduced-motion static poses,
  dispose() verified across 12 create/dispose cycles.
- **Vendored Three.js 0.185.1** (module + core builds, MIT, SHA-256 pinned
  in tests and the vendor README), served same-origin at
  `/parker/converse/static/*` with a traversal guard; shipped in the
  PyInstaller sidecar via `parker.spec` datas.
- **Live-primary**: with realtime configured the Live control leads and
  Start/Done is the labeled push-button fallback; the live status label
  reads from `ParkerExpression.describe` so words and pose cannot disagree.
- **Bridge presence truth**: `look_that_up` dispatch/finish emits
  `{type:"working", kind:"search", status:"started"|"done"|"failed"}`;
  pinned in `test_realtime.py` and declared per-frame across the scenario
  deck (`browser_frame` helper — undeclared frames still fail loudly).

## Deliberate decisions / brief deviations (documented, not hidden)

1. **`working` frames are search-only.** The context worker emits no
   presence frame: it runs once at open behind the greeting, and adding a
   frame would have churned ~30 sequential-receive pins for a cue nobody
   asked for. Not-claiming is not lying; the state machine already supports
   `context` for a future event.
2. **`action: executed` has no entry path** — exactly as the brief demands.
   No browser signal proves execution; the staged pose relaxes on a 120 s
   TTL while the confirmation card stays the durable truth.
3. **Typed turns start a `turns` session from idle/stopped/error** in the
   expression machine (`user_transcript` from rest). Late live-socket
   frames cannot reach that path: the page drops frames from any socket
   that is no longer `live.ws` (identity guard, also fixed for
   onclose/onerror).
4. **Session-review journaling of expression state**: not added. The brief
   asks that the *next human-testing record* make visual state reviewable;
   the semantic transitions are client-side. Candidate next slice: batch
   phase transitions into the existing receipts channel (aggregate-only).
   Flagged as an open item, see below.
5. **`describe()` labels live only in the live lane**; the Start/Done lane
   keeps its longer, user-tested coaching lines.

## Verified (all evidence from this session)

- `make test`: **1072 passed** (slice B branch; baseline was 1059).
- Node spec: 33/33; inline page scripts `node --check` clean.
- Browser pane: every phase + overlay driven through the real controller
  (`ParkerPresence.controller` + `scene.debug()` objective readouts) —
  thinking tilt, talking voice-light, work antenna glow, guard concern
  color `#ffb38a`, staged amber `#ffd166`, stopped droop; desktop + mobile
  framing; mic-denial → honest notice + typing fallback; typed turn e2e
  (thinking→talking→idle); Escape → stopped + speech cancelled; no
  horizontal overflow; no console errors; 12× create/dispose leak check.
- The pane blocks microphone access, so live-lane audio was fixture-driven
  through the same controller/renderer path — **not** a real-mic claim.
- **Packaged path**: `make sidecar` built clean; the frozen binary
  (`parker serve --port 8033`) served all four presence assets with the
  exact vendored byte sizes, the page mounts the scene, and traversal
  stays 404 — the parker.spec datas path works frozen. (WKWebView WebGL
  rendering itself is part of the Tauri capture gate.)

## Morning checklist (Pras)

1. Merge order: either PR first; they touch disjoint files.
2. **The real-microphone pass** (brief verification item 5): `make run`,
   open `/parker/converse`, run a Live session — barge-in, a lookup
   (watch the right antenna glow while it checks), a staged proposal
   (amber glance down), guard redirect, Stop. Then the packaged
   Tauri/WKWebView capture when convenient (release gate, not merge gate).
3. "Yes one" on voice: say it at a real choice screen — the exact
   repair_abandoned journey from your tester lap should now capture.
4. If the scene misbehaves: `ParkerPresence.controller.getState()` and
   `ParkerPresence.scene.debug()` in the console show what the machine
   believes vs what is drawn.

## Open items (named follow-ups, deliberately not built tonight)

- Expression-state trail into session review (receipts or journal) so the
  next tester record shows which pose was active per turn.
- `working` presence for future `reasoning`/`action` worker kinds.
- Spoken-selection gauntlet dimension in the audio eval harness (the text
  variants are pinned; the audio deck still exercises exact digits).
- Live-lane `hearing` could also use server VAD speech_started/stopped
  (currently local mic RMS only — honest but coarser).
