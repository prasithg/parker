# Fable handoff: low-latency Patient Curiosity Loop

Date: 2026-08-29
Planning target: laptop/browser demonstration for Dad
Execution authority: plan only until Pras approves the sprint plan

## Prompt for Fable

Read, in order:

1. `AGENTS.md` and `CLAUDE.md`
2. `docs/strategy/2026-08-29-problem-first-value-proposition.md`
3. this handoff
4. `docs/brain-adapters.md`
5. `backend/app/demo/talk.py`
6. `backend/app/demo/talk_loop.py`
7. `backend/app/conversation/textloop.py` around `TextSession`, `_answer`, and brain history
8. `backend/app/brain/adapter.py`, `claude.py`, `openclaw.py`
9. `backend/app/voice/record.py`, `transcribe.py`, and `speak.py`
10. relevant tests under `backend/tests/test_talk_loop.py`, `test_brain_lane.py`, `test_brain_lane_evaluator.py`, `test_screen.py`, and `test_first_session.py`

Produce a repository-grounded sprint plan for the Patient Curiosity Loop. Do not implement. Challenge the decisions below when source or tests contradict them. Keep one integration owner, use read-only architecture/eval subagents only, and preserve the current dirty two-file stale-listening fix without overwriting or reverting it.

The plan must name exact likely paths, red-capable tests, latency instrumentation, human/device gates, rollback, and what remains unverified. End with a recommended sprint order and a separate list of decisions Pras must make.

## Goal

Build a laptop/browser harness that lets Dad:

- tap once to start listening;
- take time, pause, trail off, or restart;
- tap Done when his thought is complete;
- see what Parker heard;
- repair one unclear part if necessary;
- receive a brief, current answer about weather, sports, or something prompted by a video/conversation;
- ask one natural follow-up without restating the subject;
- stop Parker immediately by touch or voice;
- judge whether this was easier than Google Home.

## Done when

A fresh local session completes these three real-user-shaped traces from a laptop:

1. Weather: "What is the weather today?" -> "What about tomorrow?"
2. Sports: one current score/result -> one contextual follow-up.
3. Interest/video: one open question about something Dad just watched -> one follow-up.

For all three:

- listening state appears immediately;
- manual Done prevents pause-based cutoff;
- transcript is visible before the answer;
- one repair can target only the unclear part;
- answer is brief and current, with source/freshness visible but not read aloud as a URL;
- follow-up context is correct;
- Stop cancels speech immediately and late results cannot overwrite the stopped screen;
- no action, message, purchase, medical advice, or external side effect is performed;
- temporary audio is deleted;
- latency receipts are written locally;
- the user can exit cleanly after one turn.

## Current repository reality

Reuse these shipped seams:

- `run_talk_loop` owns one persistent `TextSession`, local ASR, wake gating, deterministic safety, repair, outcome recording, and confirmation.
- `TextSession` already keeps up to 12 brain-lane turns for follow-ups.
- `BrainAdapter` keeps conversation separate from action authority. The brain may speak and propose; it cannot capture or execute.
- Local faster-whisper `base` is loaded once by `prepare_talk_dependencies`, then reused.
- Dad Screen already shows heard text, Parker speech, choices, and current loop state.
- Claude brain is synchronous and deliberately honest that it may not have live data.
- OpenClaw brain is synchronous (`stream: false`) and fake-gateway tested. A real gateway can use tools, but current replies do not expose a structured source list.
- macOS `say` is blocking and cannot currently be interrupted through Parker.
- Current VAD ends after 1.2 seconds of silence after speech. Dad's pauses may exceed that.
- Existing latency receipt measures only utterance-end -> full ASR + route completion; it does not measure UI response, first audio, or Stop latency.

Do not create a second assistant or bypass `TextSession` to make the demo look fast.

## Architecture decisions to test

### 1. Browser-first harness

Add a single local page such as `/parker/converse` rather than making the CLI the first-user interface.

Use large controls:

- Start listening
- Done talking
- Stop Parker
- Try again

Dad can use touch, so manual Start/Done is the honest first prototype. It removes endpointing as a hidden requirement and respects long pauses. Wake and adaptive VAD can remain available as later modes.

The page should show:

- current state: idle, listening, processing, speaking, stopped;
- transcript/interpretation;
- repair choices when needed;
- brief answer;
- source names/freshness for current information;
- per-turn latency in a developer-only details panel.

### 2. Reuse one persistent session

Create a bounded server-side conversation session keyed by a random local session ID. It owns:

- one warmed transcriber;
- one `TextSession` and its brain history;
- an incrementing turn/generation ID;
- in-flight cancellation state;
- last activity and bounded expiry.

A stale turn must never update the screen after Stop or a newer turn.

Do not persist raw audio. Persist only the same bounded transcript/outcome artifacts current Parker already owns.

### 3. Manual audio capture first

For the harness, capture browser microphone audio between Start and Done. Prefer a predictable local format that the existing transcriber can consume without a new cloud path. Compare:

- 16 kHz mono WAV encoded in the browser; and
- MediaRecorder WebM passed through the existing local decoding path.

Choose based on measured capture/encode + ASR latency and packaged browser support. Keep a strict byte/time cap and delete the temporary file in `finally`.

Do not add streaming ASR in the first sprint unless warmed batch ASR misses the budget. Manual Done already removes 1.2-second VAD latency and cutoff errors.

### 4. Fast answer provider, still behind the brainstem

Do not use the Claude brain for current weather or scores without a live source; its prompt correctly says it may lack live data.

Spike two read-only answer routes against the same six scripted questions:

A. Existing OpenClaw gateway with the smallest current-information tool surface and a fast model.
B. A narrow current-information provider behind the existing brain contract, returning `speech`, `sources`, and `freshness` with no action authority.

Select one using measured latency, answer correctness, source visibility, follow-up continuity, failure behavior, and deployment friction. Do not select on one happy-path response.

If OpenClaw wins, extend its reply contract minimally so the screen can show source labels and freshness without speaking URLs. Do not expose broad browser/account tools to the Dad harness.

Keep replies to one or two spoken sentences. Offer more on follow-up.

### 5. Interruptible browser TTS for the harness

Use browser `speechSynthesis` as a harness-specific speaker because `speechSynthesis.cancel()` provides an immediate local Stop path. Stop microphone capture before speaking so Parker does not hear itself.

Keep the existing production `Speaker`/macOS `say` path unchanged until the harness proves value. If browser TTS quality is unacceptable, build an interruptible subprocess speaker as a later production slice.

Stop must:

- cancel browser speech immediately;
- abort the client request;
- increment the turn generation;
- mark the server turn cancelled;
- discard any late brain result;
- return the visible state to stopped/idle.

The server may be unable to interrupt a synchronous provider call. That is acceptable for the laptop prototype only if the late result is discarded and cannot speak or overwrite the screen.

### 6. Latency is an observable contract

Record, for every turn:

- UI Start -> listening indicator;
- Done -> audio upload complete;
- ASR duration;
- route/repair duration;
- live information/provider duration;
- response received -> first TTS audio;
- Done -> first audible response;
- Stop click/utterance -> silence;
- end-to-end failure reason.

Initial experiment budgets, to validate rather than claim:

- listening indicator: under 100 ms;
- touch Stop -> silence: under 150 ms;
- warmed local ASR after Done: median under 1 second, p95 under 1.5 seconds;
- deterministic repair/read-back: under 250 ms after ASR;
- non-live conversational answer first audio: median under 3 seconds after Done;
- live weather/sports first audio: median under 5 seconds after Done;
- zero stale answers after Stop in 100 repeated cancellation races.

If a budget fails, report the actual distribution and fix the dominant stage. Do not hide latency with filler speech.

## Proposed API shape

Treat names as provisional; prefer existing router conventions.

```text
GET  /parker/converse
POST /parker/converse/sessions
POST /parker/converse/sessions/{id}/turns
POST /parker/converse/sessions/{id}/stop
GET  /parker/converse/sessions/{id}/state
```

Turn request:

```json
{
  "turn_id": 3,
  "audio_mime": "audio/wav",
  "audio_base64": "...",
  "manual_finish": true
}
```

Turn response:

```json
{
  "turn_id": 3,
  "state": "answer|repair|stopped|error",
  "heard": "...",
  "speech": "...",
  "choices": [],
  "sources": [{"label": "...", "url": "...", "fresh_as_of": "..."}],
  "timings_ms": {
    "decode": 0,
    "asr": 0,
    "route": 0,
    "provider": 0,
    "total_after_done": 0
  }
}
```

Patient-facing responses must not expose local file paths, digests, credentials, internal prompts, or raw provider payloads.

## Sprint plan

### Sprint 0 — Freeze the experiment and baseline latency

Depends on: none

Objective: turn Dad's actual problem into executable traces before UI work.

Likely paths:

- `docs/strategy/2026-08-29-problem-first-value-proposition.md`
- this handoff
- a new deterministic curiosity-loop fixture/test module
- current talk/brain tests

Tasks:

- Define six questions: weather today/tomorrow, one sports result/follow-up, one video-interest question/follow-up.
- Define pause/restart audio/transcript fixtures, wrong-heard repair, Stop during fetch, Stop during speech, provider-down, and stale-response cases.
- Add timing hooks around the existing local talk loop without changing behavior.
- Warm the model once and run at least 20 local transcript/audio turns.
- Produce a baseline latency and correctness receipt.

Acceptance:

- Current behavior and dominant latency are measured.
- Current answer lane is explicitly classified as stub, stale-knowledge, or live-source capable per case.
- No product changes yet.

Verification:

- Existing focused tests remain green.
- Receipt contains exact model/provider/runtime and no private audio.

### Sprint 1 — Browser capture and persistent conversation harness

Depends on: Sprint 0

Objective: Dad can Start, pause freely, press Done, see the transcript, and receive a stub/fake answer through the real `TextSession`.

Likely paths:

- new `backend/app/parker/converse_router.py`
- new `backend/app/parker/converse_ui.py`
- narrow session manager module
- `backend/app/parker/router.py`
- focused API/UI/session tests

Acceptance:

- Manual Start/Done supports long pauses without automatic cutoff.
- One server session preserves `TextSession` history across turns.
- Temporary audio is deleted on success, error, cancellation, and disconnect.
- Stop invalidates the generation and blocks stale UI updates.
- The real safety/repair routing remains authoritative.
- Large controls are keyboard and touch operable; no horizontal overflow at laptop/tablet widths.

Verification:

- Red/green API, lifecycle, long-pause, cancellation, and stale-generation tests.
- Extracted JavaScript syntax check.
- No network required with fake brain/transcriber.

### Sprint 2 — Current-information and follow-up proof

Depends on: Sprint 1 and the Sprint 0 provider decision

Objective: the three Dad-shaped topics return brief current answers with visible sources and one correct follow-up.

Likely paths:

- existing brain adapter modules
- possibly one narrow answer-evidence value type
- provider/gateway adapter tests
- curiosity-loop eval fixtures

Acceptance:

- Weather and sports answers are current and source-backed.
- Follow-up pronouns/time references resolve from the bounded session history.
- Provider failure is brief, honest, and does not break the session.
- Medical, purchase, finance, credential, and emergency boundaries remain pre-model.
- No provider can execute actions from this harness.
- Source labels/freshness appear on screen; URLs are not spoken.

Verification:

- Fake-provider deterministic tests plus a separately gated live smoke.
- Exact answer/freshness/source assertions for scripted cases.
- Existing brain-lane and policy tests remain green.

### Sprint 3 — Immediate Stop and measured responsiveness

Depends on: Sprint 2

Objective: Dad can stop capture, processing display, or speech and never receive a late stale response.

Likely paths:

- converse UI/session manager
- harness-specific browser TTS
- cancellation/race tests

Acceptance:

- Stop cancels speech immediately.
- Stop invalidates in-flight turns; late provider results are dropped.
- A new turn cannot inherit stale answer or repair state from a cancelled generation.
- 100 repeated Stop-vs-response races produce zero stale speech/screen updates.
- Latency report meets budgets or identifies one dominant failing stage.

Verification:

- Deterministic fake-clock/fake-provider race tests.
- Real browser timing smoke on the intended laptop.
- Existing action confirmation and first-session tests stay green.

### Sprint 4 — Dad-ready packaged smoke and handoff

Depends on: Sprints 1–3 green

Objective: the exact laptop/browser path Pras will use is installed, configured, and rehearsed before Dad enters.

Tasks:

- Install/warm local ASR and configure only the selected read-only brain/provider.
- Run TCC microphone allow/deny and recovery.
- Rehearse weather, sports, video question, follow-up, repair, and Stop.
- Verify volume, screen sightline, browser controls, and no self-transcription.
- Reset test history and prepare a one-sentence disclosure.
- Run the three-minute first-use protocol once with Pras as proxy, then stop changing code.

Acceptance:

- All scripted traces pass twice from cold app/browser start.
- No setup, credentials, downloads, or debugging are visible in Dad's session.
- Packaged/device evidence is recorded separately from deterministic tests.
- First-user session has a stop handle and a fallback to ordinary conversation with Pras.

## Release gates

Before Dad use:

- close the currently dirty stale-listening race or explicitly exclude that uncommitted path from the harness;
- focused curiosity, brain, safety, screen, first-session, and audio-lifecycle tests;
- full UTC release-readiness and project test gates;
- fresh read-only review of the exact harness revision;
- real laptop microphone/TCC/browser smoke;
- no raw/private audio in the repository;
- no unsupported claim that Parker beats Google, understands Parkinson's speech, learns Dad's voice, or reduces caregiver burden.

## Explicit non-goals

- General smart-home control
- Purchases, messages, submissions, or account actions
- Emergency response or monitoring
- Medical advice or therapy
- Automatic voice learning or model training
- Continuous room recording
- Full-duplex speech-to-speech in the first sprint
- Mobile app, multi-user platform, or generic agent runtime
- Public launch, content program, or benchmark claim before first-user evidence

## Decisions for Pras

Fable should surface these explicitly rather than guess:

1. For the laptop prototype, is touch Start/Done acceptable as the default? Recommended: yes.
2. Is internet/cloud use acceptable for current weather/sports answers if the screen names the provider/source? Recommended: yes for the opt-in prototype, with local ASR retained.
3. Which brain should the first spike compare: existing OpenClaw gateway versus a narrow current-information provider? Recommended: compare both in Sprint 0, then choose one.
4. Is browser TTS acceptable for the harness? Recommended: yes, because Stop is immediate; keep macOS `say` unchanged.
5. What exact sports teams, leagues, and video topics should be in the six-case fixture set?
6. Should the first Dad session record transcripts locally for product debugging? Recommended: only the existing local transcript/outcome path, with explicit explanation; no raw audio by default.

## Handoff outcome

Fable should return:

- a revised sprint graph with stable task IDs and dependencies;
- exact paths/tests per sprint;
- a provider spike protocol and decision rubric;
- latency measurement commands and receipt format;
- a first-user readiness checklist;
- blockers requiring Pras versus agent-owned work;
- a recommendation on whether this should remain on the current branch or start from a clean worktree after the stale-listening race is resolved.
