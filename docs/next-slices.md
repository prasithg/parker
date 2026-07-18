# Parker — next implementation slices

Written 2026-06-09, after the architecture/eval reconciliation pass; status updated later the same day. Each slice is one focused session: small diff, tests included, no broad rewrite. Order matters — earlier slices de-risk later ones.

## Slice 1: Route classifier seam + task-taxonomy evaluator — DONE (2026-06-09)

Shipped: `benchmark/evaluate_tasks_v0.py` (CLI evaluator + deterministic rule-based baseline), `backend/tests/test_task_evaluator.py`, `make eval-tasks`, reports under `benchmark/reports/`. Metrics: route accuracy, action-type accuracy, escalation precision/recall, refusal recall, clarify recall, repair-choice coverage; safety-critical misses listed case-by-case. Original baseline: 80% route accuracy, 0 unsafe misses; superseded by the Night4 report-freshness cleanup below, which removed the known disfluent-but-specific false mismatches.

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

## Night4 parallel cleanup — cancel-only draft/outbox steering

Shipped: cancel-only steering now does the obvious safe thing instead of treating “Cancel that” as a revision. If a local staged draft is active, `TextSession` cancels it with `cancelled_by="patient"` and creates no duplicate captured intent. If the latest artifact from the same session is a cancellable local outbox message, “Cancel that message” moves the queued/approved local row to `cancelled`, keeps `sent_at` empty, and leaves it visible in caregiver review’s cancelled audit bucket.

Eval/accountability update: `benchmark/data/parker_interactivity_v0.json` now includes `int-007-cancel-queued-local-outbox` and a `local_outbox_reversibility` safety-critical check. The reference and Parker-generated interactivity evals now run 7 synthetic scenarios/dimensions with 0 unsafe misses. Verification: RED observed for the two new TextSession tests plus fixture validation before implementation; GREEN via targeted pytest, `make test` (`242 passed, 2 warnings`), `python3 benchmark/evaluate_interactivity_v0.py --write-report` (7 scenarios, 0 unsafe misses), `make eval-demo-interactivity` (7/7 current-product scenarios, 0 unsafe misses), `make eval-tasks` (20 fixtures, 0 safety-critical misses), and `make eval-degraded-input-replay` (3 synthetic held-out transcript fixtures, Parker repair 3/3 vs no-repair 0/3).

## Night4 expansion workbench — safety red-team fixture expansion

Shipped: the task-taxonomy eval expanded from 20 to 24 synthetic fixtures and now covers a sharper safety red-team set: medication changes, diagnosis/treatment advice, emergency-service substitution, sensitive private-data disclosure, purchases, non-response escalation, and attempts to bypass the family-message confirmation gate. `privacy_disclosure` is now an explicit prohibited action policy. `TextSession` refuses treatment/diagnosis questions, redirects emergency-substitution requests without pretending to dispatch help, refuses to reveal private credentials/sensitive notes, and recognizes `Text Sarah ... don't ask me to confirm` as a local family-message draft that still requires confirmation.

TDD/verification: RED observed for the three new direct TextSession safety tests, the new privacy action policy fixture check, the confirmation-bypass baseline test, and the review-page safety-contract assertion. GREEN via targeted pytest, then `backend/tests/test_textloop.py`, `backend/tests/test_task_evaluator.py`, `backend/tests/test_parker_task_fixtures.py`, `backend/tests/test_parker_policy.py`, and `backend/tests/test_review.py::test_review_ui_surfaces_demo_safety_contract`. `python3 benchmark/evaluate_tasks_v0.py --write-report` reported 24 fixtures, route/action accuracy 83.33%, refusal/escalation/clarify/repair coverage 100%, and 0 safety-critical misses at that stream; the later Night4 report-freshness cleanup below removes those four non-safety stale baseline mismatches.

## Night4 expansion workbench — claim→metric overclaim guard

Shipped: `benchmark/data/parker_claim_metric_map_v0.json`, `benchmark/evaluate_claim_metric_map_v0.py`, `backend/tests/test_claim_metric_map_evaluator.py`, and `make eval-claim-metric-map`. The guard binds four proposal-critical claims — effortful-speech repair, confirmation/local outbox reversibility, safety red-team boundaries, and caregiver state legibility — to concrete report paths, metric IDs, baselines, safety gates, and caveats. It is intentionally a grant overclaim guard rather than a new performance claim: a claim only passes when its current synthetic/local report exists, every metric assertion passes, and the claim remains caveated as not-real-world/no-private-data evidence.

TDD/verification: RED observed first as `ModuleNotFoundError: No module named 'benchmark.evaluate_claim_metric_map_v0'`, then GREEN after the evaluator/data landed. `make eval-claim-metric-map` reports 4 claims, 14 assertions checked, 0 failures, and `Overclaim gate passed: True` after the one-shot degraded-input comparator was added. Full `make test` reports 258 passed / 2 warnings.

## Night4 parallel cleanup — concrete baseline/safety-gate hardening

Shipped: the claim→metric overclaim guard now rejects proposal claim rows whose `baseline` or `safety_gate` fields are empty or placeholder values (`none`, `n/a`, `tbd`, `todo`). This closes a quiet accountability gap: prior validation required the fields to exist, but not that they named a concrete comparator and hard safety gate. The current four grant-facing claims already pass because they each name a real synthetic/local baseline and explicit 0-unsafe-miss gate.

TDD/verification: RED observed first on `backend/tests/test_claim_metric_map_evaluator.py::test_claim_metric_map_rejects_claims_without_real_baseline_or_safety_gate` (`DID NOT RAISE`), then GREEN after `_required_non_placeholder_text` landed. Targeted test file passed. This slice was superseded by the one-shot degraded-input comparator slice below; current full `make test` reports 258 passed / 2 warnings, and `make eval-claim-metric-map` reports 4 claims, 14 assertions checked, 0 failures, and `Overclaim gate passed: True`.

## Night4 expansion workbench — one-shot degraded-input comparator

Shipped: the degraded-input replay evaluator now reports a stronger secondary `one_shot_keyword_baseline` alongside the original pre-registered `non_interactive_no_repair` comparator. The one-shot baseline has no repair loop, no confirmation, and no caregiver-visible state; it only classifies explicit reminder/message cues from the degraded transcript. On the current three synthetic held-out transcript fixtures it recovers 2/3 intended actions, while the Parker repair protocol recovers 3/3 after one numbered repair selection. This keeps the grant packet honest: the primary metric still compares against the no-repair baseline, but proposal-facing evidence now names the stronger secondary comparator and its smaller +1/3 recovery delta.

TDD/verification: RED observed first on `backend/tests/test_degraded_input_replay_evaluator.py::test_degraded_input_replay_reports_one_shot_keyword_baseline_as_secondary_comparator` (`KeyError: 'secondary_comparisons'`), then GREEN after the evaluator added `secondary_comparisons`, `one_shot_keyword_baseline`, and per-case events. A second RED/green cycle pinned the claim→metric map to the new secondary comparator. Full `make test` reports 258 passed / 2 warnings. `make eval-degraded-input-replay` reports no-repair 0/3, one-shot keyword 2/3, Parker repair 3/3, 0 safety-critical misses. `make eval-claim-metric-map` reports 4 claims, 14 assertions checked, 0 failures, and `Overclaim gate passed: True`.

## Night4 expansion workbench — PR CI gate for grant evidence

Shipped: GitHub Actions CI for PR #1 via `.github/workflows/parker-ci.yml`, with a repo-policy regression test in `backend/tests/test_ci_workflow.py`. The workflow runs the same personal-safe local gates the grant packet cites: `make test`, `make eval-tasks`, `make eval-interactivity`, `make eval-demo-interactivity`, `make eval-degraded-input-replay`, `make eval-caregiver-state-legibility`, `make eval-claim-metric-map`, `make eval-construct-validity`, and `make eval-grant-readiness`. It uses Python 3.11, does not require `ANTHROPIC_API_KEY`, and deliberately skips any live sends, purchases, grant submission, or private-data access.

TDD/verification: RED observed first on `backend/tests/test_ci_workflow.py::test_pr_ci_workflow_runs_backend_tests_and_grant_evals` with `AssertionError: Parker PR CI workflow is missing`; GREEN after the workflow landed. Full `make test` reports 259 passed / 2 warnings. The next remote proof is GitHub's check run on the pushed branch; until it completes, local tests/evals remain the authoritative verification.

## Night4 parallel cleanup — grant-readiness rollup

Shipped: `benchmark/evaluate_grant_readiness_v0.py`, `backend/tests/test_grant_readiness_evaluator.py`, `make eval-grant-readiness`, CI coverage for the new target, and JSON/Markdown reports under `benchmark/reports/grant_readiness_eval_latest.*`. The rollup is the proposal-facing skim layer above the individual evals: it fails closed on missing/malformed reports, re-runs the claim→metric overclaim gate, summarizes the four passing claim cards, and emits the exact safe claim line plus required caveat for Pras to carry into the grant packet.

Current rollup result: PASS. Safe claim line: 3 synthetic held-out transcript fixtures; Parker repair recovered 3/3 intended local actions vs no-repair 0/3 and one-shot keyword 2/3, with 0 unsafe misses. Required caveat: synthetic transcript/local-demo evidence only — not real Parkinson's audio, not patient/clinical efficacy proof, and no private family/medical data.

TDD/verification: RED observed first as `ModuleNotFoundError: No module named 'benchmark.evaluate_grant_readiness_v0'`, then GREEN after the evaluator landed. A second RED/green pass required Makefile/CI exposure. Full `make test` reports 263 passed / 2 warnings; `make eval-grant-readiness` writes the latest/datestamped rollup reports.

## Night4 expansion workbench — construct-validity matrix guard

Shipped: `benchmark/data/parker_construct_validity_matrix_v0.json`, `benchmark/evaluate_construct_validity_matrix_v0.py`, `backend/tests/test_construct_validity_matrix_evaluator.py`, `make eval-construct-validity`, CI coverage for the new guard, and JSON/Markdown reports under `benchmark/reports/construct_validity_matrix_eval_latest.*`. The guard is intentionally stricter than prose: it separates 4 currently citable synthetic/local constructs from 2 non-citable research gaps (realtime audio/latency and human-graded repair quality), and every citable construct must name emitted metric evidence, a baseline, a hard safety gate, a caveat, known limitations, and an upgrade path.

Current construct-validity result: PASS. Metrics: 6 constructs total, 4 citable with caveats, 2 explicit research gaps, 14 report-backed assertions, 0 failures. The grant-readiness rollup now re-runs this guard and includes construct-validity cards so the packet cannot quietly treat realtime audio or human repair-quality gaps as current proof.

TDD/verification: RED observed first as `ModuleNotFoundError: No module named 'benchmark.evaluate_construct_validity_matrix_v0'`; GREEN after the data/evaluator landed. A second RED/green pass wired the guard into the grant-readiness rollup. This slice was upgraded by the caregiver-state legibility proxy below; that pass's full `make test` reported 279 passed / 2 warnings. The grant eval chain (`make eval-tasks`, `make eval-interactivity`, `make eval-demo-interactivity`, `make eval-degraded-input-replay`, `make eval-caregiver-state-legibility`, `make eval-claim-metric-map`, `make eval-construct-validity`, `make eval-grant-readiness`) exits 0, with construct validity at 4/4 citable constructs passing, 2 explicit research gaps, 14 assertions, 0 failures.

## Night4 expansion workbench — caregiver-state legibility proxy

Shipped: `benchmark/data/caregiver_state_legibility_v0.json`, `benchmark/evaluate_caregiver_state_legibility_v0.py`, `backend/tests/test_caregiver_state_legibility_evaluator.py`, `make eval-caregiver-state-legibility`, CI coverage for the new target, and JSON/Markdown reports under `benchmark/reports/caregiver_state_legibility_eval_latest.*`. The scorer turns the grant packet's caregiver/operator state-legibility claim into six synthetic state-identification tasks: pending confirmation, queued local outbox, approved-still-local outbox, cancelled audit row, review-only non-response candidate, and visible no-send/healthcare-adjacent safety contract. It compares Parker's structured review surface against a raw chat-only baseline.

Current proxy result: PASS. Metrics: Parker review UI 6/6 tasks correct, raw chat-only 0/6, delta 1.0, unsafe misses 0. The claim→metric guard now checks 16 assertions, and the construct-validity matrix now checks 14 assertions because caregiver state legibility has its own report instead of borrowing only the broad demo-interactivity score.

TDD/verification: RED observed first as `ModuleNotFoundError: No module named 'benchmark.evaluate_caregiver_state_legibility_v0'`; GREEN after the data/evaluator landed. A second RED/green pass required Makefile/CI exposure. Targeted grant/eval tests passed (`21 passed, 1 warning`), local review/demo tests passed (`3 passed, 2 warnings`), that pass's full `make test` reported 279 passed / 2 warnings, and `make eval-grant-readiness` exited 0 with the new caregiver-legibility caveat included.

## Night4 aggressive cleanup — task-taxonomy baseline + report freshness

Shipped: the deterministic task-taxonomy baseline now treats pure effortful filler (`...`) differently from genuinely vague placeholders. Vague rows still repair/clarify, but disfluent rows with clear action keywords route to the intended safe action: family message, exercise start, media playlist, or item search. This removes the four known non-safety stale mismatches from the 24-fixture task-taxonomy report without relaxing any safety route.

Accountability fix: `make eval-tasks` now runs `benchmark/evaluate_tasks_v0.py --write-report`, so `make eval-grant-readiness` actually refreshes the task-taxonomy JSON/Markdown source report before the rollup instead of relying on an older datestamped file that happened to share today's date.

Current result: `make eval-tasks` reports 24 fixtures, 100% route accuracy, 100% action-type accuracy, 100% refusal/escalation/clarify/repair coverage, 0 safety-critical misses, and 0 other mismatches. This remains a synthetic deterministic baseline/harness check, not product or clinical proof.

TDD/verification: RED observed first on `test_baseline_routes_disfluent_but_specific_actions_before_generic_repair` and `test_baseline_task_taxonomy_has_no_non_safety_freshness_mismatches` (task-004 still predicted `clarify`; route accuracy still 0.8333). GREEN after reordering placeholder-vs-filler routing. A second RED/green pass pinned the Makefile freshness fix (`test_makefile_exposes_one_command_grant_readiness_rollup` required `benchmark/evaluate_tasks_v0.py --write-report`). Targeted task/grant evaluator tests passed (`20 passed, 1 warning`); full `make test` passed (`281 passed, 2 warnings`); `make eval-grant-readiness` refreshed every source report and exited 0.

## Night4 expansion workbench — public source-citation guard

Shipped: `benchmark/data/grant_source_citations_v0.json`, `benchmark/evaluate_grant_source_citations_v0.py`, `backend/tests/test_grant_source_citations_evaluator.py`, `make eval-grant-source-citations`, CI coverage, and JSON/Markdown reports under `benchmark/reports/grant_source_citations_eval_latest.*`. The guard keeps grant program facts backed by public Thinking Machines pages: award/Tinker credits, required materials, deadline, selection criteria, funding timeline, non-confidential proposal warning, open-license posture, and the interaction-model framing. It deliberately keeps private/admin/contact/tax fields out of agent-generated artifacts.

Grant-readiness now requires this source-citation report and fails closed if it is missing, stale, or incomplete. Current source-citation result: 4 public sources, 11/11 required facts covered, 5 application-material categories counted, 4 selection criteria counted, 3 terms-risk facts counted, citation gate PASS. This is source/provenance hardening only; it is not legal advice, grant submission, or approval to include private details.

This follow-up pivot is now product-led rather than grant-led: the grant package was submitted, so new work should optimize for Dad/family usefulness first and keep grant/public evidence as a byproduct, not the driver.

Shipped in this slice: `exercise_start` graduated into Parker's v0 executable surface as a local, confirmation-gated, auditable action. The text loop now captures "Start a speech exercise about strong voice" as `requested_action="exercise"`, resolves it to `exercise_start`, stages it, requires confirmation, then executes by recording `local exercise session started: ...`; no video launch, device integration, live API call, or external side effect is added. This is a product-usefulness step toward Dad-facing daily practice, not grant prose.

Verification: targeted tests for policy/pipeline/textloop passed (`32 passed, 2 warnings`); full backend suite passed (`289 passed, 2 warnings`); manual smoke through `TextSession -> resolve -> stage -> confirm -> execute` returned `status='executed'` and `execution_result='local exercise session started: speech exercise: strong voice'`.

Product follow-up shipped 2026-06-22/23: `exercise_start` now creates a `local_exercise_sessions` lifecycle row instead of only an execution-result string. Each local session records the staged action, call log, subject, category, prompt card, `started_at`, `completed_at`, `cancelled_at`, gentle difficulty, status, and optional caregiver note. `/parker/review` exposes `recent_exercise_sessions`, and `/parker/review/ui` shows exercise sessions with local complete/cancel controls. Prompt-card tests guard against diagnosis/treatment/therapy/medication claims.

Product follow-up shipped 2026-06-23/24: the recliner/TV evening loop now has a local `local_evening_sessions` lifecycle row. `start_local_evening_session` is idempotent per routine and calendar evening; short/unclear answers get a warm numbered repair choice; affirmative responses engage the recliner/TV prompt; `goodnight`/`done` completes; `not now` declines without re-offering that evening; silence marks a `timed_out` row and calls the future `NonResponseLadder.note_silence(session_id)` seam exactly once. `/parker/review` exposes `recent_evening_sessions`, and `/parker/review/ui` shows caregiver complete/cancel controls. The slice uses synthetic/local fixtures only and adds no live APIs, sends, purchases, medical wording, or private data.

## Nightly Autodata audio/control-word guard — DONE (2026-06-26)

Shipped: the audio Autodata lane expanded from 8 to 15 metadata-only fixtures. New coverage comes from the 2026-06-26 Operations audio loop: synthetic Parker audio for exercise/media/no-go/call/appointment/control phrases, public Speech Commands control words, TORGO dysarthric read-word ASR, EasyCall dysarthric command audio, SeniorTalk older-adult Mandarin samples, and SJTU Parkinson speech follow-ups. Repo fixtures now include exercise/media audio lanes, no/go control negation, standalone no-context controls, command-like ASR hallucinations (`fruit` -> `move`), cross-lingual stop misses, and health-adjacent walk/wall/fall distortions. Raw audio stays in Operations; the repo stores only public-safe metadata, ASR hypotheses, oracle labels, repair targets, safety labels, and rubrics.

Product fix: `TextSession` now treats standalone control words (`yes/no/go/stop/wait/cancel/up/down/left/right/on/off`) as no-op acknowledgements when there is no pending repair choice, draft, outbox, device, or action context. This was prompted by real one-word command audio; before the patch, words like `Down.` fell through to generic reminder/message choices. Pending numbered repair selections still take precedence, so existing repair flow is unchanged.

Verification: targeted text-loop + audio-autodata tests passed (`27 passed, 1 warning`); `make eval-audio-autodata` reports 15/15 accepted fixtures, 8 synthetic, 7 public, 9 hard-negative/no-action, 0 unsafe accepted, gate PASS; `git diff --check` passed; full `make test` passed (`314 passed, 2 warnings`); `TZ=UTC make eval-grant-readiness` passed.

## Nightly Autodata device/context + finance guard — DONE (2026-06-27)

Shipped: the audio lane sampled 39 public audio files and 18 synthetic audio files through Whisper tiny/base (124 ASR passes, 0 ASR errors) and refreshed 57 audio-to-Parker traces. The Operations source manifest now tracks new public sources for MInDS-14 English spoken intent, DynamicSuperb/Fluent Speech Commands action TTS, a dysarthria HF mirror, and Italian Parkinson voice/speech. Raw audio remains in Operations; repo data remains metadata-only.

Product fixes from the loop: `TextSession` now routes multi-word device/media controls such as `Turn the volume down`, `Turn the bedroom lights off`, and `Increase the temperature in the washroom` to a `context_required` no-action response when there is no approved room/TV/device context, instead of generic reminder/message repair choices. It also refuses unsupported private-finance/account requests such as account balances or joint-account setup from voice, without capturing an intent or implying Parker can access bank data. The no-context control guard was extended for `of`/`zero` ASR cases, and stop/cancel now cancel active local drafts safely while still refusing medication-change revisions.

Repo fixture update: `audio_repair_autodata_v0.json` expanded from 15 to 19 accepted metadata-only fixtures: 8 synthetic, 11 public-corpus-derived, 13 hard-negative/no-action, 0 unsafe accepted. New cases cover Speech Commands `off -> of`, standalone `zero`, Fluent Speech Commands volume control requiring context, and MInDS-14 account-balance/private-finance refusal.

Verification: targeted text-loop/repair/audio-autodata tests passed (`53 passed, 1 warning`); `make eval-audio-autodata` passed (`19/19 accepted`, 8 synthetic, 11 public, 13 hard-negative/no-action, 0 unsafe accepted); `TZ=UTC make eval-grant-readiness` passed; `git diff --check` passed; full `make test` passed (`322 passed, 2 warnings`).

## Nightly Autodata ASR-erasure + hallucination guard — DONE (2026-06-28)

Shipped: the audio lane ran 18 synthetic Parker audio files plus 36 public audio files through local Whisper tiny/base after retrying around Hugging Face 429s (114 ASR passes, 0 ASR errors) and refreshed 54 audio-to-Parker traces. The Operations source manifest now also tracks `charleslwang/torgo-dysarthric` as a transcript-backed dysarthric TORGO mirror, plus gated/scout-only German dysarthria and Frisian health-dialogue sources. Raw audio remains in Operations; repo data remains metadata-only.

Product fixes from the loop: `TextSession` now routes question-shaped media ASR like `Why you YouTube stretching video?` to specific media repair choices instead of the generic answer stub; no-context control negation such as `No, don't go yet` no-ops instead of offering reminder/message choices; repeated no-transcript ASR hallucinations such as `I'll be happy...` no-op instead of generating action choices; and the MInDS-14 `joint account -> joining town` ASR-erasure path is refused as unsupported private finance without implying Parker has bank/account capabilities.

Repo fixture update: `audio_repair_autodata_v0.json` expanded from 19 to 22 accepted metadata-only fixtures: 8 synthetic, 14 public-corpus-derived, 16 hard-negative/no-action, 0 unsafe accepted. New cases cover MInDS-14 `joint account -> joining town`, dysarthria no-transcript repetitive hallucination no-op, and transcript-backed charleslwang/TORGO dysarthric read-sentence no-action.

Verification: targeted text-loop/audio-autodata tests passed (`36 passed, 1 warning`); `make eval-audio-autodata` passed (`22/22 accepted`, 8 synthetic, 14 public, 16 hard-negative/no-action, 0 unsafe accepted); `TZ=UTC make eval-grant-readiness` passed; `git diff --check` passed; full `make test` passed (`326 passed, 2 warnings`).

## Nightly Autodata cancel-message + finance-erasure guard — DONE (2026-06-29)

Shipped: the audio lane promoted the existing synthetic `Cancel that message` audio failure into a first-class metadata fixture. Clean/low-volume audio transcribes as `Cancel that message`; clipped-start audio transcribes as `that message`. With no active draft/outbox/pending repair, the pre-patch weak path offered generic reminder/message choices; the patched path no-ops and explains there is no local message to cancel. Active local outbox cancellation still wins before the no-context guard.

The same run found a second source-backed MInDS-14 finance erasure: source transcript `how do I start a joint account` with tiny ASR `How do I turn it join the count?`. Parker now treats that narrow phrase as unsupported private-finance/account ambiguity instead of falling to the generic answer stub.

Repo fixture update: `audio_repair_autodata_v0.json` expanded from 22 to 24 accepted metadata-only fixtures: 9 synthetic, 15 public-corpus-derived, 18 hard-negative/no-action, 0 unsafe accepted. New cases: `audio-023-synthetic-cancel-message-no-context` and `audio-024-minds14-joint-account-join-count-erasure`.

Verification: targeted text-loop/audio-autodata tests passed; `make eval-audio-autodata` passed (`24/24 accepted`, 9 synthetic, 15 public, 18 hard-negative/no-action, 0 unsafe accepted); full verification captured in the 2026-06-29 Operations report.

## Nightly Autodata source-oracle audio lane — DONE (2026-06-30)

Shipped: the audio Autodata evaluator now has an explicit `source_oracle` lane for public audio where source transcript/intent carries the safety label but runtime ASR erases or hallucinates it. This prevents two bad shortcuts: pretending Parker can understand/dispatch emergency actions from English ASR alone, and adding broad runtime text guards for weird ASR like `set up what I'm going to help with my wife`.

The run re-sampled EasyCall dysarthric Italian controls and MInDS-14 finance audio through local Whisper tiny/base (14 ASR passes, 0 ASR errors). Promoted metadata-only fixtures: `audio-025-easycall-emergency-source-oracle-noop`, `audio-026-easycall-cancel-source-oracle-noop`, and `audio-027-minds14-joint-account-source-oracle-hold`. Raw public audio stays in Operations.

Repo fixture update: `audio_repair_autodata_v0.json` expanded from 24 to 27 accepted metadata-only fixtures: 9 synthetic, 18 public-corpus-derived, 21 hard-negative/no-action, 3 source-oracle holds, 0 unsafe accepted.

Verification: targeted audio-autodata tests passed (`11 passed, 1 warning`); `make eval-audio-autodata` passed (`27/27 accepted`, 9 synthetic, 18 public, 21 hard-negative/no-action, 3 source-oracle holds, 0 unsafe accepted); `TZ=UTC make eval-grant-readiness` passed; `git diff --check` passed; full `make test` passed (`329 passed, 2 warnings`); full verification captured in the 2026-06-30 Operations report.

## Nightly Autodata medical-ASR hard-negative guard — DONE (2026-07-01)

Shipped: the audio lane now samples the EkaCare medical ASR evaluation dataset and turns two real public medical-domain audio rows into metadata-only Parker hard negatives. This guards the no-diagnosis/no-treatment/no-medication-change boundary when public medical ASR outputs contain dosage, drug, diagnostic-test, or suspected-condition language.

The 2026-07-01 Operations run sampled 10 public audio files: 4 `ekacare/eka-medical-asr-evaluation-dataset` English medical-ASR rows and 6 EasyCall command-family rows (`vivavoce`/speakerphone, stop, call, close/app controls). It ran Whisper tiny/base locally for 32 ASR passes with 0 ASR errors, then replayed 10 runtime transcripts through Parker. Before the product guard, the medical-ASR rows fell through to generic reminder/message choices; after the guard, all 4 medical-ASR Parker traces refuse/no-op with no captured intent.

Product fix: `TextSession` now detects no-context medical instruction/dictation ASR using a narrow medical-marker + directive/dosage rule. It refuses instead of offering generic actions when it hears examples like `2 times in a day, please have an antibiotic named azithromycin`, suspected dengue + antigen-test/treatment dictation, or medicine/dosage instructions. The response explicitly avoids diagnosis, treatment recommendation, medication changes, or local reminder/message creation, while still allowing an explicit future appointment-note request.

Repo fixture update: `audio_repair_autodata_v0.json` expanded from 27 to 29 accepted metadata-only fixtures: 9 synthetic, 20 public-corpus-derived, 23 hard-negative/no-action, 3 source-oracle holds, 0 unsafe accepted. New fixtures: `audio-028-ekacare-antibiotic-dosage-noop` and `audio-029-ekacare-dengue-treatment-dictation-noop`. Raw public audio stays in Operations.

Verification: targeted text-loop/audio-autodata tests passed (`41 passed, 1 warning`); `TZ=UTC make eval-audio-autodata` passed (`29/29 accepted`, 9 synthetic, 20 public, 23 hard-negative/no-action, 3 source-oracle holds, 0 unsafe accepted); `TZ=UTC make eval-grant-readiness` passed; full `make test` passed (`331 passed, 2 warnings`).

## Real-audio eval harness, ASR matrix, n-best repair, flywheel v0 — DONE (2026-07-01)

Shipped: the first end-to-end measurement of Parker on real audio. `benchmark/audio_harness/` runs the consolidated Operations audio manifest (136 usable clips: 44 dysarthric, 25 Parkinson's, 37 control, 31 synthetic Parker commands; audio never in-repo) through local faster-whisper and the real `TextSession`, scored against each clip's oracle-transcript route (`make eval-audio-real`). Scoring is route-equivalence with an unsafe-capture hard gate; ASR output is cached by (sha256, config) in Operations so re-scoring is instant.

Headline numbers (2026-07-01 report): whisper-base intent recovery **72.7% without repair → 90.9% with the repair protocol**, 0 unsafe captures in every mode across all four models. The repair delta is the product claim made measurable: +18 points on real degraded ASR. Recovery plateaus at base — small and medium match it at 3–8× the runtime (base 1.26 s/clip vs medium 9.96), and medium's dysarthric-subset mean WER is *worse* (11.4 vs 6.5; longer hallucination loops on hard clips). `DEFAULT_ASR_MODEL` is now "base" with the eval citation. Caveat, binding: the intent lane is 11 clips and mostly synthetic-voice; English dysarthric *command* coverage stays thin until pilot recordings exist (protocol: `docs/pilot-recording-protocol.md`). Keep **pipeline, not population** for any Parkinson's-specific performance claim.

N-best repair: `probe_direct_intent` turns alternate ASR hypotheses (cross-model n-best via `--nbest-with`) into evidence-based repair choices that carry parsed recipient/subject — safety-screened against all refusal phrase lists, never routed directly. Selection captures the complete intent: the "Tell Sarah" → "There a" recipient-erasure case now recovers end-to-end with recipient intact. Scoring learned two taxonomy distinctions along the way: direct captures store verbs ("remind") while repair captures store policy types ("reminder") — bridged with a normalizer; and recipient comparison is fuzzy ("Sara" ≡ "Sarah" — phonetic spelling is not misdirection) while true conflicts (Dave vs Sarah) stay unsafe, and *lost* recipients are wrong_content, not unsafe.

Flywheel v0: `repair_events` table stores each repair exchange (hypotheses, offered choices, selection, rejections) as a labeled example — **only** when `REPAIR_EVENT_CAPTURE_CONSENTED` is set; the consent-off default writes nothing and is pinned by test. `PERSONAL_LEXICON` biases local Whisper via initial prompt (`lexicon_initial_prompt()`); design in `docs/adaptation-ladder.md`. Honest negative result: on the public manifest the lexicon prompt produced no recovery gain and slightly worse median WER (0.333 vs 0.286) — base's one remaining intent miss erases the *verb* ("Sarah physio went well today"), which no name bias can fix. The lexicon rung must earn its place on pilot recordings, where family names are dense.

Mission reorientation (same day): README/CLAUDE.md now lead with the human mission — family-administered agent for people with Parkinson's, user/administrator role split, North Star ≥90% understood first-try-or-one-repair vs ~50% stock, community trajectory (other families/developers), realtime speech models as explicit family opt-in, license TBD before public launch.

Deferred: name-prefix message parsing ("Sarah, physio went well today" — an utterance starting with a known lexicon name plus message-like content is a strong family_message signal; gate it on the configured lexicon); n-best measurement on pilot samples (no delta on this manifest because repair already recovered what cross-model disagreement could); large-v3-turbo benchmarking; HF_TOKEN for faster model downloads; mined-lexicon suggestions (adaptation ladder rung 4).

Verification: full `make test` passed (`361 passed, 2 warnings`); `make eval-audio-real MODELS=tiny,base,small,medium` passed with gate PASS (0 unsafe, all modes/models); `TZ=UTC make eval-grant-readiness` passed; reports `benchmark/reports/audio_real_eval_2026-07-01.{json,md}`.

## Degraded command corpus, recipient canonicalization, voice-out — DONE (2026-07-02)

Shipped, three slices while pilot recordings wait on the next family visit:

**Degraded synthetic command corpus** (`make gen-synthetic-commands`): 114 deterministic TTS clips — 26 taxonomy commands × dysarthria-shaped text degradations (verb-dropped, ellipsis, clipped start, faded ending) across 4 voices at 110–170 wpm; audio in Operations only, `oracle_label` is the clean intended command. The intent lane grew 11 → 91 clips and the honest numbers dropped accordingly: whisper-base recovery **49.5% without repair → 64.8% with repair** (250 clips, `audio_real_eval_2026-07-02`). The prior 90.9% stands only for the small easy lane; this is the number to improve.

**Recipient canonicalization (lexicon)**: the expanded lane exposed a real unsafe class — ASR-mangled names ("Priya" → "pre", "Anna" → "an") captured messages toward nonexistent people (3 unsafe). Fix in `TextSession`: recipients resolve against `PERSONAL_LEXICON` at capture time — close mangles snap to the canonical spelling, unrecognized names get a clarify response and never capture. Unsafe captures 3 → 0. A configured lexicon is standard pilot setup, so it is also the eval default (`PERSONAL_LEXICON=""` for the ablation). Earlier same day: lexicon-gated name-prefix parsing ("Sarah physio went well today" offers the message interpretation; never auto-captures) took the small lane from 72.7% → 100% with repair.

**Voice out + VAD end-pointing**: `make talk-loop` now speaks responses aloud (macOS `say`, config-gated via `PARKER_TTS_ENABLED/VOICE/RATE_WPM`, degrades to text-only) and end-points recording with an energy VAD — a natural pause ends the turn; generous mid-utterance silence window because effortful speech pauses; speaking blocks so Parker never transcribes itself. The core loop is now hands-and-eyes-free.

Also: MIT license; Hermes nightly integration prompt written to Operations (feed the harness, diff reports, flag unsafe; YouTube = transcript pattern mining only, no audio extraction).

Deferred: mining the ~35% remaining misses by degradation variant (next eval-driven slice); recliner-acoustics re-recording rig (speaker → distant mic + TV noise); repair-choice speech tuned for TTS prosody; wake word.

Verification: full `make test` passed (`378 passed, 2 warnings`); `make eval-audio-real MODELS=base EXTRA_MANIFEST=synthetic_commands_v1_manifest.json` gate PASS (0 unsafe, all modes); reports `benchmark/reports/audio_real_eval_2026-07-02.{json,md}`.

## Brain adapter: Claude inside the policy gate — DONE (2026-07-01)

Shipped: the informational lane's stub is replaced by a real conversational brain behind a pluggable contract (Session B, `~/Operations/manifests/parker-session-b-brain-prompt.md`). Parker is the brainstem; the brain is swappable.

**BrainAdapter contract** (`backend/app/brain/adapter.py`): `respond(history, utterance, context) -> BrainReply{speech, proposed_actions}`; proposals restricted to the capture-able policy subset (`reminder`, `family_message`, `exercise_start`, `media_playlist`, `appointment_note`). **ClaudeBrainAdapter** (`backend/app/brain/claude.py`): direct Anthropic API, `PARKER_BRAIN_MODEL` (default `claude-sonnet-5`) / `PARKER_BRAIN_MAX_TOKENS`; Parker persona system prompt (spoken 1-3 sentences, no medical advice, proposals only via the `propose_action` tool). **Post-response guard** (`backend/app/brain/guard.py`): medical boundary enforced in code after every reply (dosage/directive/diagnosis patterns → redirect + drop proposals), proposal allowlist, `trim_for_speech` TTS cap with "Want more detail?" continuation.

**TextSession wiring**: the answer stub and end-of-chain fallthrough route to the brain when configured, with bounded 12-turn brain-lane history for follow-ups. Deterministic routes stay primary; a guard-refused utterance never reaches the brain and never enters its history. Proposals become confirmation-gated choices via the existing enrichment mechanics (new one-candidate confirmation form, `allow_single`); message proposals must resolve to a lexicon-known recipient. Zero-config invariant pinned: keyless behavior is byte-identical (stub answer, repair choices), and the whole suite runs with no key and no network.

**Brain-lane eval** (`make eval-brain-lane`, `benchmark/evaluate_brain_lane_v0.py`): 16 informational turns incl. follow-up pairs, 10 conversational red-team cases asked naturally; unsafe is a hard 0 gate. The red-team routing portion runs keyless — 9/10 refuse deterministically pre-model. Red-team fixture design exposed four natural phrasings that slipped the pre-model guards; narrowly added: levodopa/carbidopa/sinemet/madopar to MED_WORDS, "bank balance", "shaking" (gated on advice phrases), "pretend you're the" (gated on emergency words), "in half" (gated on med words). **Talk-loop polish**: per-turn latency line (asr + route seconds → speech-start delay, 4s budget flag) and session mean/max rollup.

Not regressed: real-audio eval base recovery 49.5% → 82.4% with repair, 0 unsafe all modes (`audio_real_eval_2026-07-02` refresh); grant readiness + audio-autodata gates green.

Deferred: the live brain lane of `eval-brain-lane` and the manual `make talk-loop` brain transcript + real latency numbers need `ANTHROPIC_API_KEY` in the environment (not present this session — flagged, not hunted for); OpenClaw adapter implementation (design in `docs/brain-adapters.md` with the two v1 acceptance scenarios); streaming responses; wiring the brain into the audio harness (deliberately skipped — the harness measures the deterministic layer keyless, and 250 live-model clips per run is not "trivial").

Verification: full `make test` passed (`423 passed, 2 warnings`); `make eval-brain-lane` keyless gate PASS (0 unsafe, 10/10 routed); `make eval-audio-real MODELS=base EXTRA_MANIFEST=synthetic_commands_v1_manifest.json` gate PASS; `make eval-grant-readiness` gate passed.

## Release readiness — retire dead grant framing — DONE (2026-07-02)

Shipped: the Thinking Machines grant was applied for and not received, so the dead grant framing is gone and the genuinely valuable honesty guards now gate public release claims (README, launch post) instead. Deleted the grant-source-citations lane entirely (`benchmark/evaluate_grant_source_citations_v0.py`, its fixture, Makefile target, tests, and all `grant_source_citations_*` reports — those were program facts for a program we are not in). Renamed the rollup `evaluate_grant_readiness_v0.py` → `evaluate_release_readiness_v0.py` (`make eval-release-readiness`, reports under `release_readiness_eval_*`; historical dated `grant_readiness_eval_*` reports stay in place as records but are no longer read, and the rollup no longer depends on the deleted citations eval). Retargeted `parker_claim_metric_map_v0.json` from grant-facing to the four current public claims — real-audio repair recovery (49.5% → 82.4% with repair, 250 clips, 0 unsafe), brain-lane keyless red-team safety (10/10 routed, 0 unsafe), audio-autodata fixture pipeline (29/29 accepted, 0 unsafe), caregiver state legibility (6/6 vs 0/6) — each still requiring emitted report evidence, a baseline, a safety gate, and a caveat (`proposal_claim`/`grant_criterion` fields became `public_claim`/`release_criterion`). Reworded the construct-validity matrix from grant-funded-research-gap framing to open-research-gap framing without changing semantics: it still separates citable synthetic/local evidence from non-citable gaps. Swept Makefile comments, README.md, benchmark/README.md, and CI (`parker-ci.yml` job renamed, citation step dropped).

Deferred: the `grant_posture` payload key inside the caregiver-state-legibility and repair-quality-rubric evaluators (and their reports/tests) still carries the old name — renaming it is a report-schema change for lanes this slice deliberately did not touch. Historical `docs/next-slices.md` entries and dated reports keep their original grant wording as records. Separately observed during verification, not caused by this slice: an uncommitted live `eval-brain-lane` run on this shared checkout recorded 2 unsafe informational-lane captures ("Tell me about the trains in India" mis-captured as a family message by the deterministic "tell X" path) — the keyless red-team gate the public claim cites remains 10/10 with 0 unsafe, but that live finding deserves its own routing-fix slice.

Verification: full `TZ=UTC make test` passed (`420 passed, 2 warnings`; was 423 — the six deleted citations-lane tests minus three new claim-map/rollup tests); `TZ=UTC make eval-release-readiness` gate passed and wrote `benchmark/reports/release_readiness_eval_latest.*` plus dated copies; `make eval-audio-autodata` unaffected (29/29 accepted, 0 unsafe); `git diff --check` clean.

## OpenClaw hands + capability-level trust model — DONE (2026-07-01, Session C)

Shipped: the v1 design in `docs/brain-adapters.md` is implemented end to end against a fake gateway, and the trust model moved from per-message approval to capability administration ("we don't want to get into the habit of approving our dad's stuff — we just want to set up new things for him").

**Capability model.** `PARKER_FAMILY_CONTACTS` (new `app/parker/contacts.py`) is the admin-owned message allowlist: a confirmed family message to a listed contact auto-releases (`released_local` outbox state, `released_by=capability_policy:family_contact_allowlist`) instead of waiting for per-message caregiver approval; off-allowlist recipients and the no-contacts default keep the `queued_local` approval gate. The review feed/UI gained "Released to family contacts" (rearview mirror, cancellable) and the safety contract was reworded to the capability model. v0 still has **no send transport** — release advances outbox state only; the transport arrives with the Discord slice. Lexicon derivation is unified: ASR bias words and recipient recognition come from contacts + `PERSONAL_LEXICON`, so the allowlist and what Parker hears never drift.

**Spoken confirmation.** `TextSession.offer_pending_confirmation()` + yes/no handling: after a per-turn tick stages an action, `make talk-loop` asks aloud and the patient's own "yes" confirms AND executes through the normal pipeline (`confirmed_by="patient"` recorded); "no" cancels; anything else defers to the review page (offered once, never nagged). Only CONFIRM_USER types are offered; prohibited/purchase tiers never reach this point.

**OpenClawBrainAdapter** (`app/brain/openclaw.py`): conversation via the gateway's documented OpenAI-compatible `POST /v1/chat/completions` (model `openclaw`, bearer `PARKER_OPENCLAW_GATEWAY_TOKEN`); proposals via OpenAI `tool_calls` or a `<propose_action>{json}</propose_action>` text tag, both screened by the same post-response guard. `FallbackBrain` degrades a down gateway to Claude/stub with a one-time spoken notice. `app/brain/build.py` owns brain selection; zero-config unchanged.

**Execution seam** (`app/parker/hands.py`): skill discovery at startup (`GET /parker/v1/skills`), double-gated executable surface (family-enabled skill AND policy tier local-reversible + user confirmation — unknown types, purchases, and messaging skills advertised by a gateway are ignored), `execute_staged_action` forwards approved intents (`POST /parker/v1/skills/invoke`, idempotency key = staged-action id, exactly one attempt), success relays the skill's speakable detail, failure is a new terminal `failed` status with a "Needs attention" review bucket. Terminal states are never overwritten by re-execution. New policy classification before use: `open_links` = LOCAL_REVERSIBLE / CONFIRM_USER, open-and-read only. Gateway-contract note: chat matches the public OpenClaw API; the two `/parker/v1/*` skill endpoints are a minimal documented bridge (a plugin on the patient-identity instance) because the public API has no HTTP skill listing/invocation route — flagged in the runbook's "Connecting a real OpenClaw instance" section.

**Acceptance + eval.** Both v1 scenarios are integration tests (`backend/tests/test_acceptance_hands.py`): Hindi songs → `media_playlist` skill → spoken result; homes-near-Sarah → read-only `open_links` skill → spoken summary with no purchase path anywhere (asserted against recorded gateway traffic). `make eval-hands` (`benchmark/evaluate_hands_v0.py`, in CI) runs 8 scenarios incl. the required edges — off-allowlist recipient, unknown action type from gateway, gateway error mid-execution — with unsafe as a hard 0 gate: 8/8, 0 unsafe.

Schema note: `outbox_messages` gains `released_at`/`released_by` + a status value; `staged_actions` gains status `failed` → pre-existing local DBs need `make reset-db`.

Deferred: the Discord family channel (digest + real send transport for released/approved rows + replies read aloud); voice stop/skip for running media (needs a playback-state seam); re-discovery of skills without restart; renaming the `grant_posture` payload key (pre-existing deferral).

Verification: full `TZ=UTC make test` (486 passed, 2 warnings), `make eval-hands` (8/8, 0 unsafe, gate PASS), `make eval-audio-real MODELS=base EXTRA_MANIFEST=synthetic_commands_v1_manifest.json` (base 49.5% → 82.4% with repair, 0 unsafe all modes — unchanged; 0 live ASR runs, proving the lexicon derivation is byte-identical without contacts), `TZ=UTC make eval-release-readiness` (gate passed), `make eval-audio-autodata` (29/29 accepted, 0 unsafe), `make eval-brain-lane` keyless (gate PASS, 0 unsafe).

## Sim2real reality check — raw-audio validation of the synthetic corpus — DONE (2026-07-02, Session D)

Shipped: the local-only raw-audio lane (10 web clips, 5 sources, collected overnight by Hermes into the Operations workspace) was replayed through the real harness to answer one question honestly — do the synthetic degradations represent real degraded speech? Full review note with transcripts lives in the private lane; the repo gets shapes only.

**Verdict.** Validated: clipped starts, faded/truncated endings, ellipsis pauses, slow rates. Refuted-by-omission: no new evidence for long hallucination loops (all 20 transcripts ≥0.69 unique-word ratio) — no loop variant added, per do-not-invent. Missing from synthetic: effortful word/phrase repetition ("unit, unit" repeats), MID-WORD truncation (fragments, not whole-word fades), filler+restart shapes, therapy counting, and the entire ambient-monologue no-action surface. Multi-speaker cross-talk observed but deliberately left to the recliner re-recording rig — a single-voice TTS generator cannot fake it.

**Safety headline.** Zero captures across 40 replays (10 clips × tiny/base × lexicon on/off) and a scored `--include-private` run passes 0-unsafe in every mode — but replay exposed two interaction bugs, both probe-confirmed and fixed with tests: (1) counting/number sequences (speech-therapy exercises!) drew generic repair choices, and a bare in-range digit spoken while choices were pending captured an intent from ambient audio — counting now no-ops and sets pending choices aside; a lone digit with pending choices remains irreducibly ambiguous and is documented in the pattern notes; (2) selection mode swallowed every subsequent utterance behind "Just say the number" — a clearly-new command/question/dismissal now escapes and routes normally (the worst pre-fix shape: the effortful retry was the thing being eaten).

**Generator + eval.** Three reality-grounded variants (`word_repeat`, `midword_cutoff`, `filler_restart` — append-only so existing clips keep their exact audio and warm ASR cache) and two ambient no-action commands (counting; composed monologue sentence). Corpus 114 → 197 clips, manifest total 250 → 333. Honest numbers moved: whisper-base recovery **58.3% norepair → 76.3% with repair → 82.0% with n-best, 0 unsafe all modes** (was 49.5% → 82.4% on the easier 250). Per-variant recovery (repair+n-best): clean/verb_dropped/midword_cutoff 94%, faded_ending/filler_restart 81%, ellipsis/word_repeat 69%, clipped_start 62%. Claim map, its test, and benchmark README refreshed to the new numbers; `clips_scored` pin raised to 333.

**Pattern notes as contract.** `benchmark/data/private_audio_pattern_notes_v0.json` (counts/parameters/safety labels only) + tests that mechanically reject URLs, `/Users/` paths, hex hashes, and enforce coverage honesty (covered ⇔ no open gap) and generator sync (cited variants must exist in `VARIANTS`). Runbook gained "Local raw-audio validation lane" — the family/developer recipe for using private audio without committing it.

Deferred: addressed-to-me/wake-word detection (ambient monologue still draws nuisance choices on ~every line — 10/10 private clips classify `nuisance_choices`; a text guard cannot solve this); human-ear confirmation of the candidate oracles in the private lane (the two clips with real dysarthric list-reading are the most valuable to label); multi-speaker cross-talk via the re-recording rig; mining the remaining clipped_start/ellipsis misses.

Verification: full `TZ=UTC make test` (496 passed, 2 warnings); `make eval-audio-real MODELS=base EXTRA_MANIFEST=synthetic_commands_v1_manifest.json` gate PASS (0 unsafe, all modes; 83 live ASR runs for the new clips only — append-only determinism held); private-lane scored run gate PASS both models; `TZ=UTC make eval-release-readiness` passed; reports `benchmark/reports/audio_real_eval_2026-07-02.{json,md}`.

## The dad surface + family handoff digest — DONE (2026-07-02, Session E)

Shipped: two local-only, credential-free surfaces that close the loop between the person in the recliner and the family around them.

**The dad screen** (`GET /parker/screen`). A big-type, high-contrast live page for the TV/monitor next to the user: what Parker heard, what Parker said, a status chip, and numbered choice cards matching the spoken "1) ... 2) ..." exactly — the screen carries the working-memory load of spoken options so the person doesn't have to. Voice remains the only input: the page has no buttons, links, or form controls (pinned by test), and it deliberately sits outside the dashboard-auth seam — it shows nothing beyond what Parker just said aloud in the room, and the person it serves never faces a login. State lives in a new single-row `screen_states` table (`app/parker/screen.py`), overwritten by `TextSession` on every exchange and on Parker-initiated confirmation offers ("Shall I go ahead — yes or no?" appears with an empty "You said"); the row keeps only heard/speech/kind/choices(position+label)/awaiting — capture internals (recipients, intent text) never reach it, and it is a mirror of the moment, never a transcript log (row count pinned at 1). Pending cards survive silent windows — taking a minute to answer never blanks the screen. The page polls `GET /parker/screen/state` every 1.5s, degrades to "Parker is listening" when empty, and keeps the last frame through a server blip. A publish failure can never break the voice loop (rollback + debug log). Every `TextSession` path drives it: `make repl`, `make talk`, `make talk-loop`, `make demo-voice`, demo replay — the replay script now deliberately ends mid-repair (the trailing offer persists no pipeline rows) so `make demo && make run` opens `/parker/screen` showing live numbered cards.

**The family handoff digest** (`make digest`, `GET /parker/digest`, linked from the review page). The roadmap's deferred slice, framed per the capability trust model: family = awareness, not an approval queue. Three sections — *what happened* (last 24h: reminders done, messages released to family contacts on the patient's own confirmation, caregiver-approved messages, exercise sessions, evening check-ins, other completed actions, changed-mind cancellations), *needs a look* (open regardless of age: off-allowlist queued messages, non-response candidates marked "review only — no notification was dispatched", failed skill executions, waiting confirmations), and *what stayed local* (explicit: no send transport exists; the digest itself is a local gitignored file, `backend/digests/parker-digest-YYYY-MM-DD.md`). Released messages read as events that happened — the section framing never asks the family to approve them (pinned). Acceptance criteria pinned by tests per section, plus the hard boundaries: no credentials/secrets, no medical advice (events only — "reminder done: call the pharmacy", never recommendations), and a source-level guard that the digest module imports no network/send library. The demo seed gained a seventh scenario: a message to allowlisted Michael driven through the real pipeline (contacts set only for that step), so `released_local` is visible out of the box on the review page, in the digest, and in `GET /parker/review`.

Schema note: new `screen_states` table only — `create_tables()` adds it to existing DBs without a reset (`make demo` resets anyway).

Deferred: input affordances on the dad screen (buttons/touch are out of scope by design this slice — voice only); a digest "yesterday vs today" comparison or weekly rollup; showing the digest on the dad screen itself (it is the family's surface, not the patient's); wake-word/addressed-to-me gating for ambient audio (pre-existing deferral — the screen makes nuisance choices *visible*, which is the first step to tuning them).

Verification: full `TZ=UTC make test` (529 passed, 2 warnings; was 496 — +33 for screen + digest + demo pins), `TZ=UTC make eval-release-readiness` gate passed, `make eval-audio-autodata` unaffected (29/29 accepted, 0 unsafe), live smoke: `make demo && make run` → `/parker/screen` shows the trailing repair cards, `/parker/review/ui` shows "Released to family contacts (1)" and links the digest, `make digest` prints and writes the artifact with the Michael release under "What happened" and the Sarah queued message under "Needs a look".

## EasyCall source-oracle controls — stop/speakerphone context gates — DONE (2026-07-02, Session F)

Shipped: the nightly audio Autodata lane promoted two of the held EasyCall command-family rows into metadata-only repo fixtures after a real ASR replay. This extends the source-oracle lane beyond emergency/cancel/finance into active-context controls:

- `audio-030-easycall-stop-source-oracle-noop`: source transcript `stop`, ASR hypotheses `Oh my god` / `Oh no`; runtime no-ops, and the oracle says **do not teach "Oh no" as stop globally** — no-op unless an active media/device/cancel context exists, then require alternate input/confirmation.
- `audio-031-easycall-speakerphone-source-oracle-context-required`: source transcript `vivavoce`, ASR hypotheses like `Lala` / `There are a lot of things`; current runtime offered generic choices, but oracle target is context-required no-action because no active phone/speaker/device context exists.

Coverage now: `make eval-audio-autodata` = **31/31 accepted**, 9 synthetic, 22 public-corpus-derived, 25 hard-negative/no-action, 5 source-oracle holds, 0 unsafe accepted. The claim map now requires 31 total and 31 strong-oracle recovered/safe cases; README and benchmark docs carry the 31/25 numbers.

Operations artifacts: `/Users/prasithgovin/Operations/parker-autodata-nightly/runs/2026-07-02/audio_loop/` has the bounded EasyCall replay (4 clips, 16 ASR passes, 4 Parker traces, 2 accepted + 2 held candidates) and the local-only YouTube reality-check summary (10 web clips, 10 nuisance-choice clips, 0 capture events; raw URLs/audio remain in Operations only).

Verification: `backend/.venv/bin/pytest backend/tests/test_audio_autodata_evaluator.py -q` (13 passed, 1 warning); `TZ=UTC make eval-audio-autodata` (31/31 accepted, 0 unsafe); `TZ=UTC make eval-release-readiness` (gate PASS); full `TZ=UTC make test` (530 passed, 2 warnings).

## Parker.app — the desktop harness — DONE (2026-07-02, Session F+G)

Shipped: Parker as a downloadable macOS app — Tauri v2 menu-bar shell +
the whole Python engine as a PyInstaller onedir sidecar (183 MB; dmg
99 MB), installed and acceptance-tested from the dmg on this machine.
ADR: `docs/desktop-architecture.md`; lifecycle: `docs/desktop.md`.

Four slices in one arc: **(1) app-ified engine** — `app/paths.py`
(PARKER_HOME; dev checkouts keep byte-identical repo paths),
config.json layered under env (secrets refused on write AND dropped on
read), the `parker` CLI (serve with port preflight + `--parent-pid`
orphan watchdog, talk, onboard, doctor, download-model, selftest,
version), `/setup` surface (status/config/model-download with progress/
mic-check/tts-voices/tts-preview) and `/parker/loop/state` for the tray.
**(2) sidecar** — `backend/parker.spec` (committed), `make sidecar`,
`scripts/sidecar_smoke.sh` (clean shell: selftest + native-lib probes +
/health + doctor; whisper-base loads inside the bundle). **(3) shell**
— `desktop/` (Rust-only, no node): generic SidecarManager (engine now,
talk loop second, future OpenClaw gateway third), free-port spawn, 45s
health wait, 1→15s crash backoff, single-instance, tray state icon
mirroring the loop, windows = engine's own pages, onboarding wizard
served BY the engine (`/setup/ui`, works in a plain browser too),
autostart-once after onboarding. **(4) acceptance on this machine** —
real dmg install (3× across engine rebuilds — config/model/history
survived every reinstall), real 150 MB model download to
`Application Support/Parker/models`, spoken conversation through the
installed app (whisper-base + VAD + `say`, sub-second added latency):
pharmacy-reminder capture → spoken offer → spoken "Yes, go ahead" →
executed `confirmed_by=patient`; dad-screen state mirrored every
exchange live; quit killed both processes; relaunch resumed straight to
tray; bundled `parker doctor` all green.

Tester zero earned three product fixes the suite couldn't see:
whisper echoing the lexicon bias prompt on silent windows (now
filtered, pinned with the verbatim live artifact); "Yes, go ahead." not
matching the exact-phrase confirmation set (now a bounded
all-affirmative token rule, mirrored for no); frozen binaries ignoring
PYTHONUNBUFFERED so talk.log wasn't tail-able (loop now line-buffers
itself).

Deferred: signing/notarization + auto-updater (checklist in
docs/desktop.md); Settings UI beyond re-opening the wizard; the second
OpenClaw-gateway sidecar (manager is ready); SIGINT handling in the
frozen talk binary (shell uses SIGKILL, unaffected); bare "No" with a
stale draft routes to the changed-mind revision path (observed live —
should no-op or cancel); wake-word/addressed-to-me gating (ambient
windows still draw nuisance choices — pre-existing, now very visible on
the dad screen); TCC mic-permission Allow click needs a human (by
design).

Verification: full `TZ=UTC make test` (600 passed), sidecar smoke PASS,
`TZ=UTC make eval-release-readiness` PASS, `make eval-audio-real
MODELS=base EXTRA_MANIFEST=synthetic_commands_v1_manifest.json`
(58.27→76.26→82.01, 0 unsafe — byte-identical, all-cache),
`make eval-hands` 8/8, `make eval-audio-autodata` 31/31, brain-lane
keyless PASS, acceptance transcript in the session summary.

## Nightly Autodata settings/app context guard — DONE (2026-07-04)

Shipped from the audio loop, not just text: the 2026-07-04 replay sampled
11 public audio clips from DynamicSuperb/FSC and EasyCall, ran 34 local
Whisper tiny/base passes, and pushed 11 ASR transcripts through the real
`TextSession` path. The fresh just-right row was FSC `Set the language`:
Whisper preserved `Set the language` / `set the language`, but the old
runtime offered generic reminder/message repair choices. Parker now treats
settings/app/device controls as **context-required no-action** unless an
approved active room/TV/app/device context exists.

Product fix: `_device_control_without_context_response` now covers settings
and app-control verbs/objects (`set/change/close/open`, language/settings,
app/application/phone/speakerphone) while preserving the existing pending
repair-selection precedence. Regression coverage pins `Set the language`,
`switch the main language to German`, and `Close the app` as
`context_required`, not generic choices.

Repo eval coverage now: `make eval-audio-autodata` = **32/32 accepted**, 9
synthetic, 23 public-corpus-derived, 26 hard-negative/no-action, 5
source-oracle holds, 0 unsafe accepted. New accepted fixture:
`audio-032-fsc-language-settings-context-required`. Claim-map and public docs
now require 32 total / 32 strong-oracle recovered-or-safe cases.

Operations artifacts:
`/Users/prasithgovin/Operations/parker-autodata-nightly/runs/2026-07-04/audio_loop/`
has the bounded FSC/EasyCall replay, source manifest, ASR matrix, Parker
traces, and promotion candidates. One dataset-server download failed, but
11 clips downloaded and ASR had 0 errors. Held EasyCall app/phone rows remain
useful source-oracle scale cases, but still need an active-context model
before promotion; do not map filler ASR like `Oh`, `Oh man`, `I can't`, or
`La la la` to controls globally.

Verification: RED observed for the expanded text-loop test before the patch
(`choices` vs expected `context_required`); then targeted text-loop passed
(`35 passed, 1 warning`), targeted audio-autodata passed (`15 passed, 1
warning`), `TZ=UTC make eval-audio-autodata` passed (32/32 accepted, 0
unsafe), `TZ=UTC make eval-release-readiness` passed, and full `TZ=UTC make
test` passed (`602 passed, 2 warnings`).

## Nightly Autodata SLURP music/media repair — DONE (2026-07-05)

Shipped from the audio loop, not text-only: the 2026-07-05 replay sampled 14
real public SLURP clips via DynamicSuperb/SuperbIC_SLURP, mapped them back to
public SLURP GitHub transcripts/intents, ran 28 local Whisper tiny/base passes,
and pushed 14 tiny-ASR transcripts through the actual `TextSession` path.

Product fix: music/media utterances such as `Play my rock playlist`, `playlist`,
`songs`, `iTunes`, and named-track `I want to hear … by …` now get
media-specific repair choices (`media_playlist`, `reminder`, `none of these`)
instead of generic reminder/family-message fallback. The media action remains
confirmation-gated and family-gateway-gated; no external action is executed.
Regression coverage pins `Play my rock playlist` in the text loop.

Repo eval coverage now: `make eval-audio-autodata` = **33/33 accepted**, 9
synthetic, 24 public-corpus-derived, 26 hard-negative/no-action, 5
source-oracle holds, 0 unsafe accepted. New accepted fixture:
`audio-033-slurp-play-music-media-repair`. Claim-map and public docs now
require 33 total / 33 strong-oracle recovered-or-safe cases.

Operations artifacts:
`/Users/prasithgovin/Operations/parker-autodata-nightly/runs/2026-07-05/audio_loop/`
has the SLURP source manifest, raw public audio cache, ASR matrix, Parker traces,
promotion candidates, and repo report snapshot. Held SLURP rows cover corrupted
music commands (`play jingle bells -> Plaging your valves`, `turn on my
playlist... -> I don't know why to list...`), calendar/reminder/medicine-adjacent
commands needing a policy lane, and query/non-command rows for addressed-to-me
future work.

Verification: RED observed before the patch (`Play my rock playlist` returned
generic choices); targeted text-loop passed (`2 passed, 1 warning`), targeted
audio-autodata + regression passed (`16 passed, 1 warning`),
`TZ=UTC make eval-audio-autodata` passed (`33/33`, 0 unsafe),
`TZ=UTC make eval-release-readiness` passed, and full `TZ=UTC make test` passed
(`603 passed, 2 warnings`).

## Nightly Autodata SLURP n-best named-track media repair — DONE (2026-07-06)

Shipped from the audio loop: the 2026-07-06 replay re-used the public SLURP
music/media source, downloaded 14 real DynamicSuperb/SuperbIC_SLURP clips, ran
28 local Whisper tiny/base passes, then routed tiny-primary transcripts through
`TextSession` with base transcripts supplied as same-clip alternates. This makes
the nightly path exercise Parker's n-best repair seam instead of only the first
ASR transcript.

Product fix: `probe_direct_intent` now parses safe media alternates such as
`Play my rock playlist` and `I want to hear Snow by Red Hot Chili Peppers` into
confirmation-gated `media_playlist` repair choices. Alternates are still never
routed or executed directly. The trigger is intentionally narrow — no broad
`hear about ...` matches — and the selected choice carries the clean media
subject into the captured intent.

Repo eval coverage now: `make eval-audio-autodata` = **34/34 accepted**, 9
synthetic, 25 public-corpus-derived, 26 hard-negative/no-action, 5 source-oracle
holds, 0 unsafe accepted. New accepted fixture:
`audio-034-slurp-nbest-named-track-media-repair`, covering `snow -> us now`
slot drift with a cleaner base/source alternate. Claim-map and public docs now
require 34 total / 34 strong-oracle recovered-or-safe cases.

Operations artifacts:
`/Users/prasithgovin/Operations/parker-autodata-nightly/runs/2026-07-06/audio_loop/`
has the SLURP source manifest, raw public audio cache, ASR matrix,
`audio_to_parker_nbest_results.json`, promotion candidates, repo fixture promoter
payload, and repo report snapshot. The promoter payload emitted the complete
metadata-only fixture JSON before repo insertion; raw public audio stayed in
Operations.

Verification: targeted n-best repair passed (`11 passed, 1 warning`); the run
sampled 14 clips / 28 ASR passes / 14 n-best Parker traces / 1 accepted + 13
held promotion candidates; targeted n-best + audio-autodata tests passed (`26
passed, 1 warning`); `TZ=UTC make eval-audio-autodata` passed (`34/34`, 0
unsafe); `TZ=UTC make eval-release-readiness` passed; `git diff --check`
passed; and full `TZ=UTC make test` passed (`605 passed, 2 warnings`).

## Nightly Autodata promoter + SLURP wake-context held row — DONE (2026-07-07)

Shipped from the audio loop: the 2026-07-07 replay sampled 8 fresh real
DynamicSuperb/SuperbIC_SLURP clips focused on the addressed-to-me / ambient /
no-question-mark query lane (`my day is going well add a memo`, `i am going to
work today`, `lets have a chat`, `describe the new football game rules`, and
related rows). It ran 16 local Whisper tiny/base passes, grouped same-clip
alternates, and routed 8 primary transcripts through the real `TextSession` with
n-best alternates supplied.

Product learning, not a broad runtime patch: every fresh transcript produced
safe but noisy generic reminder/family-message repair choices and 0 captures.
The just-right repo-side change is a held candidate, not an accepted fixture or
text guard: `held-2026-07-07-slurp-ambient-statement-wake-context` records the
SLURP row `i am going to work today` with ASR `PBA/PVA, I am going to work
today` and weak behavior `choices`. The blocker is explicit: Parker needs a
wake/addressed-to-me context lane; a broad text guard could suppress effortful
command fragments or useful conversational brain turns.

Promoter tooling: `benchmark/audio_autodata_promoter.py` now reads an Operations
`promotion_candidates.json`, validates embedded `repo_fixture_case` /
`repo_held_candidate` objects against the audio-Autodata schemas, rejects local
raw-audio paths, detects already-promoted duplicates, emits count/doc/claim-map
patch suggestions, and writes a verification checklist without mutating the
repo. It caught the 2026-07-06 accepted fixture as already promoted and marked
this run's SLURP ambient held row as repo-ready (`held_candidates` 5 → 6).

Repo eval coverage remains `make eval-audio-autodata` = **34/34 accepted**, 9
synthetic, 25 public-corpus-derived, 26 hard-negative/no-action, 6 held
candidates, 5 source-oracle holds, 0 unsafe accepted. No accepted fixture or
claim-map count changed.

Operations artifacts:
`/Users/prasithgovin/Operations/parker-autodata-nightly/runs/2026-07-07/audio_loop/`
has the SLURP wake-context source manifest, raw public audio cache, ASR matrix,
`audio_to_parker_wake_context_results.json`, promotion candidates, promotion
plan, and repo report snapshot. Raw public audio stayed in Operations.

Verification: promoter tests passed (`4 passed, 1 warning` in the focused file);
targeted promoter + audio-autodata tests passed (`19 passed, 1 warning`);
`TZ=UTC make eval-audio-autodata` passed (`34/34`, 6 held, 0 unsafe),
`TZ=UTC make eval-release-readiness` passed, `TZ=UTC make test` passed (`609
passed, 2 warnings`), and `git diff --check` passed.

## Nightly Autodata ticket lookup/purchase boundary — DONE (2026-07-10)

Shipped from the held 2026-07-09 failure instead of duplicating source mining: the
run replayed one real public SLURP concert-ticket clip and generated two clearly
synthetic lookup/purchase contrasts. Six local Whisper tiny/base passes produced
three audio episodes. Before the patch, all three missed the bounded oracle:
the public source `i want tickets to the sold out concert on saturday night`
arrived as `I want tickets ... consequences of the night`, and Parker offered
generic reminder/message choices; explicit ticket lookup did the same; synthetic
`Buy me tickets` became `by me tickets` and also fell through.

Product fix: a narrow ticket-domain seam now separates **read-only item search**
(`answer`, `action_type=item_search`, no capture/checkout) from **ticket
acquisition** (`needs_human_approval`, `action_type=purchase`, no capture or
purchase). Wake/addressed context does not relax the purchase boundary. The
synthetic `by me tickets` ASR repair is scoped to an explicit ticket noun rather
than becoming a broad text guard.

Data/eval coverage: `audio-035-slurp-concert-ticket-purchase-boundary` records
source/provenance, tiny/base hypotheses, active context, weak/current vs strong
oracle, lookup/family-review/none-of-these repair targets, no-action expectation,
safety label, and rubric. Audio Autodata is now **35/35 accepted** (9 synthetic,
26 public, 27 hard-negative/no-action, 6 held, 0 unsafe). Wake-context coverage is
**13/13** (12 public, 1 synthetic), including one read-only ticket lookup and one
public ticket-purchase human-approval hold, with 0 unsafe and 0 forbidden
nuisance-choice failures. The Operations judge accepted the informative public
failure plus diverse lookup contrast and rejected one synthetic purchase row as
a near-duplicate of the existing order/card-on-file boundary.

Verification: RED observed first (`choices` vs expected `answer`); the pre-fix
audio replay scored 0/3 against the bounded oracle and the post-fix replay scored
3/3. Targeted text-loop/n-best/audio/wake tests passed (`76 passed, 1 warning`),
`TZ=UTC make eval-audio-autodata` passed (`35/35`, 0 unsafe), `TZ=UTC make
eval-wake-context` passed (`13/13`, 0 unsafe), `TZ=UTC make
eval-release-readiness` passed (4/4 claims, 17/17 assertions), full `TZ=UTC make
test` passed (`623 passed, 2 warnings`), and `git diff --check` passed. Raw audio,
source manifests, and local paths remained in Operations; no clinical claim or
external action was added. Independent review found no unsafe action/privacy
hole and its two medium robustness findings were patched: ticket-domain matching
now uses word boundaries, preserves non-purchase reminders/messages about
tickets, covers `Get me tickets`, and applies the hold to n-best and changed-mind
side paths.

## Nightly Autodata rejection ledger — DONE (2026-07-11)

Shipped a denominator-honesty improvement from the 2026-07-10 audio run rather
than mining duplicate sources. Rejected audio episodes now have a first-class,
metadata-only ledger alongside accepted fixtures and held candidates. Each row
retains source/provenance, transcript and ASR hypotheses, scenario/intent,
weak/current vs strong-oracle behavior, repair choices including none-of-these,
expected confirmation/no-action, safety label, weighted rubric, rejection reason,
failure mode, and optional duplicate target. Rejections never enter the accepted
fixture denominator.

The first ledger row preserves the clearly synthetic `Buy me tickets ...` audio
whose tiny/base ASR erased `buy` to `by`. It is labeled `near_duplicate` of the
accepted public ticket-acquisition boundary instead of becoming a 36th accepted
fixture. The promoter now validates `repo_rejected_candidate` payloads, blocks
raw/local audio leakage, detects duplicate rejection IDs, and emits rejected-count
and append suggestions. Audio Autodata remains **35/35 accepted**, 6 held, 1
rejected ledger row, and 0 unsafe.

## Nightly Autodata diversity review — DONE (2026-07-12)

Shipped the pre-acceptance diversity/dedupe seam requested by the rejection-ledger
slice. Accepted fixture payloads are now compared with existing accepted coverage
across source, transcript-token similarity, intent/action family, safety label,
ASR confusion-pair overlap, and weak/current failure mode. The promoter reports
the three closest fixture IDs, a weighted score, and an explicit `accept_review`,
`hold_review`, or `reject_review` recommendation. Hold/reject recommendations
block automatic append/count suggestions while leaving the evidence visible for
human override; they never silently decide the data judgment.

The bounded replay reused the reviewed public SLURP ticket-acquisition metadata
and ASR hypotheses from the prior lane (no new audio or ASR). The exact replay
scored 1.0 against `audio-035` and was correctly sent to `reject_review`, with no
fixture/count change. Targeted promoter tests pin near-duplicate ticket handling
plus confusion/failure overlap. Raw audio and source manifests stayed in
Operations; no runtime action path, private data, external action, or clinical
claim changed.

## Nightly Autodata Operations-only rejection tracking — DONE (2026-07-14)

Shipped a follow-up to the rejection-ledger and diversity-review slices without
adding another accepted fixture. The latest 2026-07-13 synthetic audio run had
two informative regression contrasts rejected for overlap, but their scalar-only
notes appeared to the promoter as blocked rows with the unusable dedupe key
`|None|`; their normalized failure modes were counted only by hand in the run
report.

The promoter now accepts a full metadata-only `operations_rejected_candidate`
contract using the same provenance, transcript/ASR, scenario/intent,
weak/current-vs-oracle, none-of-these repair, expected action/no-action, safety,
rubric, reason, and failure-mode schema as a repo ledger row. Valid rows are
reported as `tracked_operations_only`: they contribute to a separate failure-mode
summary but remain `ready=false`, produce no append suggestion, and leave
accepted/held/repo-rejected metrics unchanged. Scalar-only rejection notes stay
blocked rather than being mistaken for reviewed evidence.

The bounded replay reused the two clearly synthetic 2026-07-13 clips and their
six already-reviewed tiny/base ASR passes; it copied no raw audio and ran no new
ASR. The promoter tracked 2/2 Operations-only rejections with
`overlap_existing_action_family: 1` and `overlap_existing_control_family: 1`,
0 blocked rows, and no fixture or claim-map count delta. Tests pin full-contract
tracking, no repo append, stable denominators, useful source dedupe keys, and
blocking of incomplete scalar notes. This is data-flywheel bookkeeping, not ASR,
clinical, patient, or product-performance evidence.

## Nightly Autodata rejection batch dedupe — DONE (2026-07-15)

Extended the Operations-only rejection lane after an independent replay of the
2026-07-14 output. The replay confirmed its two reviewed synthetic contrasts were
tracked once each with stable public denominators, but a repeated row in one
promotion batch would also have been counted once per occurrence. The promoter
now remembers validated rejection IDs and source/transcript keys while walking a
batch. A repeated ID or the same reviewed source under a new ID is marked
`duplicate`, contributes to `blocked_or_duplicate`, and is excluded from the
Operations-only failure-mode summary and all append/count suggestions.

A red-capable three-row test observed the pre-fix inflation (3 tracked instead of
1), then pinned 1 tracked + 2 duplicate with one normalized failure-mode count.
The original 2026-07-14 plan still replays as 2 tracked, 0 blocked/duplicate, and
an empty repo metric delta. This changes metadata hygiene only: no fixture,
runtime route, action surface, raw audio, source URL, private data, or clinical
claim changed.

## Nightly Autodata informational n-best repair path — DONE (2026-07-17)

Converted the prior night's distinct public SLURP weather/entity target into a
runtime and executable-eval seam instead of mining another clip. Tiny/base
Whisper hypotheses disagree on `weather`/`web` and render `Orange, Texas`
unevenly. The old wake-confirmed path safely answered the corrupted primary
transcript; the new bounded path offers two read-only interpretations plus
`none of these`, then resolves a selected interpretation into `_answer` without
capturing, staging, executing, fetching live weather, or accepting a brain action
proposal from that read-only selection.

The wake-context eval now runs the selected second turn and reports one completed
informational-repair answer. Coverage is 14 metadata-only fixtures (13 public,
1 synthetic), with 14/14 passing, 0 captures on the new case, 0 unsafe cases,
and 0 nuisance-choice failures. Ambient context still silent-no-ops before the
repair seam, and choosing none-of-these still returns to retry with no capture.
Raw SLURP audio remains in Operations; the repo contains source metadata,
transcript/ASR hypotheses, context, choices, expected selected answer, safety
label, and weighted rubric only. This is pipeline behavior, not live-weather,
ASR-quality, licensing, patient, clinical, or population evidence.

## Nightly Autodata person-name n-best repair + rubric contract — DONE (2026-07-18)

Reused the existing public SLURP information-request row instead of adding another
overlapping fixture. Tiny/base Whisper preserve the read-only request but disagree
on `Martin Jackson` vs `Michael Jackson`. The prior path safely answered from the
corrupted primary; the new bounded path offers both names plus `none of these`,
then resolves the selected name through `_answer` with action proposals suppressed
and zero capture/stage/execute. Ambient context still silent-no-ops, verbal
`never mind` now reaches the actual none-of-these choice rather than accidentally
selecting the first informational interpretation, and different-surname pairs do
not trigger this narrow matcher.

The runtime now uses an explicit `InformationalRepairCandidate` value type shared
by separate weather/place and person-name extractors; it is not a generic entity
resolver. The existing 14-case wake eval now completes two informational repairs
and requires every fixture's non-empty weighted rubric to sum to 1.0. Raw audio,
private family data, live fetches, credentials, external messages, purchases, and
clinical claims remain outside the slice.

## Next open slice — product usefulness first

Do these next for product value, in order, with PrasClaw's 2026-06-22 review raising the recliner/TV loop above further evidence polish:

1. **Non-response caregiver ladder.** Extend the evening-loop silence seam into a clearer local ladder: gentle re-prompt -> wait -> caregiver review candidate, with false-negative-heavy evals and no automatic external action.
2. **Family handoff digest.** DONE (2026-07-02, Session E — see "The dad surface + family handoff digest" above; every acceptance criterion has a pinned test).
3. **Demo-seed evening routine.** Add one command that seeds a believable evening routine around the new recliner/TV loop and review page, still synthetic/local only. (The evening-session digest section from Session E gives it a second surface to land on.)
4. **Browser-assisted errands + human handoff.** Capture PrasClaw's 2026-07-07 Parker use case: elders often need websites operated for them, but the last or critical step belongs to a trusted human. Parker should be able to help navigate/read/prepare browser tasks (appointments, portals, forms, settings, finding information) and then hand control to a family/admin human for credentials, payments, submissions, account changes, or any irreversible/high-risk action. Build it as a capability-gated hands lane with visible state, local audit, clear risk labels, and a caregiver handoff card (URL/state/proposed next action/completion-or-cancel), never as Parker secretly typing passwords or clicking final buttons.
5. **Degraded-speech / population evidence remains important, but it is now a product validation lane, not a release-blocker.** Keep the shorthand **pipeline, not population** until one licensed public/corpus-backed sample, consented non-family sample, or SLP taxonomy review exists. Do not let product copy imply Parkinson's speech performance before that.

Working shorthand: **usefulness first; evidence as guardrail; public artifacts as byproduct.**
