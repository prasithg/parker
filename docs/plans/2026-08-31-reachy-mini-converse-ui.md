# Plan: 3D Reachy Mini Converse experience

Date: 2026-08-31

Status: chairman-approved next UI/UX direction; implementation not yet started

Owner for the next build session: Claude Fable

## Goal

Make Parker's primary Converse experience feel like a living, responsive Reachy Mini rather than a browser form or abstract orb.

The screen should render a stylized **3D Reachy Mini** whose movement and expression are driven by Parker's real voice-agent state: listening, hearing the user, thinking locally, waiting on background work, speaking, being interrupted, waiting for confirmation, succeeding, failing, and ending the conversation.

This is an embodiment of the architecture in [`docs/voice-agent-architecture.md`](../voice-agent-architecture.md): one consistent Parker face and voice, with invisible workers behind it.

## Product decision

The earlier 2D-vector/no-WebGL constraint is superseded. A 3D renderer is now the intended primary visual experience.

The renderer must remain downstream of a runtime-neutral expression state machine:

```text
real voice/runtime signals
  -> small semantic expression state
  -> 3D Reachy Mini renderer
  -> accessible text/status + reduced-motion/static fallback
```

The expression state—not Three.js, a particular model asset, or a physical robot API—is the durable product contract. The same state can later drive an actual Reachy Mini without rewriting the voice orchestrator.

## Experience principles

1. **Live is the experience.** When OpenAI Realtime is configured, the primary action should begin Live conversation. Start/Done remains a clearly labeled fallback.
2. **One face, one voice.** Background workers never become visible personas. Reachy remains Parker while search, context, reasoning, or action staging happens behind it.
3. **Motion tells the truth.** Listening, thinking, talking, and waiting states must come from real runtime signals—not a timer pretending work exists.
4. **Calm, not busy.** Subtle gaze, antenna, tilt, breathing, and audio-reactive motion should communicate attention without creating a distracting toy.
5. **Voice first.** The 3D scene supports the conversation; it does not hide transcripts, sources, Stop, confirmation, or errors.
6. **Interruptible.** Barge-in and Stop must make Parker visibly and audibly yield immediately.
7. **Accessible fallback.** Reduced motion, WebGL failure, low-power hardware, keyboard use, and assistive technology must retain a complete experience.

## State model

Avoid one giant enumeration with every combination. Use a primary conversation phase plus small orthogonal overlays.

### Primary phase

| Phase | Real source of truth | Reachy behavior | Exit condition |
| --- | --- | --- | --- |
| `offline` | engine/realtime unavailable | asleep/dim, clear unavailable text | service becomes available |
| `idle` | page ready, no active turn | gentle breathing, occasional neutral gaze/antenna motion | session start |
| `connecting` | mic or realtime socket opening | orient toward user, restrained anticipation | connected or error |
| `listening` | live mic active; no output playing | attentive forward gaze, open posture, mic-level breathing | VAD/transcript/Stop |
| `hearing` | user speech energy/VAD active | responsive head/gaze tracking from audio energy, no lip-sync | speech stops |
| `thinking` | transcript accepted; front response pending | brief deliberate glance/tilt; not a generic spinner | audio/reply/error/worker wait |
| `talking` | output audio is actually scheduled/playing | audio-energy-driven head/antenna/lip-light motion | played audio drains/interruption |
| `interrupted` | clear/barge-in/Stop flushes output | yield/reorient immediately; no continued talking animation | listening, stopped, or idle |
| `closing` | server `closing` plus remaining playback | calm goodbye gesture, then rest | audio drains and line closes |
| `stopped` | terminal user Stop/end | visibly quiet, no implied listening or work | new session |
| `error` | mic/socket/provider/runtime error | calm concern state with plain recovery text | retry/recovery |

### Work overlay

| Overlay | Source | Meaning |
| --- | --- | --- |
| `none` | no tracked job | Parker has no known slow work outstanding |
| `context` | context worker in flight | Parker is quietly loading relevant context |
| `search` | `look_that_up` dispatched and not terminal | Parker is checking current information while remaining conversational |
| `reasoning` | future bounded reasoning/project job | deeper work is happening behind the live conversation |
| `action` | proposal is being validated/staged | Parker is preparing a reviewable action, not executing it |

A work overlay can coexist with listening or talking. It should be a subtle secondary cue—such as an antenna pattern or small orbiting status marker—not a second character.

### Action overlay

| Overlay | Source | UI truth |
| --- | --- | --- |
| `none` | no current proposal | no action claim |
| `staged` | `proposal_staged` | waiting on the screen for confirmation; nothing happened yet |
| `executed` | ordinary confirmation pipeline reports success | completed exactly as shown |
| `failed` | action pipeline terminal failure | failed; do not imply success or silently retry |

### Guard/repair overlay

- `guard_redirect`: apologetic/concerned expression while the standard redirect is heard and shown.
- `repair`: attentive uncertainty rather than failure or blame.
- `selection`: choices remain large, voice-readable, and compatible with natural phrases such as “yes one,” “one please,” and ordinals.

## Real signal mapping

Reuse current signals before inventing new protocol:

- local microphone amplitude → subtle `hearing` energy;
- Live WebSocket open → `listening`;
- `user_transcript` → transcript visible and `thinking` until the next real event;
- `audio` chunks plus `AudioContext` scheduling → `talking` and audio-reactive motion;
- `assistant_transcript_delta` → visible spoken-text mirror;
- `clear` → immediate `interrupted` and audio/motion flush;
- `sources` → evidence chips, not a new persona;
- `proposal_staged` → `action: staged` and waiting-on-screen pose;
- `guard_redirect` → guard overlay plus audible redirect;
- `closing` → goodbye/drain state;
- recoverable `notice` → plain-language non-terminal notice while preserving the real conversation phase; it must not trigger a terminal error pose;
- `unavailable`, microphone denial, socket close/drop, or unrecoverable runtime failure → `offline`/`error` as appropriate;
- client Stop → terminal `stopped`.

Some truthful states are not currently available to the browser—especially worker dispatch/completion and provider response-active state. First derive what is honest from existing events. If the scene would otherwise lie, add the smallest explicit presence event to the browser protocol, pin it in the realtime/scenario tests, and journal it for session review. Do not add a parallel UI-only fake state machine.

The current live WebSocket also has no browser event proving that a staged action was later confirmed and executed. `action: executed` must remain unavailable unless Fable adds a real pipeline/screen signal or consumes an existing authoritative state endpoint and pins that behavior. Never infer execution from `proposal_staged`, elapsed time, animation completion, or optimistic UI.

## 3D Reachy Mini renderer

### Visual target

- recognizable Reachy Mini silhouette and proportions;
- expressive head orientation, gaze, and antennas;
- restrained idle micro-behavior;
- clear listening versus talking versus waiting poses;
- audio-reactive movement from actual input/output energy;
- cinematic enough to feel alive, simple enough to remain legible from across a room;
- warm Parker visual identity rather than a generic developer-dashboard scene.

### Asset and technology decisions

Fable should inspect the existing single-file HTML/JavaScript surface and choose the minimum sustainable 3D path. A WebGL library such as Three.js is acceptable if justified, pinned, bundled locally, CSP-compatible, and license-compliant. Do not load runtime code, fonts, textures, or models from a CDN.

Before using an official or third-party Reachy model/texture:

- verify redistribution and attribution rights;
- record the source and license in the repository;
- avoid copying an asset whose terms do not permit the public MIT project to ship it.

If a redistributable official model is unavailable, create an original stylized low-poly interpretation rather than blocking the experience or shipping an unclear asset.

### Performance and degradation

- renderer initialization must not delay microphone availability or the first spoken response;
- target smooth animation on the actual Mac/browser/Tauri path; measure rather than claiming a fixed frame rate from headless tests;
- pause/reduce rendering when hidden;
- no unbounded animation loop, object, source, or WebGL resource leaks across repeated sessions;
- `prefers-reduced-motion` uses restrained transitions and no continuous decorative motion;
- WebGL unavailable/failed → the existing orb or a static Reachy image plus the complete text/status experience;
- narrow layouts preserve Stop, transcript, choices, confirmations, and error recovery without horizontal overflow.

## Information hierarchy

The primary surface should show, in order:

1. 3D Reachy Mini and one plain-language state label;
2. immediate Stop and the primary Live control;
3. what Parker heard;
4. what Parker is saying/said;
5. sources or a confirmation/action card when relevant;
6. fallback/type/family details as secondary controls.

Do not expose worker names, model names, queues, tokens, or orchestration internals to the person-facing UI.

## Accessibility

- Stop remains keyboard-native, at least 44px, visible during every active state, and bound to Escape.
- Status text uses `aria-live` without announcing every animation frame or transcript token.
- All essential meaning exists in text in addition to color/motion/3D pose.
- Visible focus, logical tab order, sufficient contrast, and no focus loss when controls change.
- Reduced motion and WebGL fallback are testable states, not comments.
- The 3D canvas is non-essential presentation; it cannot trap focus or hide controls from screen readers.

## Acceptance criteria

### Product

- With realtime configured, a new user meets Live conversation as the primary path.
- A real microphone session visibly distinguishes idle, listening/hearing, thinking or waiting, talking, interruption, staged confirmation, and close/error.
- Parker's visible state never claims work, speech, listening, or execution that is not actually occurring.
- Barge-in and Stop cancel talking motion at the same time unplayed audio is flushed.
- “Yes one” and related spoken-selection behavior is implemented or explicitly separated into a linked follow-up; the UI must not present a path the voice layer still rejects without saying so.

### Engineering

- expression state is decoupled from the 3D renderer and can be unit-tested without WebGL;
- existing realtime, scenario, review-journal, confirmation, and Stop contracts remain green;
- new browser protocol events, if any, are minimal, documented, scenario-tested, and journaled;
- renderer lifecycle survives repeated start/stop/reconnect/page-hide cycles;
- inline JavaScript/module syntax and CSP/package behavior pass;
- no external runtime dependency fetch;
- desktop and true narrow-layout captures show no clipping or control regression;
- no console errors or horizontal overflow;
- reduced-motion and no-WebGL fallbacks are verified;
- packaged Tauri/WebKit real-microphone pass remains a release gate.

### Evidence

The next human-testing record must make the visual state reviewable: which expression phase/overlays were active when the person spoke, waited, interrupted, received a worker result, or stopped. It does not need animation-frame logging; semantic state transitions are enough.

## Verification plan

1. Focused unit tests for expression-state transitions and stale-event rejection.
2. Existing Converse/realtime/scenario/session-review tests.
3. JavaScript syntax/CSP and Rust/Tauri compile tests.
4. Desktop and narrow browser captures; reduced-motion and WebGL-failure captures.
5. Real local microphone session with barge-in, worker lookup, staged proposal, guard redirect, and Stop.
6. Fresh-context Fable review against this brief and the exact final diff.
7. PR, green CI, and merge—no direct-to-main implementation.

## Non-goals for this slice

- controlling a physical Reachy Mini;
- camera input, face recognition, gaze tracking, or room surveillance;
- rebuilding the backend or voice orchestrator;
- Pipecat/PhoneLLM migration;
- a general avatar engine or theme marketplace;
- 3D customization/settings beyond what the primary experience needs;
- hiding transcripts, sources, confirmations, or Stop to make the scene look cleaner;
- pretending background work or an external action completed.

## Recommended implementation order

1. Spike WebGL, local-model loading, and asset bundling in the actual packaged Tauri/WKWebView path before committing to a renderer/library; retain the static/orb fallback throughout.
2. Extract and pin the semantic expression-state controller around current events.
3. Make Live primary and preserve the labeled Start/Done fallback.
4. Build the 3D Reachy renderer against synthetic state fixtures.
5. Connect actual input/output audio energy and current WebSocket events.
6. Add the smallest missing truthful presence/action event only where derivation is impossible.
7. Complete accessibility/degraded renderers and responsive layout.
8. Verify the real microphone path and session-review state trail.
9. Fable review, PR, CI, merge, then another human session.
