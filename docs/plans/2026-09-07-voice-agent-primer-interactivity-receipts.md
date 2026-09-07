# Voice-agent primer intake: realtime interactivity receipts

**Date:** 2026-09-07
**Status:** implemented and independently reviewed; hosted checks tracked on [PR #50](https://github.com/prasithg/parker/pull/50)
**Source:** [Voice AI & Voice Agents — An Illustrated Primer](https://voiceaiandvoiceagents.com/)

## Goal

Turn the primer's highest-value product lesson into a Parker learning loop: capture bounded, honestly named browser-side proxies for response latency, premature turn endings, and interruption-control latency in the existing realtime session journal and review surface.

## Done when

- The realtime relay forwards provider speech-start and speech-stop markers with a relay-assigned turn id, maps each provider response id to the most recently stopped turn, and tags relayed audio with that turn id; it does not expose provider payloads wholesale.
- The real companion page records exactly three allowlisted client signals:
  - `provider_stop_notice_to_first_playback_ms`;
  - `speech_start_notice_to_local_flush_ms`;
  - `turn_reopened_before_output`.
- Missing audio produces no fake zero latency; repeat audio chunks produce one first-playback metric per stopped turn.
- Realtime client frames are typed, range-checked, capped, and best-effort; bad telemetry cannot break a call.
- The session journal and actual review page render the metric name, value, unit, and honest boundary.
- The article is captured comprehensively in `~/Knowledge/parker/2026-09-07-voice-agent-primer-parker-field-guide.md`, with section anchors, source/adaptation labels, Parker decisions, and an implementation backlog.
- Targeted tests, scenario tests, the full project gate, diff checks, independent code review, and exact-SHA CI pass.

## Context

- Parker already uses a server-relayed OpenAI Realtime WebSocket, low-eagerness semantic VAD, browser playback, playback flush on provider `input_audio_buffer.speech_started`, a bounded session journal, and a local review page.
- `docs/voice-agent-architecture.md` already requires client-measured interaction latency and explicit false-endpoint/interruption evaluation, but the live companion does not capture those measurements.
- The primer treats latency tails, turn completion, interruptions, and tool reliability as coupled product behavior, not generic infrastructure: [latency](https://voiceaiandvoiceagents.com/#latency), [turn detection](https://voiceaiandvoiceagents.com/#turn-detection), [state-of-the-art turn detection](https://voiceaiandvoiceagents.com/#sota-turn-detection), [interruption handling](https://voiceaiandvoiceagents.com/#interruption-handling), and [voice evals](https://voiceaiandvoiceagents.com/#evals).
- Baseline on the untouched branch: `101 passed` for realtime/session-review/expression tests.
- An initial broader truncation/playback-truth plan failed independent architecture review three times because reliable playback accounting needs an explicit drained-receipt and reclaim state machine. Per the stop rule, this plan narrows to instrumentation; it documents that P0 separately rather than disguising an underspecified patch as complete.

## Architecture decision

The relay remains the trust boundary. It converts only provider `speech_started` and `speech_stopped` events into a small browser frame:

```json
{"type":"speech_state","status":"started|stopped","turn_id":7}
```

The relay increments `turn_id` on each provider speech-start and forwards at most one matching stop for that id; a stop before any start or a duplicate stop is dropped. For each provider `speech_started`, the relay sends `speech_state: started` **before** the existing `clear` frame. The browser stores the start-notice time on the first frame, and the subsequent `clear` frame remains the sole playback-flush trigger and records the metric after local source-stop calls complete. There is no second flush.

On `response.created`, the relay maps that provider response id to the most recently stopped turn (or zero for greeting/system output); every later JSON audio frame for that response carries only that mapped `turn_id` in addition to its existing base64 `data`. Two responses created after one stop both map to that stopped turn. A response created after the next speech-start but before its stop still maps to the previous completed turn because no new completed user turn exists yet. Provider timing fields are not treated as the browser clock.

The browser records receipt times with `performance.now()`:

- On `stopped`, it opens one pending turn marker.
- When the first PCM buffer with the same `turn_id` is scheduled, it sends `provider_stop_notice_to_first_playback_ms`. The value is elapsed `performance.now()` plus `max(0, scheduledTime - currentTime)` from the AudioContext, so absolute clocks are never subtracted and late scheduling never makes the value negative.
- If another `started` arrives while that stopped marker has not produced matching audio output, it sends `turn_reopened_before_output = 1` and immediately closes that pending marker. Audio scheduled with no pending marker or a different turn id emits no response-latency metric. This is an endpoint/output-recovery candidate for human review—not proof of an interruption or false endpoint—and intentionally also catches a response that completed without audible output.
- When `started` arrives while Parker audio is locally queued, the browser times the existing `clear`/flush path and sends `speech_start_notice_to_local_flush_ms` after the local `AudioBufferSourceNode.stop()` calls complete. This is a control-path proxy, not audible silence.
- Tool-only, text-only, error, or pre-audio cancellation turns emit no first-playback metric.

The browser sends:

```json
{"type":"voice_metric","name":"…","value":123,"turn_id":7}
```

The relay accepts only the three names above, integer turn ids from 1 through its current issued counter (with an absolute ceiling of 1,000,000), and finite numeric values from 0 through 60,000 ms (`turn_reopened_before_output` must be exactly 1). A future/unissued turn id or out-of-range value is dropped rather than clamped into a plausible-looking measurement. Only accepted, journaled metrics count toward the 200-frame cap; invalid frames cannot consume the budget. On the first valid overflow it journals one `metrics_capped` event, then silently drops later metrics. No global journal cap applies to these rows; the existing 400-row cap is expression-specific. The boundary, source, and unit are generated server-side from the metric name, never trusted from the browser.

If a pathological session reaches turn id 1,000,000, later starts/stops are not assigned an id, later responses are tagged turn `0`, later client metrics are rejected, and one `turn_tracking_capped` fact is journaled. The counter never wraps or reuses an id.

These are proxies, not end-to-end claims. The response metric starts when the browser receives the provider stop notice, not at the physical end of speech. The interruption metric starts when the browser receives the provider start notice, not when the user began speaking. Real mic-to-speaker latency, false interruptions, and exact played-text truth require waveform/playhead instrumentation and human/device tests.

## Constraints / negative space

- No new model, provider, transport, framework, dependency, dashboard, audio retention, or medical inference.
- Do not tune VAD thresholds from synthetic metrics alone.
- Do not call these measurements mic-to-speaker, user-end-to-audio, or proof of Parkinson's-speech performance.
- Do not change wake behavior, audio format, model choice, tool execution, action confirmation, guard behavior, or PR #49.
- Do not implement `conversation.item.truncate` until the explicit playback-drained/reclaim design passes a new plan review.

## Tasks

### T1 — Write red-capable server and review tests
Depends on: none

**Files:**
- `backend/tests/test_realtime.py`
- `backend/tests/test_session_review.py`

**Acceptance:**
- start/stop markers carry the stable turn id;
- provider start produces `speech_state: started` followed by the existing `clear` frame in that exact order;
- stop without a prior start is ignored;
- a duplicate stop for the same turn is ignored;
- response created after stop N tags its JSON audio frames with N;
- response created before any stop tags audio with 0;
- two responses after one stop both tag N;
- response created after start N+1 but before stop N+1 still tags the most recently completed turn N;
- valid metric frames journal server-authored boundary/unit/source fields;
- wrong-name, future/unissued-turn, non-finite, oversized, and overflow frames cannot break the socket or spoof fields;
- invalid frames sent before valid metrics do not consume the 200 accepted-frame budget;
- the 201st metric produces one cap event and later frames produce no further rows;
- the actual sessions-page HTML contains human-readable branches for all three metrics and the proxy-boundary text.

**Verification:**
- `backend/.venv/bin/pytest -q backend/tests/test_realtime.py backend/tests/test_session_review.py` must fail first for the missing contract, then pass.

### T2 — Write red-capable real-page Node tests
Depends on: none (may run in parallel with T1)

**Files:**
- `backend/tests/js/companion_page.spec.js`

**Acceptance:**
- one stop marker plus multiple audio chunks emits one first-playback metric;
- a second start before output emits one reopened-turn candidate;
- after reopening, an old-turn audio chunk emits nothing; after the next stop, a fresh matching-turn chunk emits exactly one metric for the new turn;
- a chunk scheduled after reopen with no pending marker emits nothing;
- queued playback plus the real ordered `speech_state: started` then `clear` frames emits one local-flush metric;
- no pending stop/no queued audio emits no false metric;
- power-off and stale sockets emit nothing;
- a telemetry `send` exception does not prevent PCM scheduling or playback flush.

**Verification:**
- `backend/.venv/bin/pytest -q backend/tests/test_expression_state.py` must fail first, then pass.
- Existing `test_expression_state.py::test_companion_page_lifecycle_spec_passes` extracts the real first inline companion script and runs this Node spec.

### T3 — Implement the smallest relay, page, journal, and review changes
Depends on: T1, T2

**Files:**
- `backend/app/parker/realtime.py`
- `backend/app/parker/companion_ui.py`
- `backend/app/parker/session_review.py`
- `backend/app/parker/sessions_ui.py`

**Acceptance:**
- all T1/T2 tests pass;
- telemetry failures remain unable to affect audio/control flow;
- no new dependency or persistence table is added.

**Verification:**
- targeted commands from T1/T2;
- `backend/.venv/bin/pytest -q backend/tests/test_scenarios_speech.py backend/tests/test_scenarios_concurrency.py`.

### T4 — Commit the source-backed knowledge and repo decisions
Depends on: T3

**Files:**
- `~/Knowledge/parker/2026-09-07-voice-agent-primer-parker-field-guide.md`
- `docs/voice-agent-architecture.md`
- this plan

**Acceptance:**
- all major primer topics are mapped: pipeline/model choice, STT, TTS, audio, transport, turn-taking, interruption, context, tools, multimodality, multiple models, prompting, evals, telephony, memory/RAG, hosting/cost, and model churn;
- source claims, Parker adaptations, shipped behavior, and deferred/rejected work are visibly distinct;
- the unresolved WebSocket playback-truncation invariant is P0 before claiming truthful interruption context.

**Verification:**
- `git diff --check -- docs/voice-agent-architecture.md docs/plans/2026-09-07-voice-agent-primer-interactivity-receipts.md`
- `python3 -c "from pathlib import Path; p=Path.home()/'Knowledge/parker/2026-09-07-voice-agent-primer-parker-field-guide.md'; s=p.read_text(); assert all(a in s for a in ['#latency','#speech-to-text','#text-to-speech','#audio-processing','#network-transport','#turn-detection','#interruption-handling','#managing-conversation-context','#function-calling','#evals','#rag-memory','#hosting'])"`

### T5 — Verify, review, and publish the stacked milestone
Depends on: T4

**Acceptance:**
- `make test`, diff checks, and private/public-data checks pass;
- independent Fable code review returns PASS after at most two focused repair cycles;
- the coherent commit is pushed on this branch and opened as a PR based on `fable/companion-integration`, leaving PR #49's exact candidate unchanged;
- exact-SHA hosted CI is green.

A third blocking code-review result stops the slice on the branch for architectural reconsideration.

**Verification:**
- `make test`
- `git diff --check`
- `git status --short`
- `gh pr checks <new-pr> --watch`

## Execution receipts

- RED was observed for the new relay, real companion-page, and review-page contracts before implementation.
- Focused new realtime contracts: `4 passed`.
- Real companion-page lifecycle, including dropped-line reconnect: `1 passed`.
- Session-review rendering contract: `1 passed`.
- Parker voice scenario deck: `98 passed`.
- Full local gate after the final reconnect and test-race corrections: `1385 passed, 1 failed, 2 warnings` out of 1386 collected. The only failure is `test_a_lock_held_past_the_default_busy_timeout_costs_a_retry_never_the_write`; it reproduces unchanged at base SHA `869eb09` in an isolated worktree because the contender does not always begin before the six-second lock is released. Its data-integrity observables still pass. No changed-area test fails.
- Independent Fable 5.1 code review: `PASS`; focused follow-up review of the client/review-page hunk and reconnect reset: `PASS`.
- `git diff --check`: pass. Required knowledge-guide anchors: 12/12 present. Added-lines secret-pattern scan: no finding.
- Hosted CI passed on implementation commit `a394b90f9d554ae17c0829b70f6ed8bdd4d65d48`: runs [34156352488](https://github.com/prasithg/parker/actions/runs/34156352488) and [34156354949](https://github.com/prasithg/parker/actions/runs/34156354949), including backend tests and every configured release eval.

## Explicit next P0, not claimed here

The client still needs playhead-based `conversation.item.truncate` on WebSocket interruption so provider context and Parker's stored “said” history exclude generated words the person never heard. That correctness slice requires response/item/content identity, per-item playback cursors, duplicate/stale receipt handling, and conservative behavior when a receipt is lost; this metrics change deliberately does not claim it.

## Rollback

Telemetry is additive. If client frames destabilize the live loop, remove `speech_state` forwarding and `voice_metric` handling together; existing playback, provider interruption behavior, and session review remain unchanged. The knowledge artifact and deferred P0 can remain because they describe source-backed decisions rather than shipped claims.
