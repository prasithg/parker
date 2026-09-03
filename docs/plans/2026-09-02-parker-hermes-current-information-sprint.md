# Sprint: land the companion stack, then prove Parker + Hermes

Date: 2026-09-02
Status: Phase 0 implementation complete; automated release closure is tracked below and human/device gates remain

## Goal

Land the companion foundation and Fable follow-ons without losing safety semantics, then prove a two-speed Parker in household use:

- direct low-latency retrieval for volatile facts;
- local Hermes as a cancellable read-only background researcher for slower/multi-step work;
- Parker as the only conversational voice, confirmation authority, action boundary, and durable source of user state.

The first new feature outcome is **Curiosity Continuity**: ask once, get a current answer, optionally remember the interest, and return a fresh update later without requiring typing or repetition.

## Done when

1. The blockers in the independent reviews of PRs #40, #43, and #45 are fixed with red-capable tests; #46's unique motion diff remains intact.
2. A fresh integration tip from live `main` contains the semantic union of #37/#40/#43/#45/#46, passes the full local gates, receives green exact-SHA CI, and receives an independent final-tree PASS. Power/wake/session-end changes also pass Pras's real-mic/package/room gates before merge.
3. An isolated local Hermes profile can complete and cancel a read-only research run through Parker's existing worker lifecycle. Power off stops the Hermes run and every other cloud/provider operation before persistence drains.
4. Parallel Fast, Parallel Turbo, Exa Instant, and Tavily Ultra-fast are compared through the same uncached Parker query corpus. The winner is selected from measured latency, freshness, source quality, factual exactness, contradiction behavior, abstention, and cost—not vendor claims.
5. Volatile questions cannot be answered from the realtime model's training memory while current retrieval is pending or unavailable.
6. The Curiosity Continuity vertical preserves an explicitly confirmed interest and can offer a source-backed update at a later interaction; rejection, stop, power off, and stale-result suppression are verified.
7. A Pras household session records actual latency, successful follow-ups, corrections, false closes, false wakes, and preference versus the stock assistant. Dad testing remains opt-in.

## Context and accepted decisions

- Independent review artifacts:
  - `docs/reviews/2026-09-02-pr40-independent-review.md`
  - `docs/reviews/2026-09-02-pr43-independent-review.md`
  - `docs/reviews/2026-09-02-pr45-independent-review.md`
  - `docs/reviews/2026-09-02-pr46-independent-review.md`
- Existing search spike: `docs/plans/2026-09-01-fast-current-web-search-spike.md` on PR #40.
- The live stack is divergent: PR #40 does not contain the live PR #37 tip, and live `main` has advanced. Resolve through an explicit integration branch; never use blanket `ours`/`theirs` conflict selection.
- Parker's power switch governs the whole companion. Powered off means no wake detection, audio handling, streaming, provider work, scheduled research, or response.
- Parkinson-friendly wake recall remains the priority. Do not narrow Parker-like pronunciations to solve unrelated ambient false wakes.
- Realtime audio stays on Parker's existing lane. Hermes receives bounded text and source context, never raw audio.

## Non-goals

- No generic `ask_hermes` tool exposed to the realtime model.
- No default-profile Hermes access, Pras work memory, terminal, files, browser control, messaging, cron, Home Assistant, or external actions in the first integration.
- No new admin/compliance dashboard or broad permission system.
- No calendar claim until a real calendar source exists.
- No medication dose output or medical advice.
- No proactive speech from dormancy in this sprint; a due continuity item is offered at the next interaction.
- No physical Reachy motor control.

## Phase 0 — close and reconcile Fable's stack

### P0.1 — strict power-off and safe wake-tail transport

Depends on: none

Objective: repair PR #40 so power off closes/cancels every provider before slow persistence and same-breath speech remains untrusted, complete user content.

Likely files:

- `backend/app/parker/realtime.py`
- `backend/app/parker/realtime_workers.py`
- `backend/app/parker/companion_ui.py`
- `backend/tests/test_realtime.py`
- `backend/tests/js/companion_page.spec.js`
- `backend/tests/test_scenarios_concurrency.py`

Acceptance:

- upstream realtime close begins before session finalization/journaling;
- already-running lookup/research operations expose a real cancel/stop path rather than merely suppressing delivery;
- power-off HTTP completion is not held behind database persistence;
- no provider result can arrive or continue processing after off;
- wake-tail transcription is sent as user/untrusted content, never interpolated into a system-role message;
- rolling/sliding tail updates cannot erase earlier post-wake words;
- a bounded greeting latch accepts `hey`, a pause longer than 2.4 seconds, then a Parker-like name; bare `a` and intervening lexical speech remain negative;
- missing/offline local-model errors reach the explicit unavailable → power-off UI path;
- one `Try again` activation after microphone denial actually retries, and engine/UI power truth agrees;
- playback nodes and source citations are separate; `audio → sources → clear` stops every old source;
- reduced-motion disables all nonessential WebGL and CSS animation/transition;
- packaged evidence binds the app/sidecar to the reviewed SHA and does not infer OS microphone state or process death from unrelated HTTP/process signals;
- the wake-soak harness records achieved RMS/SNR after mixing/clipping and gates/reports only on achieved values; zero-minute soaks cannot support false-wake claims;
- generation fencing and stale-result suppression remain intact.

Verification:

- add the blocked-finalizer/upstream-close ordering reproduction from the #40 review;
- add a provider-blocked power-off test that observes cancellation at the provider boundary;
- add a shrinking rolling-tail test, delayed-tail-after-live-open test, and user/system role-boundary test;
- add real temporal PCM for `hey`, >2.4-second silence, Parker-like name, plus intervening-speech negatives;
- add missing-model/OSError, microphone-denial retry, and `audio → sources → clear` runtime tests;
- regenerate wake reports with explicit `not_run` sections and achieved SNR; build before probing and verify bundle/SHA and sidecar lifecycle;
- repeat concurrency/power deck five times;
- run full backend suite, Rust/Tauri tests, exact-SHA CI;
- Pras packaged real-mic power-off test.

### P0.2 — conservative spoken ending

Depends on: P0.1

Objective: preserve `OK, thanks` as the observed end signal without treating ordinary bare gratitude as completion.

Likely files:

- `backend/app/parker/realtime.py`
- `backend/tests/test_scenarios_session_end.py`
- `docs/plans/2026-09-02-spoken-session-end.md`

Acceptance:

- evidence-backed compound closers wind down;
- bare `thanks`/`thank you` after an answer remain conversational;
- a delayed follow-up after gratitude survives;
- hard enders, question negatives, pending offers, lookup gating, and barge-in remain correct;
- real-mic test covers an intended end and a mid-conversation thank-you with a long pause.

### P0.3 — date-grounded My Day

Depends on: P0.2

Objective: make My Day report records due in the requested local-day horizon rather than records created recently.

Likely files:

- `backend/app/parker/realtime_workers.py`
- `backend/tests/test_realtime.py`
- `docs/plans/2026-09-02-my-day-worker.md`

Acceptance:

- selection uses `execute_after`/captured due time and a timezone-aware local-day window;
- an old reminder due today is included; a new reminder due next month is excluded;
- overdue/open and tomorrow semantics are explicit;
- undated/stale free-text `today`/`tomorrow` notes are omitted or labeled date-uncertain;
- medicine names/times remain dose-free;
- no-calendar and partial-store-failure statements remain honest.

### P0.4 — semantic-union integration

Depends on: P0.1–P0.3

Objective: integrate #37/#40/#43/#45/#46 and their review fixes onto an exact live-main SHA without silently dropping a behavior, test, fixture, or document.

Acceptance:

- contribution matrix records every unique source/test/fixture change as present, equivalently present, or blocking;
- #46 motion behavior and reduced-motion/a11y fixes survive;
- generated reports are regenerated only from the final tree; skipped sections remain `not_run`, `--as-of` cannot mint a current-dated report, and scene receipts retry after session creation;
- at least one production-shaped SQLite contention test complements the faster isolated fixture;
- targeted decks, full backend suite, Rust tests, `git diff --check`, exact-SHA remote CI, and fresh final-tree review pass;
- no merge until Pras completes power/wake/session-end/package/visual gates.

## Phase 1 — local Hermes worker vertical

### P1.1 — isolated Hermes runtime

Depends on: P0.4 PASS

Objective: expose a dedicated `parker-worker` Hermes API server on loopback without exposing the default profile.

Configuration contract:

- profile: `parker-worker`;
- host: `127.0.0.1`, separate port, bearer auth, no CORS;
- max concurrent runs: 1;
- initial toolsets: web search/extract only;
- no shared Pras memory or session history;
- pin and record the tested Hermes version; feature-detect `/v1/capabilities` and the Runs API before use.

No secrets enter Git. The setup/runbook records key names and health checks only.

### P1.2 — cancellable Runs adapter

Depends on: P1.1

Objective: use Hermes `POST /v1/runs` → status polling → `/stop` behind Parker's existing typed worker seam.

Likely files:

- `backend/app/config.py`
- new bounded adapter under `backend/app/brain/` or `backend/app/parker/`
- `backend/app/parker/realtime_workers.py`
- `backend/app/parker/realtime.py`
- focused fake-server tests
- `docs/brain-adapters.md`

Acceptance:

- self-contained normalized text, local time, timezone, region, and output contract are the only request context;
- Parker validates a typed result containing short speech, sources, status, and no executed actions;
- timeout, malformed output, auth failure, unavailable server, and provider failure are honest;
- session close/generation replacement/power off calls Hermes stop and suppresses late results;
- Parker performs final medical/output screening and remains the only speaker;
- no user confirmation is required for this read-only lookup.

First experiential probes:

- a multi-source sports follow-up;
- a personalized media recommendation requiring comparison;
- a deliberately slow run cancelled by power off.

## Phase 2 — fast current-information bake-off

### P2.1 — provider-neutral benchmark harness

Depends on: P0.4; may run in parallel with P1

Objective: compare direct provider APIs without a second agent/model loop.

Candidates:

- Parallel `fast`;
- Parallel `turbo`;
- Exa `instant` with explicit freshness;
- Tavily `ultra-fast` as the incumbent baseline.

Corpus categories:

- completed sports result;
- in-progress score/state;
- tonight's schedule and Tampa channel;
- Tampa weather;
- very recent local/news event;
- an ambiguous contextual follow-up.

Record raw response receipts and score:

- provider and full-answer p50/p95 latency;
- exactness against a timestamped oracle;
- official/primary-source rank;
- publication/crawl freshness;
- excerpt sufficiency;
- contradictions and abstention;
- source coverage and cost.

Warm cache hits are reported separately and never used as cold-network evidence. Credentials are supplied out of band.

### P2.2 — deterministic current-fact routing and winner integration

Depends on: P2.1 winner

Objective: prevent the realtime model from answering a volatile fact from training memory.

Acceptance:

- every volatile test query enters retrieval before answer audio is permitted;
- unavailable/conflicting evidence produces a short honest abstention;
- one grounded synthesis step uses provider excerpts; no second full Claude/Hermes search loop;
- spoken provenance names the source plainly without reading URLs;
- target retrieval median is below 500 ms, p95 below one second, and grounded speech begins near 1.5 seconds where network conditions permit;
- fallback behavior is explicit and measured.

Single-hop current facts use the direct winner. Multi-step/open-ended research uses Hermes. Both return the same Parker-owned `WorkerResult` contract.

## Phase 3 — Curiosity Continuity

### P3.1 — one confirmed interest and later offer

Depends on: P1.2 and P2.2 household PASS

Objective: turn a current question into one durable, reversible open loop without exposing Hermes memory/cron directly.

Canonical flow:

1. Dad asks who is playing or what happened.
2. Parker answers from current evidence and supports a contextual follow-up.
3. Parker offers to remember one named interest and bring back an update later.
4. Only an explicit yes creates the Parker-owned record.
5. At a later powered-on interaction, Parker offers the update; it retrieves only after acceptance in v1.
6. `stop`, rejection, cancellation, or power off prevents further work.

Acceptance:

- interest subject, requested horizon, source provenance, consent outcome, and delivery state are explicit;
- no background provider work occurs while powered off;
- duplicate interests merge safely; changed intent replaces rather than accumulates;
- stale results cannot cross session/generation boundaries;
- skipped updates do not nag;
- session review shows what was asked, saved, retrieved, offered, and accepted without inventing outcomes.

## Phase 4 — household acceptance and sprint close

Run with Pras first, then Dad only by opt-in. Record:

- wake success/false wake;
- first useful audio latency;
- current-fact correctness/freshness;
- follow-up continuity without repetition;
- premature/failed session endings;
- stop/power-off latency and provider cancellation;
- correction count;
- whether Parker was preferred to the stock assistant for each task.

A sprint PASS requires executable receipts plus an exact-revision independent review. Same-family Fable reviewers remain inner-loop evidence, not the final merge authority.

## Fable launch brief

Use `/parker-session` with this plan as the intent source. Work phase by phase. After each phase: run the named local gates, push a coherent checkpoint, require green exact-head CI, run a fresh-context `/parker-review`, fix every confirmed blocker, and update this plan with revision/evidence/untested scope. Continue automatically only when the next phase's dependency is objectively PASS. Never merge power/wake/session-end work or claim human/device acceptance without Pras/Hermes review.

## Phase 0 ledger (2026-09-02, integration session)

Delivery on one integration branch, `fable/companion-integration` (draft
PR #49 → `main`), built from exact live `main` be91ecc with #37/#40/#43/#45/
#46/#48 merged explicitly (all clean; no blanket ours/theirs), then the
review fixes as commits on top. The stacked PRs are superseded by #49.

### Decisions taken on the review open questions

- Cancel plumbing: a contextvar (`realtime_workers.CURRENT_CANCEL`) set by
  the bridge's worker thread; provider cancel = socket shutdown through
  `app/brain/transport.py` (measured: `httpx.Client.close()` does not wake a
  blocked read on macOS; `sock.shutdown` does).
- Power-off route: in-memory release → revoke every socket and wait for
  provider quiescence → start durable persistence. The HTTP ack is not held
  behind SQLite: it returns `save_state=pending`, and the page polls settings
  to `saved`/`failed`. The authority remembers it released power in this
  process so a revoked page reads OFF during the write window; a later claim
  serializes against the old off write.
- Same-breath tail: text path (growing post-wake window, `tail_end` →
  final tail → one `tail` frame → one user-role item + one nudge,
  `TAIL_WAIT_SECONDS = 1.5`); PCM replay deferred to a live experiment.
- Greeting latch `GREETING_LATCH_SECONDS = 6.0` (audio time; measured
  silence envelope 7.0 s wakes / 7.1 s quiet, pinned at 6.8/7.4), reset on
  ≥ 3 non-greeting tokens; F5 give-up applies to the dormant lane only.
- Soft closer = compound closers only (`OK, thanks`, `that's helpful,
  thanks`); bare thanks stays conversation; the goodbye owes its own nudge
  and `closing` rides the goodbye's done (real VAD order).
- My Day: reminders by `execute_after` / pending `CapturedIntent.due_at` in a
  home-tz local-day window; undated → "no time on record"; notes labelled
  within 7 days, older omitted; the reminder cap says "…and N more".
  Naive due strings are home wall time, stored as naive UTC.
- Reduced motion: one universal `* {animation:none; transition:none}` block.
  Mic denial releases the claim; a click from `error` retries; realtime
  `unavailable` returns to dormancy with the honest card.
- `anthropic` pinned `<1`: 1.x moved to `httpx2` and rejects the httpx
  cancellable client (found by exact-SHA CI at 1038242; the local venv was
  0.109.1). The builder now logs the swallowed constructor error.

### Acceptance status

P0.1 — all eight PR #40 blockers implemented with red-then-green tests
(strict power-off + provider-boundary cancel; ordered same-breath handoff
with a user-role tail; bounded greeting latch; playback/citation split;
fail-closed missing model; mic-denial retry; universal reduced motion;
truthful evidence harness/probe/provenance). P0.2 — compound closers only,
delayed follow-up survives, VAD-order goodbye. P0.3 — date-grounded My Day
through the real capture pipeline. P0.4 — semantic union on exact live
main (contribution matrix: `docs/reviews/2026-09-02-p0-contribution-
matrix.md`, 0 MISSING), production-shaped SQLite contention tests,
`--as-of` cannot mint a dated report, scene receipts retry (pinned).

The 1a88534 handoff checkpoint also closes the three fresh Hermes review
groups below. Final gate execution then found five deterministic suite
regressions plus one untested register-before-`run()` power race and a broken
direct Node gate. b3bc12d closes those with red-capable tests: only a true
power-off waits for provider quiescence (same-owner handover remains live), a
pre-revoked bridge is already quiescent and never opens upstream, async
power-save expectations poll the durable state, and each page spec can extract
the real inline page scripts when invoked by the documented Node glob.

### Evidence by revision

| revision | what | evidence |
|---|---|---|
| 6f3e3ed | pure merge tip (main + six PRs) | backend 1254 passed; Rust 16; exact-SHA CI green (run 33671007914) |
| 89c00be | bridge slice (F1 shutdown, F2 user-role tail, P0.2) | backend 1261 passed; live realtime probe PASS with `--wake-tail "can you help me with the tv" --pending` (first reply answered the tail, tail as a user item, never in a system item, API accepted the payload) |
| f757fd3 | probe extension | exact-SHA CI green (run 33680754506) |
| 1038242 | six implementation slices merged + reconciliation | backend 1319 passed; concurrency/power deck 9/9 ×5; Rust 17; Node companion spec 49/49; packaged chain PASS bound to 1038242 (engine + shell report the SHA, sidecar exited 0.5 s after the shell, webgl_ready receipt, no power claim, no wake socket); real-model wake soak shakedown (session scratchpad, superseded by the 7af4fd9 report); exact-SHA CI **FAILED** (run 33682939430): anthropic 1.3.0 (httpx2) rejected the cancellable httpx client and the builder swallowed it — a fresh install would have lost every lookup; fixed at ef66a17 |
| 1038242 | fresh-context review (nine lenses, adversarially verified) | all nine NEEDS_FIX; every blocker/major finding was verified by two refuters: 14 distinct confirmed (2 blockers: the SDK/CI break; the revoked screen re-claiming power during the write window) and 2 reframed as coverage gaps (now pinned by tests: SDK-over-transport cancel on a real socket, the latch envelope) |
| ef66a17 | review fix round (bridge/route/power/pin) | backend 1325 passed; concurrency deck 10/10 |
| aacd751 | fix round merged (page, evidence, My Day, transport/latch tests) | backend **1337 passed**; Rust 17; Node companion spec 55/55 (inside the suite) |
| d3b5d77 | ledger + contribution matrix (docs) | exact-SHA CI **green** (run 33692044612) with the pin in place |
| d3b5d77 | fresh-context review of the fix round (four lenses, adversarially verified) | page PASS, My Day PASS; bridge/power NEEDS_FIX on one test-integrity major (the negative-space pin passed without the fix — closed at 7ec9eeb); evidence/docs NEEDS_FIX on two majors (the soak narrative misattributed misses — rewritten below; the build chain stamped `-dirty` because untracked files were present — rebuilt on a clean tree, below). All seven bridge findings, all page findings, and all My Day findings judged closed. |
| 7af4fd9 | wake soak regenerated on the final code (`benchmark/reports/wake_soak_2026-09-02_base.{json,md}`, schema wake_soak_v1) | recall 48/48; over-TV labelled with achieved SNR (+12 dB 4/4, +6 dB 4/4, 0 dB 0/4, −6 dB 0/4); paused greeting 19/24, stale-greeting quiet 6/6; 1 ambient-TV false wake (`hey i'm parker` — the single-window grammar's one-token tolerance, not the latch); **Gate: FAIL** on the paused section and the false wake — see the reading below. Live realtime probe on this code: PASS (`--wake-tail --pending`; probe revision line printed) |
| 7ec9eeb | negative-space pin seeds the durable flag ON | test can only pass with the authority's `released` override |
| ea0ef7c | packaged chain on a clean tree | PASS — `make sidecar` → `sidecar_smoke.sh` → `cargo tauri build` → `packaged_companion_probe.sh` (default expectation = HEAD, clean tree): bundled engine and shell both report ea0ef7c; the engine ran as the shell's child and exited 1.5 s after it; WKWebView posted `webgl_ready`; no power claim, no wake socket (the probe states it does not observe TCC/microphone or pixels) |
| 342edfe | ledger filled (docs) | exact-SHA CI **green** (run 33694627135) — the code is identical to ea0ef7c/7ec9eeb; cross-family (Hermes / GPT) review: **not completed** — started against 342edfe and stopped at wrap-up (credits); packet at the session scratchpad `hermes-review-packet-final.md`; re-run it before merge |
| 1a88534 | checkpoint after three fresh Hermes blocker reviews | power lifecycle, My Day, and session-end fixes present; intentionally **not release-ready**: the resumed full suite exposed five failures |
| `b3bc12d` | final blocker/gate repair | `make test`: **1354 passed**; focused concurrency/power/My Day/session-end deck: **161 passed × 5**; Rust/Tauri: **17 passed**; direct Node glob: companion **56/56**, lab **3/3**, expression **48/48**; voice scenarios: **98 passed**; `git diff --check`: clean |
| `bb3fe01` | canonical UTC evidence refresh (2026-09-03) | all ten dated JSON/Markdown pairs byte-identical to their `latest` mirrors; read-only release evaluator against the committed reports: **PASS**, no blocking failures; task taxonomy 24/24, demo interactivity 9/9, degraded replay 3/3 vs 0/3 no-repair, caregiver legibility 10/10, repair rubric 5/5, wake context 14/14, zero unsafe misses in the named gates |
| `329b057` | scheduled-wrapper CI gate repair | restored the two Make recipes the semantic union had silently reduced to phony no-ops; static target pins pass; contract **15/15**, inactive harness **9/9** with one bounded worker and zero live activations; full backend **1356 passed** |
| `3e97c2d` | first fresh-review blocker closure | OFF preempts blocked/queued ON claims; retired bridges remain power-owned; startup and delivery are cancel-safe; actual provider computation is tracked through timeout; unspoken results remain in flight; My Day includes undated pending reminders with truthful cardinality; README stack contradictions corrected. Full backend **1380 passed**; focused six-file deck **182 passed × 5**; Rust/Tauri **17 passed**; direct Node **56/56 + 3/3 + 48/48**; voice scenarios **98 passed**. |
| `3a8833b` | first-review ledger closure + second fresh review source | power lifecycle **PASS**; release evidence/docs **PASS**; My Day/session correctness **NEEDS_FIX** on two new edge cases: search/My Day key aliasing and safety-filtered records disappearing from both omission counts and the empty-day decision. |
| `064b60d` | second-review blocker closure | search keys are structurally namespaced; safety-filtered records retain generic counted presence without exposing content; stale power comment and medication README wording corrected. Full backend **1383 passed**; focused six-file deck **185 passed × 5**; Rust/Tauri **17 passed**; direct Node **56/56 + 3/3 + 48/48**; voice scenarios **98 passed**; release-readiness **PASS**. |
| `2630996` | final narrow-review source | **PASS** on the two second-cycle fixes and ledger claims; the reviewer independently confirmed all three new regressions pass here and fail on `3a8833b`, then reran the exact six-file deck (**185 passed**). Aggregate final review: power PASS, My Day/session PASS, evidence/docs PASS. Artifact: `docs/reviews/2026-09-03-pr49-hermes-final-review.md`. |
| `76f0011` | reviewed release-candidate receipt | Exact-head hosted CI **PASS** (run `33712062093`, job `100513492079`). Clean package chain **PASS**: `make sidecar`, `scripts/sidecar_smoke.sh`, `cargo tauri build`, and `scripts/packaged_companion_probe.sh --expect-sha 76f0011596f148c2970880751f8988ab63d273de .../Parker.app`; both engine and shell reported the full SHA, WKWebView posted `webgl_ready`, power stayed OFF with no wake socket, and the bundled engine exited with the shell. |

### Fresh-review blocker closure

| concern | blocker | status | revision / permanent check |
|---|---|---|---|
| power | background provider work could outlive OFF | closed | 1a88534; provider-boundary cancel and quiescence tests in `test_companion_power.py` |
| power | in-flight upstream connection setup was not cancellable | closed | 1a88534; pre-`run()` registration race additionally closed at b3bc12d in `test_realtime.py` |
| power | OFF acknowledgement waited behind session/database persistence | closed | 1a88534; pending → saved/failed page polling pinned in Python and Node tests |
| power | a new claim could race an older async OFF write | closed | 1a88534; generation + persistence-lock interleaving tests |
| power | wake socket was unowned while the local model warmed | closed | 1a88534; warmup-revocation test |
| power | a revoked screen could transiently report ON | closed | 1a88534/7ec9eeb; durable-ON negative-space pin |
| power | same-owner handover was blocked by an abandoned provider thread | closed | b3bc12d; only `power_off` awaits provider quiescence; two concurrency scenarios are in the five-repeat deck |
| power | blocked ON persistence held the state lock and delayed synchronous OFF | closed | 3e97c2d; transition tickets plus blocked and queued-claim concurrency pins |
| power | superseded bridges disappeared from later OFF accounting | closed | 3e97c2d; retired registrations remain power-owned through true quiescence |
| power | revoke could strand the supervisor in a pre-pump await | closed | 3e97c2d; four gated startup-phase cancellation tests |
| power | semantic timeout could mark provider quiescent while its thread still ran | closed | 3e97c2d; computation and delivery tasks split, with abandoned-thread pin |
| power | a suspended result delivery could resume after revoke | closed | 3e97c2d; delivery cancellation plus post-await closed fences |
| My Day | failed-source truth could be truncated | closed | 1a88534; source-health line appended after item cap |
| My Day | the prefilter could hide plan-like notes | closed | 1a88534; seven-day scan then bounded truthful omission line |
| My Day | generic plan-like notes lacked date uncertainty | closed | 1a88534; explicit uncertainty phrasing |
| My Day | fixed-offset local time lost future DST rules | closed | 1a88534; named `tzlocal` zone with winter/summer pin |
| My Day | due timestamps were not canonical instants | closed | 1a88534; aware → UTC, naive wall time with DST gap/fold rejection, outbound `Z` |
| My Day | unresolved future reminders sounded confirmation-ready | closed | 1a88534; “recorded; not set yet” |
| My Day | recent unresolved undated intents were omitted entirely | closed | 3e97c2d; pending-undated two-day-window regression |
| My Day | medication defaults compared UTC digits with local schedules outside realtime | closed | 3e97c2d; central named-zone normalization with winter/summer pins |
| My Day | nested source summaries made the global omission total false | closed | 3e97c2d; structured represented-record cardinality and mixed-source regression |
| My Day / realtime | arbitrary search text could alias the reserved My Day result key | closed | 064b60d; disjoint `search:` namespace and handler-level collision pin |
| My Day | safety-filtered records vanished from omission counts and could trigger a false empty-day claim | closed | 064b60d; generic counted blocked-record line plus all-filtered/global-cap regressions |
| session end | a lookup stopped counting as in-flight before its result was actually spoken, including cancelled/incomplete/silent responses | closed | 1a88534 + 3e97c2d; response-bound obligations requeue until completed observable speech; browser Stop stays silent |
| release evidence | the ledger could imply the old `ea0ef7c` app was code-identical to final | closed | 3a8833b; old package explicitly invalid for final-head testing |
| release evidence | wake-soak summary contradicted the committed Fred-voice misses | closed | 3a8833b; five misses split accurately across 120/175 wpm plus the TV false wake |
| public docs | shipped wake/realtime capabilities were also listed as future | closed | 3e97c2d; stack rows distinguish local talk, opt-in realtime, and later evidence/provider work |
| release gate | `node --test backend/tests/js/*.spec.js` exited on missing generated-script arguments | closed | b3bc12d; specs extract the real page scripts when run directly |
| release gate | CI named both scheduled-wrapper evaluators, but their phony Make targets had no recipes and passed without executing anything | closed | 329b057; restored historical recipes plus target-presence regressions; observed 15/15 + 9/9 real checks |

### Current automated-gate checklist

- [x] Full backend suite: 1383 passed, 2 known deprecation warnings.
- [x] Rust/Tauri tests: 17 passed.
- [x] Direct companion/lab/expression Node specs: 56/56, 3/3, 48/48.
- [x] Voice scenario deck: 98 passed.
- [x] Focused concurrency/power/My Day/session-end deck: 185 passed in each of five consecutive runs. Exact command: `backend/.venv/bin/python -m pytest -q backend/tests/test_scenarios_concurrency.py backend/tests/test_companion_power.py backend/tests/test_realtime_workers.py backend/tests/test_my_day_worker.py backend/tests/test_realtime.py backend/tests/test_scenarios_session_end.py`.
- [x] Canonical UTC report refresh and read-only committed-artifact rollup: PASS for 2026-09-03; dated/latest pairs are byte-identical.
- [x] Scheduled-wrapper contract and inactive harness: 15/15 and 9/9 real checks (not phony no-ops).
- [x] `make eval-repair` executed honestly: no `ANTHROPIC_API_KEY`, so the optional model-backed repair eval reported **skipped** and produced no evidence. No key was fabricated or read from `.env`.
- [x] Fresh final-tree independent review artifact bound to source `26309965a5f57ac18516e3d89379c0480bc212cd`: `docs/reviews/2026-09-03-pr49-hermes-final-review.md`.
- [x] Green GitHub CI for reviewed/package source `76f0011596f148c2970880751f8988ab63d273de`: run `33712062093`, job `100513492079`.
- [x] Clean `76f0011` Parker.app build + sidecar smoke + packaged companion probe; exact path: `/Users/prasithgovin/Development/personal/parkinsons-assistant/desktop/src-tauri/target/release/bundle/macos/Parker.app`.
- [ ] Pras human/device checks below; never claimed by automation.

The receipt-only ledger commit after `76f0011` does not change `backend/app`, the desktop source, dependencies, or scripts. Its own exact-head CI and SHA-stamped rebuild are recorded in PR #49's mutable body and the final handoff rather than falsely self-referencing a commit hash from inside that same commit.

The earlier `.app` bound to `ea0ef7c` is not valid final evidence because it predates the runtime and dependency fixes. The `76f0011` package above replaces it; the receipt-only head still requires its own SHA-stamped rebuild before handoff.

### What the wake soak says (real base model, synthesized voices, final tree)

Recall on isolated phrases 48/48. Over the TV the rows are labelled with the
SNR the mix achieved (the bed is attenuated instead of the voice clipping):
+12 and +6 dB wake 4/4 each, 0 and −6 dB 0/8 — the documented measured
limit. The paused-greeting section (real silence between "hey" and the
name) is new evidence for the latch: Samantha and Daniel wake 16/16 across
both rates (latch_s 1.5–3.8 s); the five misses are all the Fred voice
(3/8): in three the local ASR never produced a Parker-like token for the
lone slow name (`Paracure`, `Tarakur`, `Tarko`); in one it never heard the
greeting (`Oh`, `I'm a`) so nothing armed; in one the greeting armed, then a
garbage partial decode of the name's onset (`Tarek her. Can you...`, four
tokens) cleared the latch one hop before the clean `Parker, can you help
me?` — the reset-on-hallucination interaction is a design question for the
room test, not a unit-pinnable defect. The bare-"a" rows woke 4/6 because
the model transcribes the synthesized "a" as `Hey` (an ASR artefact; the
unit pin keeps a transcribed `a` from arming the latch). The one
ambient-TV false wake, `Hey, I'm Parker something`, matches the
pre-existing single-window grammar (one token tolerated between greeting
and name — the chairman's Dad calibration), not the latch. CPU/latency
figures in this report ran while review agents shared the machine and are
not evidence.

### Untested / open

- Human/device gates (unchanged, NOT claimed): Pras's packaged real-mic
  power-off (TCC transition, true revocation); real-mic session end
  (intended "OK, thanks" ends; mid-conversation "thanks" + long pause +
  follow-up does not); Dad-like wake recall, paused greeting, ambient TV,
  same-breath request, stop/barge-in in the room; VoiceOver order, no-WebGL,
  reduced-motion runtime; Reachy motion quality.
- The realtime lane still stores no due time for spoken reminders
  (`propose_action` has no `when`); every spoken reminder reads "no time on
  record" until that follow-up.
- `anthropic` 1.x / `httpx2`: the cancellable transport must be ported
  before lifting the pin.
- Realtime `unavailable` (no OpenAI key) now rests honestly with a card;
  a real-key-missing packaged run is a human gate.
- The wake soak remains gated and currently FAILs on five Fred-voice paused-
  greeting misses (three at 120 wpm and two at 175 wpm), plus the separately
  recorded ambient-TV false wake. Human room disposition remains open.
