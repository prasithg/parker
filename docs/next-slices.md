# Parker — next implementation slices

Written 2026-06-09, after the architecture/eval reconciliation pass; status updated later the same day. Each slice is one focused session: small diff, tests included, no broad rewrite. Order matters — earlier slices de-risk later ones.

## Slice 1: Route classifier seam + task-taxonomy evaluator — DONE (2026-06-09)

Shipped: `benchmark/evaluate_tasks_v0.py` (CLI evaluator + deterministic rule-based baseline), `backend/tests/test_task_evaluator.py`, `make eval-tasks`, reports under `benchmark/reports/`. Metrics: route accuracy, action-type accuracy, escalation precision/recall, refusal recall, clarify recall, repair-choice coverage; safety-critical misses listed case-by-case. Baseline: 80% route accuracy, 0 unsafe misses (over-clarifies on disfluent-but-clear requests, by design).

## Slice 2: Repair-choice generation for unclear speech — DONE (2026-06-09)

Shipped: `backend/app/conversation/repair.py` (deterministic `build_repair_prompt`: 2–3 candidates + auto-appended "none of these", label/count validation, policy-taxonomy typing, prohibited types rejected, frozen value objects with no pipeline access), plus the `offer_repair_choices` conversation tool in `backend/app/conversation/tools.py`. The tool is conversation-level only: it validates model-proposed (label, action_type) candidates in code, returns a spoken prompt and typed choices, and rejects unsafe sets with a recoverable `status: rejected`. A picked choice flows into `capture_intent` and stays gated by the pipeline. Tests: `backend/tests/test_repair.py` (module + tool + offer→pick→capture flow + clarify fixtures).

Deferred: grading repair-choice *content* quality (concreteness/plausibility) needs a model- or human-graded pass; the v0 evaluator checks structure only.

## Slice 3: Non-response → escalation candidate — DONE (2026-06-09)

Shipped: `backend/app/escalation/candidates.py` (`flag_non_response_candidates`), wired into `POST /parker/tick` (response now includes `escalation_candidates`). A staged action with `resurface_count >= parker_non_response_resurface_threshold` (default 3), last resurfaced ≥ `parker_non_response_quiet_minutes` ago (default 30), still unconfirmed, becomes an open escalation **candidate** exactly once (`StagedAction.escalation_id` dedup column). Tests: `backend/tests/test_escalation_candidates.py`.

Deliberate deviation from the original sketch: candidates are severity `info`, not `warning` — `auto_escalate_check` promotes open warnings to `urgent` *and dispatches notifications*, which would violate candidate-only semantics if that check ever gets scheduled. `info` is never auto-promoted and no notifications are dispatched at creation (`notified_contacts` stays `[]`). Candidates surface through the existing `/escalations` review flow and next-call context.

Deferred: a caregiver acknowledgment step that *upgrades* a candidate to a dispatched warning/urgent escalation. Note: the `escalation_id` column requires recreating any pre-existing local SQLite DB (`create_tables()` does not ALTER).

## Slice 4: Family message action type, end to end behind confirmation — DONE (2026-06-09)

Shipped: `capture_intent(requested_action="message", recipient=...)` resolves to `family_message`, stages, resurfaces with recipient + drafted message text (`GET /parker/resurface` now returns `recipient`/`message_text` so confirmation restates exactly what will happen), and after recorded confirmation "executes" by writing a `queued_local` row to the new `outbox_messages` table. New endpoints: `GET /parker/outbox`, `POST /parker/outbox/{id}/cancel` (the reversibility story). The v0 codebase contains **no send path at all** — stronger than the original "config flag defaulting off" sketch. Policy invariant updated deliberately: executable surface is now `{reminder, family_message}` because both execution artifacts are local and reversible (`test_v0_execution_surface_is_reminders_and_local_outbox_messages` documents the rationale). Tests: `backend/tests/test_outbox.py` (pipeline + tool + API end-to-end + 404).

Schema note: adds `CapturedIntent.recipient` and the `outbox_messages` table — pre-existing local SQLite DBs need recreating (`create_tables()` does not ALTER).

Deferred to a future, explicitly approved slice: an actual sender (per-message human approval + config flag + contact resolution via `notifier.get_family_contacts`).

## Slice 5: Stale-naming cleanup pass — MOSTLY DONE (2026-06-10)

Shipped: prompt identity is now Parker (`BASE_IDENTITY` rewritten around effortful speech, repair choices, confirm-before-acting); cloned-voice framing only appears when **both** `VOICE_CLONE_CONSENTED=true` and a voice ID are configured, and instructs the agent to never claim to be the family member. App title/description, `app/__init__` docstring, escalation notification prefix, and the tools logger renamed to Parker. Prompt tests cover the consent gate.

Stale-naming cleanup completed in slice 9 (see below): logger names in `calls/`, `voice/`, `meds/`, `conversation/agent.py`, DB filename, and `db/models.py` docstring all renamed to Parker.

## Post-milestone slice (2026-06-10): pilot-readiness — reset path, caregiver review UI, text loop

Shipped toward the family-pilot blocker list:

- **`make reset-db`** — deterministic local reset (removes both historical DB locations; tables recreate via `create_tables()`); `make run` now runs from `backend/` so the server and seeding/REPL commands share one DB file (previously they silently used two different SQLite files).
- **Caregiver review surface** — `GET /parker/review` aggregates everything awaiting a human decision (pending staged/confirmed actions, `queued_local` outbox, non-response candidates, other open escalations); `GET /parker/review/ui` serves a single-file local HTML page with confirm/execute/cancel/acknowledge/resolve buttons over existing endpoints. New caregiver control: `POST /parker/actions/{id}/cancel` (`cancel_staged_action` in the pipeline). Confirm/execute/cancel now return typed 404s instead of 500s for missing ids (closes a review-prep note). Tests: `backend/tests/test_review.py`.
- **Text loop** — `make repl` (`backend/app/conversation/textloop.py`): a deterministic transcript-capture seam routing typed utterances through the real tool layer (`offer_repair_choices`, `capture_intent`), with refusal/human-approval guards mirroring the policy. No model, no audio, no confirmation/execution from the loop itself. Tests: `backend/tests/test_textloop.py`.

Deferred: real microphone/ASR input (the seam is `TextSession.handle(text)` — an ASR transcript drops straight in); dashboard auth (page is localhost-only v0); model-driven candidate generation for repair choices.

## Post-milestone slice (2026-06-10, second): one-command demo — seed + transcript replay

Shipped: `make demo` = `reset-db` → `app/demo/seed.py` (a believable family day driven through the *real* capture→resolve→stage→confirm→execute functions: 3 actions awaiting confirmation incl. a drafted message, 1 outbox-queued message, 1 non-response candidate, 2 executed history items; double-seed guarded) → `app/demo/replay.py` (synthetic effortful-speech transcript through `TextSession`: repair choices + selection, med-change refusal, purchase→human approval) → tick. Tests assert the seeded state through `GET /parker/review` — the surface a caregiver actually sees. Benchmark README/card titles reframed to Parker. Tests: `backend/tests/test_demo.py`.

The replay script doubles as the ASR drop-in point: a real transcript replaces `DEMO_SCRIPT` line-for-line.

## Post-milestone slice (2026-06-10, third): outbox approval gate + review polish

Shipped: `approved_local` outbox state with `approve_outbox_message` / `POST /parker/outbox/{id}/approve` — the second human gate (patient confirms → caregiver approves), recorded with who/when, cancellable from either live state, no resurrection of cancelled messages via approve. `GET /parker/review` now returns separate `outbox_queued` / `outbox_approved` buckets. Review page: Approve button, approved section ("still local only"), status-colored badges, live section counts, last-updated stamp. A future sender must only ever consider `approved_local` rows behind an explicit config flag (architecture protocol rule 7). Schema note: new `approved_at`/`approved_by` columns → `make reset-db`.

## Post-milestone slice (2026-06-10, fourth): local voice input onto the transcript seam

Shipped: `backend/app/voice/transcribe.py` (`transcribe_audio` — file → stripped utterance lines; faster-whisper lazily imported, injectable `Transcriber` for tests, missing-dep `RuntimeError` points at `make voice-deps`) and `backend/app/demo/voice.py` (`run_voice_demo` — transcript lines into the existing `replay_transcript`/`TextSession` seam, then tick). Make targets: `make voice-deps` (optional `backend/requirements-voice.txt`, kept out of core requirements) and `make demo-voice AUDIO=path.wav`. Transcription is fully on-device (CTranslate2 Whisper, CPU int8); the only network touch is the one-time model-weight download to the local HF cache. The audio file is only read — never copied, re-encoded, or stored; transcripts are the only artifact, and a test pins that. Verified end-to-end on a macOS `say`-synthesized wav: transcribed locally, captured as a reminder, staged. Tests: `backend/tests/test_voice_transcribe.py` (fake transcriber; no audio deps in the suite).

Deferred: splitting long Whisper segments on sentence boundaries (pause-free synthetic audio merges sentences into one utterance; real effortful speech segments naturally); live microphone capture; dashboard auth for `/parker/review*` (done in the next slice).

## Post-milestone slice (2026-06-10, fifth): opt-in auth on the caregiver decision surface

Shipped: `backend/app/parker/auth.py` (`require_dashboard_auth` — HTTP Basic over the existing `dashboard_username`/`dashboard_password` settings, constant-time comparison, 401 + `WWW-Authenticate: Basic` challenge), applied as a route dependency to the whole caregiver decision surface: `/parker/review`, `/parker/review/ui`, `/parker/outbox`, action confirm/execute/cancel, outbox approve/cancel. Opt-in by design: empty password (default) keeps every route open so `make demo` and the runbook curl flows stay zero-config; setting `DASHBOARD_PASSWORD` locks the surface. Verified against a live server via env vars, not just TestClient. Scope decision: wider than the original "/parker/review* only" sketch because leaving the mutation endpoints open while locking the read view would be backwards; `/parker/tick` and `/parker/resurface` stay open as the assistant-loop surface. Tests: `backend/tests/test_review_auth.py`.

Deferred: a machine credential for the assistant loop (tick/resurface) when it leaves localhost; HTTPS/reverse-proxy guidance (Basic over plain HTTP is LAN-pilot grade, not internet-grade).

## Post-milestone slice (2026-06-10, sixth): utterance splitting in the voice path

Shipped: `split_utterances` in `backend/app/voice/transcribe.py`, applied inside `transcribe_audio` so its contract is now genuinely "one line per utterance". Two boundary rules: sentence punctuation (`./!/?` + whitespace) and comma-joined capture commands (`, [and] tell|remind|message|send …` — the exact merge Whisper produces for pause-free speech, observed verbatim from the synthesized wav). The critical non-rule: ellipsis disfluencies are never split — "Call... the... you know..." is the text loop's repair-choice cue and must arrive intact (regression-pinned). Verified on real audio: the same wav that previously captured one garbled reminder now stages a reminder + a drafted message to Sarah. Tests: 7 new cases in `backend/tests/test_voice_transcribe.py`.

Deferred: smarter clause splitting (e.g. "and"-joined commands without a comma); live microphone capture (done in the next slice).

## Post-milestone slice (2026-06-10, seventh): live microphone capture — `make talk`

Shipped: `backend/app/voice/record.py` (`load_local_recorder` — sounddevice/PortAudio, default input device, 16kHz mono int16 wav; lazy import with the `make voice-deps` hint; injectable `Recorder` for tests) and `backend/app/demo/talk.py` (`run_talk` — record into a `TemporaryDirectory`, transcribe through the existing `transcribe_audio` → `split_utterances` path, replay through `replay_transcript`, then tick from the CLI). `make talk SECONDS=n` (default 6). The no-audio-retention invariant now covers recordings: the temp wav is deleted unconditionally — success *and* transcriber-failure paths are regression-pinned. `sounddevice` added to the optional `backend/requirements-voice.txt`; the core suite injects fakes. Tests: `backend/tests/test_talk.py`.

Deferred: push-to-talk / voice-activity end-pointing instead of a fixed window; a continuous listen loop (done in next slice); in-memory transcription (skip the temp file entirely by passing the array straight to faster-whisper).

## Post-milestone slice (2026-06-10, eighth): continuous talk loop — `make talk-loop`

Shipped: `run_talk_loop` in `backend/app/demo/talk.py` — one `TextSession` and one `CallLog` live for the whole conversation, so `_pending_choices` state carries across recording windows: a repair-choice offered in turn 1 is correctly selected by "1" in turn 2. Per-turn: record → transcribe → feed each utterance line to `session.handle()` → per-turn tick (intents stage promptly). Silence (empty transcript) prints a cue and continues without resetting session state. Exits cleanly on `KeyboardInterrupt`; returns all exchanges collected so far. `backend/app/demo/talk_loop.py` is the thin CLI for `make talk-loop SECONDS=n`. Tests: `backend/tests/test_talk_loop.py` — single turn, repair-choice spanning turns, multi-turn capture, silence-then-selection (silence must not reset `_pending_choices`), refusal, single-CallLog invariant, `KeyboardInterrupt` mid-loop, and recording-deletion across turns.

Deferred: push-to-talk / voice-activity end-pointing; in-memory transcription.

## Post-milestone slice (2026-06-10, ninth): stale-naming cleanup — Parker throughout

Shipped: all remaining `parkinsclaw` identifiers renamed to `parker` across the codebase.
- `config.py` default `database_url`: `parkinsclaw.db` → `parker.db`
- `Makefile` reset-db: removes both `parker.db` and `parkinsclaw.db` so upgrades are smooth
- `db/models.py` module docstring: "ParkinsClaw" → "Parker"
- Loggers: `parkinsclaw.calls` → `parker.calls`, `parkinsclaw.scheduler` → `parker.scheduler`, `parkinsclaw.voice.stream` → `parker.voice.stream`, `parkinsclaw.voice.clone` → `parker.voice.clone`, `parkinsclaw.meds` → `parker.meds`, `parkinsclaw.agent` → `parker.agent`
- `docs/runbook.md`: `parkinsclaw.db` → `parker.db`

No schema change — existing local DBs named `parkinsclaw.db` are unaffected until `make reset-db`. `make reset-db` now cleans up both names so both old and new installs start clean.

Deferred: nothing. The project now reads as Parker end to end.
