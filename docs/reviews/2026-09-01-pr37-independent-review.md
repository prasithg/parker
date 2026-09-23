# Independent review: PR #37 Reachy Mini Converse + PR #36 spoken selection

Date: 2026-09-01

Reviewer: Hermes — GPT-5.6 SOL, acting AI CEO and Pras's planning, strategy, and review extension

Reviewed revisions:

- PR #37 head: `9639fc87d9e981a7419bd3400db34f833c98ffca`
- PR #37 base when reviewed: `35a624dd7d0e4ced8ebd8e14bd64fb6adf5040ec`
- merged PR #36 / `origin/main`: `19d4f1cea81b4002adaccf7eb2c2652b9ea175e2`

## Verdict

**NEEDS_FIX — keep PR #37 open.**

The architectural direction is right and the implementation is a strong first prototype. The remaining problems are not broad-design failures; they are semantic truth, interruption, lifecycle, and acceptance-gate gaps. PR #36 also needs a narrow spoken-selection grammar hotfix.

## What is strong

- Clear split between the semantic expression controller and the Three.js renderer.
- Original low-poly Reachy construction from primitives; no unlicensed downloaded model.
- Three.js 0.185.1 is vendored locally, MIT-licensed, SHA-256 pinned, packaged in the PyInstaller sidecar, and served through a traversal-safe same-origin route.
- Transcript, sources, Stop, typing, confirmation, and screen-reader status remain outside the WebGL canvas.
- Reduced-motion and no-WebGL/orb fallbacks work.
- Live becomes the leading control when realtime is configured.
- Real lookup dispatch/completion/failure frames drive the search-work overlay.
- The expression vocabulary cannot claim action execution without a real entry event.
- Tests are broad: expression unit tests, scenario updates, JavaScript syntax, static assets, license/hash checks, and packaged sidecar asset serving.
- Desktop/mobile browser checks found no horizontal overflow and control/text contrast passes AA.

## Blocking findings

### 1. Guard speech bypasses semantic output state and Stop

`backend/app/parker/converse_ui.py:1039-1048` flushes realtime PCM, sets only `guard_redirect`, and speaks the redirect through `speakNow()`.

`backend/app/parker/converse_ui.py:1111-1117` creates an untracked `SpeechSynthesisUtterance`. It emits no `assistant_audio`/drained events and has no generation fence. `endLive()` does not cancel browser speech, and the Live branch of `stopParker()` returns before the later `speechSynthesis.cancel()` call.

Consequences:

- guard speech can continue after Stop/Escape or line close;
- Reachy can show interrupted/listening while Parker is audibly speaking;
- browser speech can outlive the session generation that created it.

Required fix: make every spoken output participate in one generation-fenced output lifecycle. Stop/end/page-hide must cancel both WebAudio and browser speech. Add executable browser tests for redirect → Stop and redirect → line close.

### 2. Response lifecycle is inferred from the local PCM queue

The bridge observes authoritative `response.created` and `response.done` at `backend/app/parker/realtime.py:1145-1148`, but forwards neither to the browser.

The page emits `assistant_audio_drained` whenever its locally scheduled queue temporarily empties (`converse_ui.py:917-933`). The expression controller maps that directly to listening in Live mode (`expression.js:160-163`).

Under network jitter, a gap between chunks can therefore make Reachy claim listening while the provider response is still active and more audio is coming.

Required fix: expose a minimal authoritative front-response lifecycle. Listening can resume only after the response is done **and** played audio has drained or been cancelled. Add inter-chunk underrun tests.

### 3. Worker presence can arrive in reverse order

`backend/app/parker/realtime.py:1295-1303` spawns the search worker before sending `working: started` to the browser.

A reproduction using the real method ordering produced:

```text
working: done
working: started
```

That can leave Reachy claiming search work after the work has already finished, until the TTL expires.

Required fix: publish/commit the started state before the worker can finish, and pin start-before-terminal ordering for immediate success, immediate failure, duplicate lookup, cancellation, and close.

### 4. Stop is not terminal for every stale event

`ws.onopen` at `converse_ui.py:985` lacks the `ws === live.ws` identity guard used by message/close/error handlers. A socket that completes opening after Stop can restore page state/controls to Live even when the expression controller rejects the stale `connected` event.

The expression controller also accepts `repair_offered` after `stopped`; the terminal-state test omits this real event.

Required fix: one socket/session/generation fence must apply to open, message, error, close, browser TTS callbacks, timers, and every semantic event. Expand the terminal event-vocabulary test.

### 5. Start/Done repair and confirmation poses disappear while input is still pending

`converse_ui.py:435-457` classifies every final `choices`/`yes_no` result as `repair_offered`, ignoring whether the real result is an action confirmation.

When TTS drains, `converse_ui.py:686-692` transitions to idle. `expression.js:105-117` clears guard/action overlays whenever entering idle, even though choices or confirmation remain on screen awaiting the user.

Reproduction:

```text
before drain: phase=talking, guard=repair
after drain:  phase=idle, guard=none
```

Required fix: waiting-for-choice and waiting-for-confirmation are durable semantic overlays independent of spoken playback. An authoritative confirm offer should use the staged/waiting-on-screen state. Clear only when resolved, dismissed, expired, stopped, or replaced.

### 6. Page hide retains microphone and UI resources

`converse_ui.py:1202-1207` only sends an HTTP end beacon on `pagehide`. It does not stop mic tracks, close realtime/WebAudio contexts, cancel browser TTS, clear timers, close the socket, unsubscribe the expression controller, disconnect the ResizeObserver, or call the renderer's `dispose()`.

Browser reproduction:

```text
before pagehide: capture=true, track=live
after pagehide:  capture=true, track=live
```

`reachy.js:455-471` implements a substantial `dispose()`, but production code never calls it.

Required fix: define idempotent local teardown and invoke it on Stop, terminal line failure, page hide/unload, scene replacement, and app/window closure. Pin repeated start/stop/reconnect/page-hide cycles and BFCache behavior.

### 7. Expression state is absent from session review

The approved brief requires the next human-testing record to show what Parker visibly presented. The current subscription updates status text only; client receipts and the session journal do not carry semantic phase/overlay transitions. The overnight handoff explicitly defers this.

Without it, review cannot answer whether Reachy showed listening, thinking, working, talking, guard, or staged confirmation at the right moment.

Required fix: append bounded semantic transition receipts—session/generation, monotonic timestamp, from/to phase, overlay changes, and reason. Do not log frame-by-frame animation or raw audio energy.

### 8. Real-microphone and packaged WKWebView acceptance are still open

Verified: browser fixtures, local asset serving, PyInstaller sidecar packaging, no-WebGL fallback, and reduced-motion behavior.

Not verified: real Live microphone, true barge-in, Stop-to-silence, guard redirect in the room, WebGL rendering/context lifecycle in the packaged Tauri/WKWebView, and the full session-review trail.

The approved brief made these pre-merge/release gates. They remain gates for PR #37.

## Merged PR #36: spoken-selection hotfix

The new grammar correctly supports useful forms such as `yes one`, `one please`, `the first one`, and `number two`, while preserving confirmation-before-action.

It also accepts ordinary-politeness homophones and repeated/contradictory number structures:

```text
thank you two     -> choice 2
thanks two        -> choice 2
second number one -> choice 2
second one one    -> choice 2
```

With pending choices `[reminder, family message, none]`, `thank you two` selected and captured the family-message choice. It remained confirmation-gated, but it was still a deterministic wrong capture.

Required grammar correction:

- reject `thank you <number>` / `thanks <number>` as a prefix;
- allow thanks only after an already unambiguous selection;
- allow exactly one number or ordinal expression;
- allow `first one` only as one adjacent ordinal+noun phrase;
- reject repeated or contradictory markers;
- add ASR-homophone and effortful-repetition negative tests;
- pin the pending-confirmation behavior of `yes one` separately from pending choices.

Prefer a small hotfix PR to `main`, then bring that revision into PR #37 before final integration verification.

## Non-blocking follow-ups

- The assets are local, but the Converse page still has no CSP response/meta enforcement. Keep this as a scoped acceptance follow-up rather than expanding into broad security infrastructure.
- The Reachy base/pedestal appears clipped against the scene boundary on desktop and mobile.
- Mobile has excess space between instruction and primary action while secondary controls crowd the bottom.
- The character is a promising stylized robot; Reachy identity can be strengthened through silhouette, lighting, gaze, and movement rather than more UI chrome.
- In the final companion interface, critical confirmation/error text must outrank the avatar.

## Verification evidence

- PR #37 GitHub CI: pass.
- PR #36 + PR #37 merge simulation: clean.
- Combined backend suite: **1114 passed**, 2 existing warnings.
- Expression Node suite: **34/34 passed**.
- Existing Rust suite: **14 passed** on the normal checkout; the temporary integration-worktree Rust compile was blocked only because the ignored frozen sidecar artifact was absent there.
- `git diff --check`: pass.
- Vendored Three.js hashes/license: verified.
- Same-origin assets, no-WebGL fallback, reduced-motion static rendering, desktop/mobile layout, and horizontal overflow: exercised.
- Worker finish-before-start, page-hide mic retention, repair-overlay clearing, and spoken-selection false positives: independently reproduced.

## Fix and re-review sequence

1. Hotfix PR #36 grammar on a small branch and merge it to `main`.
2. Bring current `main` into PR #37.
3. Fix the seven semantic/lifecycle/evidence blockers above.
4. Add executable browser/interleaving tests rather than only source-string assertions.
5. Run the combined full suite and scenario deck.
6. Run real-microphone and packaged Tauri/WKWebView acceptance.
7. Obtain a fresh independent exact-revision review.
8. Merge PR #37 only after the exact reviewed revision and CI are green.
