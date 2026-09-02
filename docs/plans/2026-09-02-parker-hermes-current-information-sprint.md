# Sprint: land the companion stack, then prove Parker + Hermes

Date: 2026-09-02
Status: ready for implementation after review findings are accepted

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
