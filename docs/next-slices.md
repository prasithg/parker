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

Deferred: smarter clause splitting (e.g. "and"-joined commands without a comma — done in slice 10); live microphone capture (done in the next slice).

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

## Post-milestone slice (2026-06-10, tenth): and-joined command splitting + Makefile print fix

Shipped: extended `_COMMAND_BOUNDARY` in `backend/app/voice/transcribe.py` to also split on bare `\s+and\s+(capture-verb)` — no comma required. Previously only the comma form (", tell Sarah") was handled; now "Remind me to stretch and tell Sarah hi" correctly produces two utterances. Non-capture uses of "and" (list items like "apples and oranges") are unchanged — the capture-verb lookahead is the discriminator. Also fixed the Makefile `reset-db` print message which still said `parkinsclaw.db` after the slice-9 rename. Tests: 4 new cases in `backend/tests/test_voice_transcribe.py` (bare-and split, multiple bare-and, no-split for list items, and-remind boundary).

Deferred: nothing voice-splitting–related remains. Next open item: model-driven repair-choice candidate generation.

## Post-milestone slice (2026-06-10, eleventh): model-driven repair-choice generation

Shipped: `suggest_repair_candidates(utterance, *, client, model)` added to `backend/app/conversation/repair.py`. Makes a `claude-haiku-4-5-20251001` call (fast, cheap) with a tight system prompt that asks for exactly 2 JSON candidates, specific to the utterance, using only the policy-safe action types `reminder` and `family_message`. Never raises: any error (missing key, network, malformed JSON, unsafe action type, over-length label) falls back to the hardcoded `["set a reminder about this", "send a family message about this"]` candidates without interrupting the conversation. `TextSession.__init__` gains an optional `model_client` keyword arg (injectable `anthropic.Anthropic` instance; `None` → fallback). `anthropic>=0.50.0` added to `backend/requirements.txt` and `anthropic_api_key: str = ""` added to `Settings`.

Previously ambiguous utterances always got the same generic "set a reminder / send a family message" options. With an API key set, Parker now offers choices grounded in what was actually said: "Call... the... you know... the one with the garden..." → "remind you to call your neighbour" / "message your neighbour". Fallback keeps the zero-config demo fully functional with no key.

Tests: `backend/tests/test_repair_suggest.py` — model candidates returned, no-key fallback, API-error fallback, malformed-JSON fallback, wrong-count fallback, unsafe-action-type fallback, over-length fallback, utterance present in prompt, TextSession wired end-to-end, fallback-only session still produces valid choices, model→selection→capture round-trip.

Deferred: more than 2 candidates (current prompt locks to 2 for simplicity); multi-turn repair (feeding the conversation history to the model for better grounding).

## Post-milestone slice (2026-06-10, twelfth): auto-wire model client + eval-repair

Shipped: `_build_model_client()` in `backend/app/conversation/textloop.py` — reads `settings.anthropic_api_key`, instantiates `anthropic.Anthropic` when set, returns `None` otherwise (no import, no error). Called in both interactive entry points: `textloop.main()` (`make repl`) and `talk.run_talk_loop()` (`make talk-loop`). `replay_transcript` / `make demo` intentionally left without a client — the demo uses a scripted fixture and must work zero-config. `_build_model_client` is re-exported from `talk.py` for the loop; `talk_loop.py` CLI picks it up transitively.

Previously, setting `ANTHROPIC_API_KEY` had no effect on the interactive tools — `TextSession` always got `None`. Now `make repl` and `make talk-loop` automatically use model-driven candidates when the key is present. Tests: 3 new cases in `test_repair_suggest.py` (no-key returns None, key instantiates client, import-error returns None).

Also shipped: `benchmark/evaluate_repair_v0.py` + `make eval-repair` — 8 effortful-speech fixtures through the real model, prints candidates + action_type hint-match for human review, optionally writes a JSON report. Skips gracefully when `ANTHROPIC_API_KEY` is unset. Not part of the core test suite (quality is subjective; pilot family grades it).

Deferred: feeding conversation history to the model for better grounding; more than 2 candidates.

## Post-milestone slice (2026-06-10, thirteenth): pilot setup docs + .env.example rewrite

Shipped: `backend/.env.example` rewritten from the stale "ParkinsClaw" stub into a well-annotated pilot guide — every setting grouped, marked [REQUIRED FOR X] or [NOT USED], `ANTHROPIC_API_KEY` section explains the repair-choice upgrade, `DASHBOARD_PASSWORD` section explains the LAN pilot recommendation, legacy settings (Twilio, OpenAI, ElevenLabs) explicitly labelled as unused in v0. `docs/runbook.md` updated: stale header fixed, new "Pilot setup: what to configure" section added (env file copy, model key, dashboard password, voice deps), `make eval-repair` added to Demo 5, "what this demo cannot show" extended with the generic-repair-without-key note.

No code changes. This closes the last gap between a working local demo and a real pilot hand-off: a family member can now pick up the repo, read the runbook, and know exactly what to configure.

Deferred: a `SETUP.md` or `README.md` at the repo root (currently no top-level readme); multi-turn repair grounding.

## Post-milestone slice (2026-06-10, fourteenth): README refresh — scaffold to pilot-ready

Shipped: the stale bottom half of `README.md` brought up to v0 reality; the vision sections (pitch, thesis, why-it-matters, what-Parker-should-do, safety boundaries, eval agenda) were already accurate and are untouched. Changes: "Current repo state" no longer says *scaffold* — it now lists the shipped input ladder, pipeline, repair choices with model opt-in, two-gate outbox with no send path, review UI with opt-in auth, escalation candidates, eval harness, and the then-current 214-test count. Stack table split into "v0 (shipped)" vs "possible later" — Twilio/OpenAI Realtime moved out of the current column (no send/call path exists in v0). Setup section now leads with the three-command demo (`make demo` → `make run` → review UI) and the talk-loop voice path, and points at the runbook's pilot-setup section and `.env.example`. The completed "Near-term Fable 5 task" section (architecture reconciliation — done a dozen slices ago) replaced with "Where to start reading" (runbook, next-slices, AGENTS/CLAUDE). Naming section updated: ParkinsClaw is fully retired from code as of slice 9.

No code changes; the then-current 214 tests and evals were unchanged.

## Night4 live-demo QA docs refresh — DONE (2026-06-17)

Shipped: README test-count references refreshed to the verified 225-test suite, and `docs/runbook.md` now matches the actual `make demo` end state after seed + transcript replay: six pending actions, one queued local outbox message, one non-response candidate, two recent-history rows, and one cancellation audit row. This was a demo/runbook fix only; the existing behavior was already pinned by `backend/tests/test_demo.py::test_seed_and_replay_compose_for_the_full_demo`.

Verification: targeted review/demo tests passed, full `make test` passed with 225 tests, `make eval-tasks` reported 0 safety-critical misses, `make eval-repair` skipped cleanly without `ANTHROPIC_API_KEY`, and the live local review UI at `http://127.0.0.1:8000/parker/review/ui` returned HTTP 200 with the expected review-feed counts.

Deferred after the README refresh slice: nothing README-related. Open quality items at that point: voice-activity end-pointing, multi-turn repair grounding, human-graded repair-content evals.

## Post-milestone slice (2026-06-10, fifteenth): multi-turn repair grounding

Shipped: `suggest_repair_candidates` gains an optional `prior_choices: list[str] | None` parameter. When provided, the previously rejected labels are injected into the user message (`_SUGGEST_USER_WITH_HISTORY`) so the model generates genuinely different alternatives instead of repeating itself. `TextSession` now tracks `_prior_offered_labels`: set to the candidate labels when the user picks "none of these", passed to `_offer_choices` on the next vague utterance, and cleared to `None` after any successful capture so the history does not leak into unrelated conversations.

Before this slice: "none of these" → vague follow-up → model would often regenerate the exact same two choices. Now the model receives the rejected labels as grounding context and produces different, more specific alternatives.

Tests: 4 new cases in `test_repair_suggest.py` — prior labels appear in the prompt; plain user message when no prior history; full TextSession round-trip (vague → none-of-these → vague again verifies rejected labels reach the model call); prior labels cleared after successful capture (no leakage into subsequent fresh offers). Total: 218 tests.

## Post-milestone slice (2026-06-10, sixteenth): review-page history — "Recently done"

Shipped: `GET /parker/review` gains `recent_history` — the last 10 (`RECENT_HISTORY_LIMIT`) executed actions, newest first (`executed_at` desc, id desc tiebreak); `_serialize_action` now includes `executed_at`. The review page gets a read-only "Recently done (stayed on this machine)" section at the bottom: reminders show their subject, messages show recipient + text + "queued to local outbox", each with done-time, who confirmed, and the execution result. No buttons — it's the trust surface ("what did Parker actually do"), not a decision surface. Pending/cancelled actions are excluded. Verified live against the seeded demo: both seeded executed items (pharmacy reminder, message to Sarah) render newest-first.

Tests: 4 new cases in `backend/tests/test_review.py` — newest-first ordering with `executed_at` serialization, pending/cancelled exclusion, cap at the limit (newest kept), section present in the HTML page.

Deferred: pagination/full history view beyond the last 10; including cancelled actions as a separate "changed my mind" audit list.

## Post-milestone slice (2026-06-10, seventeenth): "Changed my mind" — cancelled-items audit list

Shipped: cancellations no longer vanish from the review page. `GET /parker/review` gains two read-only buckets, both capped at `RECENT_HISTORY_LIMIT`: `recent_cancelled` (cancelled staged actions, id-desc — there is deliberately no `cancelled_at` column in v0; who/when lives in the `execution_result` text, which the card shows verbatim) and `outbox_cancelled` (cancelled outbox messages, `cancelled_at` desc). The page renders both under one "Changed my mind (cancelled)" section with a combined count — no buttons, pure audit. Verified live on the seeded demo: a cancelled pending reminder and a cancelled queued message to Sarah both appear instead of disappearing.

Tests: 3 new cases in `backend/tests/test_review.py` — cancelled actions newest-first with canceller visible and excluded from pending/history; cancelled outbox message moves from `outbox_queued` to `outbox_cancelled` with its timestamp; section present in the HTML page.

Deferred: a real `cancelled_at`/`cancelled_by` column pair on `StagedAction` if audit ordering ever needs to be time-true across restarts (schema change → reset-db).

## Post-milestone slice (2026-06-10, eighteenth): time-true cancellation columns + seeded audit item

Shipped: `StagedAction` gains `cancelled_at`/`cancelled_by` columns; `cancel_staged_action` records both (the human-readable `execution_result` text is kept for back-compat display), `_serialize_action` exposes them, and the "Changed my mind" audit ordering switched from the id-desc proxy to `cancelled_at` desc — the review test now proves time ordering beats insertion order. The cancelled card's meta line shows "cancelled <when> by <who>" from the structured fields. The demo seed adds a sixth scenario item: a bridge-night card-table reminder the patient cancelled, so the "Changed my mind" section is populated out of the box (`make demo` summary now reports it; deterministic-summary test updated).

Schema note: two new nullable columns on `staged_actions` → pre-existing local DBs need `make reset-db` (`create_tables()` does not ALTER); `make demo` resets anyway.

Tests: review audit test rewritten time-true (later cancellation listed first despite earlier id) + `cancelled_at`/`cancelled_by` serialization; demo tests assert the seeded cancelled item and the new summary key. 225 total.

Deferred: nothing from the original slice menu remains.

## Night4 parallel cleanup stream X1 — review UI safety contract

Shipped: the caregiver review page now begins with a visible **Demo safety contract**. It makes the demo/research trust boundary impossible to miss before a caregiver clicks anything: patient confirmation plus caregiver approval still stays local; v0 has no outbound send path; medical advice, medication changes, purchases, and emergency-service replacement remain out of scope; non-response escalation items are review-only candidates with no dispatched notifications. `docs/runbook.md` now calls out that banner in the fastest-path demo instructions.

Tests: strict TDD on `backend/tests/test_review.py::test_review_ui_surfaces_demo_safety_contract` (red first, then green), targeted review-page tests, full 226-test backend suite, and `make eval-tasks`.

## Night4 Lane D + parallel freshness pass — interactivity eval made grant-visible

Shipped: `benchmark/data/parker_interactivity_v0.json`, `benchmark/interactivity_v0.py`, `benchmark/evaluate_interactivity_v0.py`, `backend/tests/test_interactivity_evaluator.py`, `make eval-interactivity`, and JSON/markdown reports under `benchmark/reports/`. The eval scores synthetic multi-turn traces for repair under uncertain speech, changed-mind cancellation, confirmation-before-action, caregiver UI clarity, latency/turn budget, and unsafe-action suppression. Safety-critical misses for confirmation gates and unsafe-action suppression are reported separately from ordinary latency/UI failures.

This follow-up freshness pass made the new eval visible in the repo README and grant packet instead of leaving Pras with stale `226 tests` / pre-Lane-D evidence. Verification: `make test` passed with 232 tests and 2 warnings; `make eval-tasks` still reports 20 synthetic fixtures with 0 safety-critical misses; `make eval-interactivity` reports 6 scenarios, 100% reference-trace pass rate, and 0 unsafe misses.

Deferred: wire live Parker demo/replay traces into the interactivity prediction schema so the grant packet can show Parker-generated trace scores, not only reference synthetic trace scores.

## Night4 demo-generated interactivity trace — current-product eval made honest

Shipped: `benchmark/demo_interactivity_predictions_v0.py`, `backend/tests/test_demo_interactivity_predictions.py`, `make eval-demo-interactivity`, and demo-specific JSON/markdown reports under `benchmark/reports/`. The new generator builds evaluator-compatible predictions from Parker's actual local surfaces: the repair-choice tool, `TextSession`, capture/resolve/stage/confirm/execute pipeline, demo seed, and caregiver review feed. This closes the prior Night4 deferral: the grant packet now has a Parker-generated trace, not only a perfect reference trace.

## Night4 changed-mind cancellation pass — demo trace now green

Shipped: conversational changed-mind revision in `TextSession`, with a TDD regression test in `backend/tests/test_textloop.py` and updated Parker-generated interactivity predictions in `benchmark/demo_interactivity_predictions_v0.py`. When the user interrupts a staged local draft (for example, "Wait, no, after lunch instead"), Parker now cancels the prior staged action with `cancel_staged_action(..., cancelled_by="patient")`, captures the revised reminder, and still leaves execution behind the normal confirmation path. No external send path is added or touched.

Verification: strict TDD red first on `test_changed_mind_interruption_cancels_staged_draft_and_captures_revised_reminder`, the medication-change regression guard, and the demo trace score expectation, then green. `make test` reports 239 passed / 2 warnings. `make eval-demo-interactivity` now reports 6 scenarios, 100% Parker-generated current-product pass rate, 0 unsafe misses, and 0 other failures. `make eval-tasks` remains at 20 fixtures with 0 safety-critical misses; `make eval-degraded-input-replay` still reports Parker repair 100% vs non-interactive 0% on the 3 synthetic held-out transcript fixtures.
