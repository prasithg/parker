# Patient Curiosity Loop — execution plan (Fable, 2026-08-29)

Executes the sprint direction in
`docs/plans/2026-08-29-fable-handoff-low-latency-curiosity-harness.md` against
`docs/strategy/2026-08-29-problem-first-value-proposition.md`, revised where
the repository contradicted the handoff. Pras authorized autonomous execution
and merge-on-green for this session; decisions listed at the end were made
under the blocker policy (adopt the recommendation, record it, keep moving).

Goal: a laptop/browser harness at `GET /parker/converse` where Dad can tap
Start, take his time, tap Done, see what Parker heard, repair one unclear
part, get a brief current answer with visible sources, ask one follow-up
without restating the topic, and stop Parker instantly — all through the real
`TextSession` brainstem.

## Revisions to the codex handoff (repo-grounded)

1. **Provider decision made, not spiked.** The handoff wanted an A/B spike:
   OpenClaw gateway vs a narrow current-information provider. No OpenClaw
   gateway is configured on this machine, gateway replies expose no structured
   source list today, and a direct keyless API call strictly beats a full
   agent loop on latency. Decision: build the narrow provider
   (`CuriosityBrain`) as a `BrainAdapter` wrapper — weather via Open-Meteo
   and scores via the ESPN public scoreboard, both keyless — delegating
   everything else to the configured inner brain (Claude here; OpenClaw when
   a family configures one). The live A/B against a real gateway stays a
   follow-up for when one exists.
2. **Audio format decided: browser-encoded 16 kHz mono WAV.** The entire
   local lane (recorder, transcriber, practice router) already speaks WAV;
   MediaRecorder WebM would add a server-side decode dependency for zero
   benefit. The page captures PCM via WebAudio, downsamples to 16 kHz, and
   posts base64 WAV — same shape as the shipped Voice Practice lane,
   same byte caps, same delete-in-`finally` contract.
3. **Sprint 0's VAD-loop latency baseline folded into the harness smoke.**
   The harness replaces VAD end-pointing with manual Done, so the old loop's
   pause-latency numbers decide nothing. Existing exchanges already carry
   asr/route timings; the receipt that matters is measured on the real
   converse path (say-generated WAV through warmed whisper-base on this
   laptop).
4. **Fix shipped red first**: `test_functional_phrase_bridge.py`'s
   unavailable-ASR case simulated unavailability by relying on faster-whisper
   being uninstalled — on a voice-deps machine it attempts a real model load
   inside a unit test and fails 422≠503. Fix the simulation (a transcriber
   that raises `RuntimeError`, the documented unavailable signal) and memoize
   the practice lane's lazily-loaded transcriber so an attempt does not
   reload the model per request.
5. **The stale-listening desktop fix is excluded, not closed.** The dirty
   two-file fix lives in the Hermes worktree
   (`desktop/src-tauri/src/lib.rs` + runbook), is owned by that session, and
   the browser harness never touches the desktop shell. Release gate
   satisfied by exclusion; noted in the handoff section.

## Architecture (final)

```text
Browser page /parker/converse
  Start → WebAudio capture (no auto-cutoff; Dad's own Done ends it)
  Done  → POST turn {audio_base64 wav | text} ────────────┐
  Stop  → speechSynthesis.cancel() + abort fetch + POST stop
  choices/yes-no → tap posts the same words a voice reply would
                                                          ▼
ConverseStore (server, per-session)                TextSession (unchanged core)
  id → {TextSession, db session, generation,        guards → capture/repair →
        turn lock, last_active}                     answer lane → brain
  one warmed shared transcriber                            │
  stop bumps generation; a finishing stale turn            ▼
  is discarded + transient prompts dismissed       CuriosityBrain(inner)
                                                     weather → Open-Meteo
                                                     scores  → ESPN scoreboard
                                                     else    → inner brain/stub
                                                     returns speech + sources
                                                             + freshness
```

- New public seam `TextSession.dismiss_transient_state()` so a cancelled
  generation cannot leak pending choices/confirmation into the next turn.
  Staged actions keep the defer semantics (stay visible for review).
- `BrainReply` gains `sources: tuple[Source, ...]` (label, url, fresh_as_of).
  The guard passes sources through untouched on clean replies and drops them
  with everything else on a medical trip. Sources render on screen; they are
  never spoken.
- The converse turn runs the same per-turn tick + `offer_pending_confirmation`
  as the talk loop: action requests through the harness stay
  confirmation-gated, by voice or tap.
- Touch Start is addressing Parker, so turns route with
  `UtteranceContext(addressed_to_parker=True, source="touch_start")` —
  wake gating stays a talk-loop concern.

## Slices

- S0 — fix the red functional-phrase case + practice transcriber memoization.
- S1 — answer-evidence contract: `Source`, `BrainReply.sources`, guard
  passthrough, `_answer` surfaces sources in the response dict.
- S2 — `CuriosityBrain`: weather (place from utterance or
  `PARKER_HOME_PLACE`), scores (`PARKER_SPORTS_LEAGUES`), follow-up state
  ("what about tomorrow?", "when do they play next?"), honest failure
  speech, injectable fetcher; suite never touches the network.
- S3 — `ConverseStore` + turn/stop lifecycle: serialization, temp-audio
  delete on every path, generation/stale discard, expiry sweep,
  100× stop-vs-response race test with a slow fake brain.
- S4 — router + page: sessions/turns/stop/state endpoints, big-control UI,
  browser TTS with immediate cancel, choice/yes-no taps, dev timing panel,
  extracted-JS `node --check`.
- S5 — latency receipts: per-stage server timings in every turn response,
  client marks posted to a receipts endpoint, JSONL under `backend/receipts/`
  (gitignored), aggregate-only report script.
- S6 — deterministic curiosity eval: the six Dad-shaped cases + failure cases
  through the real session with a fake fetcher;
  `make eval-curiosity-loop`; evaluator test.
- S7 — laptop smoke: uvicorn + browser drive of the real page, say-generated
  WAV turns through real whisper-base, one gated live provider call each
  (skips cleanly offline), receipt written.
- S8 — docs: strategy/handoff docs into the repo, runbook first-session
  section, README, next-slices.
- S9 — gates (full suite local tz + `TZ=UTC make eval-release-readiness`),
  fresh-context verifier subagents on the real artifact, merge to main, push,
  GitHub project update.

## Latency budgets (validate, not claim)

Listening indicator <100 ms; touch Stop → silence <150 ms; warmed ASR after
Done median <1 s / p95 <1.5 s; live answer first audio median <5 s after
Done; zero stale answers after Stop across 100 races. Failures report the
distribution and the dominant stage.

## Decisions adopted under the blocker policy

1. Touch Start/Done is the default first-user mode — adopted as recommended.
2. Internet for current weather/scores with on-screen source naming —
   adopted; local ASR retained; both providers keyless.
3. Provider: narrow current-information provider now, gateway A/B later —
   reasoned above.
4. Browser TTS for the harness; macOS `say` path untouched — adopted.
5. Exact teams/leagues/video topics: **still Pras's.** The provider is
   config-driven (`PARKER_SPORTS_LEAGUES`, team matched from the utterance),
   fixtures use league-generic examples.
6. First Dad session records only the existing local transcript/outcome
   artifacts; no raw audio — adopted.
7. Home place for bare weather questions: `PARKER_HOME_PLACE`, empty default
   asks one bounded question instead of guessing.

## Explicitly out of scope (unchanged from the handoff)

Streaming ASR, realtime speech models, desktop-shell changes, new action
types, smart-home control, purchases/messages beyond the shipped gated lanes,
any claim that Parker beats Google or understands Parkinson's speech.
