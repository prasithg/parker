# Parker Voice Practice — first data loop

Date: 2026-08-23
Status: execution plan

## Goal

Ship one supporting, tablet-like voice-practice experience inside the existing Parker app so a person can complete a sustained-voice exercise at their own pace and Parker can retain a useful, provenance-rich local attempt record—without displacing the repair-first assistant wedge or delaying EXP-001 deployment.

## Done when

- `GET /parker/practice` serves a large, calm, keyboard/screen-reader-operable practice page.
- A user starts and stops each attempt manually; no automatic progression or failure for taking longer to read/respond.
- Three rounds are suggestions: after every saved round the user can continue or Finish for today. Page exit uses beacon plus keepalive fallback to request abandonment for saved, in-flight, or response-ambiguous Saves; sticky browser state keeps that request eligible even if the Save response is lost.
- The page uses the browser microphone to show device-relative feedback and records duration, level summary, target-time fraction, protocol version, prompt, and optional self-rating.
- The user can explicitly choose whether that attempt's short audio sample is retained locally.
- `POST /parker/practice/attempts` validates and persists one retry-idempotent attempt attached to Parker's existing local exercise lifecycle; the server derives target fraction from primitive voiced-frame counts.
- Optional audio is size/type bounded, stored only under `PARKER_HOME`, and represented by a separately scoped local artifact row with explicit local-only purpose/use.
- `GET /parker/practice/attempts` returns recent attempts for progress display.
- The packaged tray pauses Parker's normal talk loop before Voice Practice owns the microphone; page hide, stream loss, and permission failure release the audio graph.
- MediaRecorder finalization has stop/error handling and a bounded fallback in `try/finally`; metrics remain saveable when optional audio cannot finalize.
- The existing Dad Screen remains output-only and existing exercise/caregiver behavior does not change.
- Focused tests and the project test gate pass.

## Context

Parker already has:

- Python/FastAPI, SQLite, and `PARKER_HOME` state routing;
- a Tauri macOS shell that opens local engine pages;
- local exercise-session lifecycle rows;
- a large-type Dad Screen;
- local microphone permissions and app packaging;
- no JavaScript build pipeline or need for a new dependency.

The first slice should reuse those seams rather than creating a React Native/Expo product before daily use is proven.

## Constraints and non-goals

- One exercise only: sustained `ah`, three suggested rounds.
- Device-relative dBFS feedback, not calibrated sound-pressure level and not a clinical measure.
- No treatment, diagnosis, improvement, or efficacy claim.
- No cloud sync, account system, central research database, mobile-store release, clinician portal, exercise marketplace, pitch scoring, or model training in this slice.
- No automatic timer advancement. A target duration is guidance; the user decides when to stop.
- No maximum user-controlled attempt duration or frame-counter ceiling at the API boundary; audio bytes remain independently capped.
- Optional local audio is a data primitive, not permission to publish or centrally ingest it.
- Sustained `ah` is acoustic/adherence data, not evidence that everyday ASR or intent recovery improved.

## Data contract

One `VoicePracticeSession` attaches to a `LocalExerciseSession`. Each `VoicePracticeAttempt` child records:

- identity: `id`, opaque `client_attempt_id`, `practice_session_key`, parent IDs, and `sequence`;
- protocol: `exercise_key`, `protocol_version`, `prompt_text`, `target_seconds`, `source`;
- measurement: duration and dBFS summaries; analyzed, voiced, and in-target frame counts; server-derived `in_target_fraction`; algorithm version; sample rate, channel count, and actual browser processing flags;
- user outcome: optional `self_rating` (`1=comfortable`, `2=okay`, `3=effortful`);
- provenance: payload digest, source, artifact policy, and completion time.

Optional retained audio lives in a separate `VoicePracticeAudioArtifact` row with path, type, size, digest, capture purpose, and `local_personalization_only_v1` allowed use.

The API never treats these metrics as clinical state. Device-relative measurements are comparable primarily within the same device/room setup.

## Tasks

### T1 — Operating direction

Depends on: none

Objective: Record the chairman/AI-CEO governance model and product/data flywheel.

Files:

- `docs/strategy/operating-model.md`
- `docs/strategy/README.md`
- `docs/strategy/roadmap.md`

Acceptance:

- SMF is clearly an operating precedent, not Parker's business model.
- Chairman and AI-CEO decision rights are explicit.
- Repair-first everyday voice access remains the organizational wedge; Voice Practice is a supporting tool/data experiment.

Verification:

- citation ledger verifies every external reference used by the strategy note.

### T2 — Attempt persistence

Depends on: T1

Objective: Add the smallest structured attempt model and local audio writer.

Files:

- `backend/app/exercises/voice_practice.py`
- `backend/app/exercises/session.py` (generic parent lifecycle only)
- `backend/app/paths.py`
- `backend/tests/test_exercises.py`

Acceptance:

- structured attempts persist and list newest-first;
- repeated client retries are idempotent and conflicting ID/sequence reuse fails;
- attempts share and complete the generic parent exercise lifecycle;
- completion and abandonment use atomic compare-and-set terminal transitions for both the practice and generic parent; an abandonment request arriving before Save creates a terminal tombstone so a late Save fails closed, while a response-lost committed Save remains eligible for page-exit cleanup;
- invalid metric ranges are rejected at the API boundary;
- optional audio stays under `PARKER_HOME`, records a digest and size, and cleans up on DB failure;
- unsupported, failed, oversized, or unavailable recording retention is disclosed to the user rather than silently ignored;
- no-audio attempts write no file.

Verification:

- `backend/.venv/bin/pytest backend/tests/test_exercises.py -q`

### T3 — Practice API and patient page

Depends on: T2

Objective: Serve the practice UI and recent-attempt API through the existing Parker router.

Files:

- `backend/app/parker/practice_ui.py`
- `backend/app/parker/practice_router.py`
- `backend/app/parker/router.py` (subrouter inclusion only)
- `backend/tests/test_practice.py`

Acceptance:

- route contracts match the Done criteria;
- page has manual Start/Stop/Next/Finish controls, visible elapsed time/level feedback, an audio-save choice, three suggested rounds, and large accessible controls;
- browser-side payload uses the fixed protocol contract;
- actual media settings and voiced-frame counts cross the boundary; device identifiers and per-frame traces do not;
- history renders from the local API;
- Dad Screen output-only tests remain unchanged.

Verification:

- `backend/.venv/bin/pytest backend/tests/test_practice.py backend/tests/test_screen.py -q`

### T4 — Integration and product evidence

Depends on: T3

Objective: Make the slice discoverable, inspect the real UI, and bind claims to behavior.

Files:

- `docs/strategy/README.md`
- `docs/strategy/roadmap.md`
- `docs/next-slices.md`
- README or desktop docs only if the shipped route changes their current-state truth.

Acceptance:

- live page exercised through a local server with a temporary/home-safe DB;
- desktop/Tauri route compatibility checked;
- no external send, cloud upload, or unrelated action path added;
- independent reviewer returns PASS after final diff/test evidence.

Verification:

- targeted tests;
- `make test`;
- `git diff --check`;
- live browser capture of `/parker/practice` at desktop and narrow/tablet widths.

## Later, only after use earns it

- first evidence-gated bridge: one personalized functional phrase into Parker ASR/repair/action; only then consider pitch glide, DDK, or a broader catalog;
- per-device calibration and day-level baselines;
- native iOS/Android shell;
- explicit contribution/export package with consent terms and provenance manifest;
- transcription, acoustic feature extraction, model/eval ingestion;
- family/SLP-authored protocols;
- community cohort studies and central research infrastructure.
