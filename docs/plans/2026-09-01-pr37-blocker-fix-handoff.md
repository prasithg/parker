# Handoff: PR #37 blocker-fix session (builder: Claude Fable)

Date: 2026-09-01

Task: close the verified PR #36/#37 correctness blockers from
[`docs/reviews/2026-09-01-pr37-independent-review.md`](../reviews/2026-09-01-pr37-independent-review.md)
and advance the real acceptance gates the builder can execute. Non-goals
honored: no wake-word/companion work, no session-lab work, no gate
reclassification.

## Task ledger

| # | Review blocker | State | Where |
|---|---|---|---|
| PR #36 | Gratitude homophones / contradictory numbers select | `verified` | PR #39 (`fable/spoken-selection-grammar-hotfix`, merged into this branch) |
| 1 | Guard TTS bypasses output state and Stop | `verified` | `converse_ui.py` speakNow/endLive; page spec: redirect→Stop, redirect→close |
| 2 | Listening inferred from local PCM queue | `verified` | `realtime.py` response_state frame; page drain gate; underrun specs both orders |
| 3 | `working: done` can overtake `started` | `verified` | `realtime.py` dispatch order; instant success/failure/close pins |
| 4 | Stop not terminal for every stale event | `verified` | ws.onopen fence; full-vocabulary terminal spec |
| 5 | Repair/confirmation poses die at drain | `verified` | expression `attention` axis; real-browser repro now holds through idle |
| 6 | Page hide retains mic/UI resources | `verified` | `releasePage()`; page spec pins teardown + BFCache reload |
| 7 | Expression state absent from session review | `verified` | ws `expression` frames → journal; receipts beacon; sessions UI card |
| 8 | Real-mic + packaged WKWebView acceptance | `open — human/device gate` | unchanged, deliberately |

## Evidence (all on this branch)

- `make test`: **1142 passed** (main baseline 1114; hotfix branch 1120).
- `git diff --check`: clean.
- Node expression spec: **41/41**.
- **New executable page suite** `backend/tests/js/converse_page.spec.js`:
  runs the REAL inline page script under Node with stubbed
  DOM/WebAudio/WebSocket/speechSynthesis and virtual timers. **10/10 on
  this branch; 7/10 FAIL on the pre-fix page (`ee89ea7`)** — the failures
  are exactly the review's reproductions (stale open, guard TTS ×2, drain
  jitter, guard-drain hold, page-hide, receipts).
- Scenario deck + session review: 91 passed; browser-frame vocabulary
  unchanged for audioless responses (only the two audio-bearing fixtures
  see the new `response_state` frame).
- Real browser (dev server, this branch): page boots with no console
  errors; typed garbled turn → 3 choices; after TTS drained to idle the
  choices stay on screen with `attention: 'choice'` (the review's exact
  repro, now truthful); live-lane label for a staged proposal reads
  "Waiting for you to confirm on the screen. Nothing has happened yet.";
  staged eye color in `scene.debug()`; Stop/Escape clears everything.
- Packaged: `make sidecar` rebuilt; `scripts/sidecar_smoke.sh` **PASS**;
  frozen binary serves all four presence assets **byte-exact** with
  traversal 404 (re-verified after the expression.js change).

## Known minor follow-ups (not blockers, not fixed here)

- The turns lane's streaming "thinking flap" fires the `user_transcript`
  presence event without new user words, which relaxes the repair *face*
  early (the waiting overlay and label stay truthful). Cosmetic;
  candidate for a later cleanup of `TURNS_PRESENCE`.
- Review's non-blocking notes (CSP, base clipping, mobile spacing,
  identity polish) remain untouched per scope.

## Deliberate deviations and approving authority

- Review fix-sequence step 1 says "hotfix PR #36 grammar … and merge it
  to main" before integration. The builder has no merge authority
  (T2 + explicit "do not merge" in the task), so PR #39 is open and
  unmerged, and its branch is merged into this PR branch for integration
  testing instead. Git dedupes once PR #39 lands on main. Needs Hermes
  acknowledgement rather than approval of substance.
- No other gate was changed. Real-microphone Live acceptance and packaged
  Tauri/WKWebView acceptance remain open, reserved for Pras.

## Untested scope

- Real microphone, real barge-in, Stop-to-silence in the room, guard
  redirect audibility, WKWebView WebGL lifecycle — the named human/device
  gates.
- The Node page harness stubs browser surfaces; fidelity was
  cross-checked against the real page in the browser pane for the
  waiting-overlay, label, and Stop flows, but speechSynthesis/BFCache
  behavior on real WebKit is only covered by the harness.
- Live-provider behavior: no upstream OpenAI payload changed (browser
  frames and dispatch ordering only), so no live probe was spent.

## Completion footer

```markdown
Delivery state: verified (awaiting independent review)
Blast tier: T2
Exact revision: 1698aed (code) on fable/reachy-mini-converse-3d; grammar hotfix 0b9f5d5 on PR #39
Intent/acceptance source: docs/reviews/2026-09-01-pr37-independent-review.md + docs/plans/2026-08-31-reachy-mini-converse-ui.md
Evidence checked: make test 1142 passed; node page spec 10/10 (7/10 fail pre-fix); expression spec 41/41; sidecar smoke PASS + byte-exact serve; real-browser flow readouts
Independent review: required next — fresh exact-revision Hermes review of PR #37 head and PR #39
Human/device gates: real-mic Live session; packaged Tauri/WKWebView capture (both open, reserved for Pras)
What remains untested: see Untested scope above
Deliberate deviations and approving authority: PR #39 unmerged before integration (merge authority withheld from builder; needs Hermes ack)
Next owner/action: Hermes — independent review of PR #39 and PR #37; then Pras — human/device gates before merge
```
