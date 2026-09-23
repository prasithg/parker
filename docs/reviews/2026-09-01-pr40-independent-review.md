# Independent review: PR #40 always-available Reachy companion take 2

Date: 2026-09-01

Reviewer: Hermes — GPT-5.6 SOL, acting AI CEO and Pras's planning, strategy, and review extension

Reviewed revisions:

- Product/docs head supplied by Fable: `437dbaaecedd23770723409b3830ab3f802977e1`
- PR #40 branch after Hermes synchronized its updated base: `4a19bf8808b49e3a19a1a09e905360a44e6fa949`
- PR #40 base after CI/hotfix synchronization: `f051938f07850e4f9db9be2d0de37011bcf97792`
- Main at review: `1c40275de77f84d0705d04be7a32646a22005757`

## Verdict

**NEEDS_FIX — the product direction is validated, but the stack is not merge-ready.**

Pras's real-microphone session is meaningful positive evidence: the companion was substantially more dynamic and conversational, local wake activated the live lane, and the session journal correctly diagnosed the invisible-search and session-ending problems. The remaining blockers are authority/lifecycle and evidence defects rather than a rejection of the design.

## What is strong

- The person-facing route is now a minimal virtual Reachy with power and optional captions; the previous button/type interface moved to a lab surface.
- Powered dormancy keeps cloud realtime closed and routes microphone frames only to a local wake socket.
- Wake reuses the same held microphone stream, avoiding a second permission prompt.
- Spoken confirmation remains deterministic and contract-bound; the model cannot execute actions by itself.
- Action-result UI has a real server outcome path.
- Fable used the new delivery workflow, kept both stacked PRs open, recorded exact evidence/deviations, and did not claim the human gates passed.
- Pras's journal from call 41 proves the review/evidence lane is now product-useful: lookup dispatches, injections, final exchanges, and visible state could be inspected after the session.
- Reachy v2 is visually improved and the next motion pass can now use official Pollen/Hugging Face reference material rather than another text-only redesign.

## Blockers before the next feature slice

### 1. The suite is still false-green under concurrent session creation

Independent full-suite run at this branch failed `test_a_mutated_action_fails_closed_on_spoken_yes` after the shared SQLite connection was corrupted by the concurrent session-create test. A focused run then reported three unhandled thread exceptions while pytest still exited successfully:

```text
sqlite3.DatabaseError: no more rows available
sqlalchemy.exc.InvalidRequestError: Could not refresh CallLog
```

Strict repetition of `test_concurrent_session_creates_load_the_model_exactly_once`:

```text
15 runs: 11 pass, 4 fail when thread warnings are errors
```

This is an existing root cause, not introduced by the companion, but it invalidates `1179 passed` as a clean verification claim and will make the newly enabled stacked-PR CI nondeterministic.

Required fix:

- make every spawned thread's result/exception part of the assertion;
- serialize or otherwise correctly isolate the CallLog write performed by `ConverseStore.create_session`;
- make `PytestUnhandledThreadExceptionWarning` fail CI;
- rerun the strict test repeatedly and then the full suite.

### 2. The power switch is page-local, not system-authoritative

The product contract says power off means no listening, wake detection, cloud stream, processing, or response. Current code does not enforce that at the server boundary:

- `/parker/converse/wake` does not check persisted `power_on`;
- `/parker/converse/realtime` does not check persisted `power_on`;
- setting `power_on=false` does not close other tabs' wake or realtime sockets;
- `MAX_LIVE_BRIDGES=2` permits two simultaneous lines;
- the settings POST is fire-and-forget and every persistence failure is swallowed.

A second/stale tab can therefore continue listening or talking after another tab turns Parker off. If the local settings write fails, the visible switch turns off while the database can remain on and reappear on restart.

Required fix:

- make power transitions acknowledged, with failure visible and retried/fail-closed;
- enforce power state and one companion owner server-side;
- close/revoke active wake/realtime sessions on power-off;
- pin two-tab/stale-tab, restart, failed-write, in-flight-permission, and reconnect interleavings.

The lab may remain separately available to family/developers, but companion power must own every companion audio session.

### 3. Wake matching is too permissive for a TV room

The current fuzzy matcher treats any token within edit distance one of `parker` as valid and includes `a` as a greeting. Independent deterministic probes produced wakes for:

```text
hey darker
hey marker
hey barker
hey packer
hey parked
a parker
```

This is especially risky in the intended recliner/TV environment. The energy gate prevents inference in silence, but during ambient TV speech the detector runs a roughly 350 ms faster-whisper inference every 700 ms of new energetic audio—potentially high sustained CPU and a large false-wake surface.

Required fix/evidence:

- remove broad tokens such as `a` from greeting acceptance;
- keep generous Parker-like acceptance for Parkinsonian speech (chairman override, 2026-09-01 evening: optimize for Dad's wake recall — see the session-3 plan's "Chairman decisions"); do not shrink to a purist confusion set;
- add the false-wake phrases above and TV-like transcripts as negative fixtures;
- measure inference count, CPU/latency, wake recall, and false wakes during a real ambient-TV soak;
- **fail closed when local ASR is unavailable**: the current `unavailable -> startActive('fallback')` path silently opens continuous cloud audio and violates dormant privacy; require an explicit user decision instead;
- preserve the rest of a same-breath request: `Hey Parker, can you help me?` currently wakes but drops `can you help me` while the realtime socket connects; forward a bounded transcript tail or buffered audio under a pinned handoff contract;
- retain the real-room/evening human gate before merge.

### 4. Live reconnect retries are unbounded

`lineDropped()` schedules `startActive()` after every failure without a per-power-generation retry cap. A persistent provider/network failure can reconnect forever while the UI claims a retry is underway. The take-2 brief promised one quiet retry.

Required fix: one bounded reconnect attempt per drop/power generation, then a stable honest error/dormant state until user wake or power interaction.

### 5. The packaged app does not yet enter the companion

The Tauri first-session/tray path still opens `/parker/screen` and starts the separate TALK sidecar. There is no normal packaged route into `/parker/converse`, so the named packaged WKWebView gate cannot exercise the companion through the actual app flow.

Required fix: make the companion the intended packaged person-facing window while preserving the lab/review surfaces for family/development. Verify Tauri microphone permission, power persistence, wake/local audio ownership, WebGL lifecycle, close/hide teardown, and restart behavior from that real entry point.

### 6. Dynamic cards and search truth need accessibility/contract cleanup

- The companion receives exact staged-action and action-result text in `#card`, but the dynamic card is not an atomic live status/alert. VoiceOver can miss the confirmation readback or outcome.
- The realtime prompt says sources appear on screen while the companion discards `sources` frames when CC is off. Either change the prompt/contract or provide a truthful cue. Preserve the zero-chrome direction: use an unmistakable Reachy/spoken work cue with CC off and bounded source labels when CC is on.

Required fix: pin staged/executed/cancelled/error announcements with accessible live semantics and make the search source promise match both CC modes.

### 7. The stack still needs clean merge choreography

Hermes has now closed the CI infrastructure gap and synchronized the stack:

- PR #41, stacked-PR CI: merged to main;
- PR #39, spoken-selection hotfix: independently reviewed and merged to main;
- updated main merged into PR #37 and then PR #40;
- PR #40 now has a real CI run.

PR #37's expression-receipt/schema fix originally landed only in PR #40, and expression journaling still awaits SQLite writes on the microphone WebSocket pump. Before merge, either port the remaining review-trail fix cleanly into PR #37 or explicitly collapse/retarget the stack after PR #37 is independently accepted. Evidence logging must not block audio forwarding under SQLite lock/retry.

## Review of Pras's session-3 findings

### Session ending

The diagnosis is correct: `OK, thanks` left the line in listening because only the long idle ladder ends sessions.

Do not treat bare `thanks`, `OK thanks`, or `stop` as unconditional hard end phrases. They can occur mid-conversation or mean stop speaking. Recommended v1:

- hard enders: explicit `goodbye Parker`, `that's all`, `that's it for now`, `I'm done`, `go back to sleep`, `stop listening`;
- soft closer: a bounded server/model proposal when gratitude appears after a substantive answer, no confirmation/action or worker is pending, and the assistant has not just asked a question;
- Parker speaks one short goodbye, then returns dormant;
- any user speech during the goodbye cancels dormancy and resumes listening;
- pending action/worker state must be resolved, cancelled, or durably deferred before dormancy;
- keep the Parkinson-friendly idle window conservative until real data supports shortening it.

### Dormant versus engaged

The proposed label and scene-level dimming are correct. Dormancy should be unmistakably asleep and must not react to ambient speech. The power switch label should say `Resting — say “Hey Parker”`; active listening should use a visibly awake pose and accessible status. Do not solve this with more visible controls.

### Search visibility and date grounding

- Keep the zero-chrome contract when CC is off: use a stronger Reachy antenna/head work cue and the existing spoken acknowledgment, not a permanent text chip.
- When CC is on, show `checked the web` plus bounded source labels.
- Ground the search worker with the same local date/time context as the front agent.
- A local `my reminders`/`my day` worker is valuable but is a separate worker slice. Name its limits honestly: Parker has local reminders, not a general calendar, until a real calendar source exists.

### Voice

Do not change the default from prose alone. Give Pras a short live audition of the supported male candidates and record the selected voice as a family-admin setting. Voice choice is a human gate, not a code-review decision.

### Reachy expressiveness

Hermes gathered official motion references in [`docs/references/2026-09-01-reachy-mini-motion-reference.md`](../references/2026-09-01-reachy-mini-motion-reference.md). Use that packet after correctness/power/wake/session-end gates, not before.

## Corrected next-session order

Keep the next session to **foundation closure only**:

1. Confirm the new stacked-PR CI run and fix the strict concurrent-session false-green; make thread failures fail CI.
2. Make companion power server-authoritative, single-owner, persistence-acknowledged, and fail-closed; bound reconnects.
3. Tighten wake matching, fail closed when local ASR is unavailable, preserve same-breath requests, and add ambient-TV CPU/false-wake evidence.
4. Make the packaged Tauri person-facing entry open the companion and verify the real power/wake/WebGL lifecycle.
5. Fix action/error-card live semantics and align the search/source promise with CC-off and CC-on behavior.
6. Stop for another real-mic, multi-tab, evening false-wake, and packaged-WKWebView gate; return for fresh Hermes review.

Only after those pass, use a separate product session for:

7. Safe explicit/soft session ending and conservative return to dormancy.
8. Dormant-versus-engaged legibility and search date grounding.
9. A separately designed local-reminders worker.
10. Voice audition and, after human selection, a configured default.
11. A reference-backed Reachy expressiveness pass.

## Verification checked

- Branch/PR graph and exact revisions.
- `git diff --check` over PR #40.
- Local focused companion/wake/realtime/scenario suite: 155 passed, but emitted three unhandled thread exceptions from the known concurrency defect.
- Local full suite: failed once after the concurrency defect contaminated a later realtime confirmation test.
- Strict concurrent-create repetition: 11/15 passed, 4/15 failed.
- Local wake real-audio probe: PASS on three provided positives and three negatives; insufficient for ambient-TV release evidence.
- Rust tests: 14 passed.
- PR #41 CI: passed and merged.
- PR #39 CI/review: passed and merged.
- PR #40 CI: enabled and running at review time.

## What remains unverified

- Multi-tab/global power-off behavior, persistence failure, and server revocation.
- Ambient-TV wake false-positive and CPU behavior.
- Session-ending behavior under mid-conversation gratitude, pending worker/action, and barge-in during goodbye.
- Real companion spoken-confirmation cycle in Pras's reported session.
- Packaged Tauri/WKWebView power/wake/WebGL lifecycle.
- Final stack merge shape and fresh exact-revision review after the blockers above.
