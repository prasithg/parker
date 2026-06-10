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

Left as inert legacy (rename when those modules are next touched): logger names in `calls/`, `voice/`, `meds/`, the `parkinsclaw.db` filename, and `db/models.py`'s docstring.

## Post-milestone slice (2026-06-10): pilot-readiness — reset path, caregiver review UI, text loop

Shipped toward the family-pilot blocker list:

- **`make reset-db`** — deterministic local reset (removes both historical DB locations; tables recreate via `create_tables()`); `make run` now runs from `backend/` so the server and seeding/REPL commands share one DB file (previously they silently used two different SQLite files).
- **Caregiver review surface** — `GET /parker/review` aggregates everything awaiting a human decision (pending staged/confirmed actions, `queued_local` outbox, non-response candidates, other open escalations); `GET /parker/review/ui` serves a single-file local HTML page with confirm/execute/cancel/acknowledge/resolve buttons over existing endpoints. New caregiver control: `POST /parker/actions/{id}/cancel` (`cancel_staged_action` in the pipeline). Confirm/execute/cancel now return typed 404s instead of 500s for missing ids (closes a review-prep note). Tests: `backend/tests/test_review.py`.
- **Text loop** — `make repl` (`backend/app/conversation/textloop.py`): a deterministic transcript-capture seam routing typed utterances through the real tool layer (`offer_repair_choices`, `capture_intent`), with refusal/human-approval guards mirroring the policy. No model, no audio, no confirmation/execution from the loop itself. Tests: `backend/tests/test_textloop.py`.

Deferred: real microphone/ASR input (the seam is `TextSession.handle(text)` — an ASR transcript drops straight in); dashboard auth (page is localhost-only v0); model-driven candidate generation for repair choices.
