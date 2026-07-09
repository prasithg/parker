# Parker Architecture (v0)

Parker is a family-aware, room-aware, action-capable home assistant for people whose speech, routines, movement, and support needs are changing. Voice is the main interface and first wedge, but Parker is a system, not a call bot, transcription app, medication reminder, or voice-clone demo.

Core loop:

```text
Understand -> Confirm -> Act -> Follow up -> Escalate/Coordinate -> Learn
```

Legacy note: the codebase began as "ParkinsClaw," a scheduled-outbound-call companion using a cloned family voice. Treat that as historical scaffolding, not the product thesis. The active v0 surfaces are Parker-first; remaining legacy references are isolated to historical modules/docs. Voice cloning is optional and consent-gated. A condensed legacy map is in the appendix.

## 1. Product loop mapped to the repo

| Loop stage | What it means | Where it lives today | State |
| --- | --- | --- | --- |
| Understand | Interpret effortful/variable speech as intent, with calibrated uncertainty | `backend/app/conversation/textloop.py`, `backend/app/conversation/tools.py`, `backend/app/conversation/repair.py`, `backend/app/voice/transcribe.py`, `backend/app/demo/` | Typed, scripted-replay, audio-file, live-mic, and continuous talk-loop paths exist. Ambiguity routes through model-driven repair choices when configured, with deterministic fallback when no key/model is available. The text loop now accepts an explicit `UtteranceContext` so a future wake/addressed-to-me detector can keep ambient room speech as silent no-op without disabling directed commands. |
| Confirm | Never act on ambiguous input; repair with choices; confirm before side effects | `backend/app/conversation/repair.py`, `backend/app/parker/pipeline.py` (`confirm_staged_action`, `cancel_staged_action`), `backend/app/parker/policy.py` | Repair choices, "none of these," changed-mind revisions, cancel-only steering, and confirmation gates are implemented before any side effect. |
| Act | Execute safe, policy-allowed actions through a tool layer | `backend/app/parker/pipeline.py` (`execute_staged_action`, outbox helpers), `backend/app/conversation/tools.py`, `backend/app/exercises/session.py` | v0 executes reminders locally, starts local exercise sessions with prompt cards/lifecycle rows, and queues family messages to the local outbox only. The message path has a two-human gate: patient confirmation, then caregiver approval; no sender exists in v0. |
| Follow up | Resurface staged intents, track completion, retry | `backend/app/parker/pipeline.py` (`get_due_resurfaced_actions`), `/parker` routes, review UI | Working vertical slice (capture → resolve → stage → resurface → confirm/cancel → execute/audit) with recent-history and changed-mind audit rows. |
| Escalate/Coordinate | Notify family per policy; severity routing; candidate escalation when the user does not respond | `backend/app/escalation/` (engine, notifier, models), `backend/app/escalation/candidates.py` | Severity-routed escalation engine exists. The non-response escalation candidates path is candidate-only, review-only, `info` severity, and never auto-dispatched. |
| Learn | Memory of the user, family, routines, preferences; eval feedback | `backend/app/memory/`, `benchmark/`, `docs/task-taxonomy.md` | Basic memory store + context builder exists. Accountability now comes from 24 synthetic fixtures, task-taxonomy eval, interactivity trace eval, Parker-generated demo trace eval, degraded-input replay, metadata-only audio Autodata fixtures, wake/addressed-to-me audio-context fixtures, claim→metric overclaim guard, construct-validity matrix guard, and repair-quality spot checks. |

Supporting modules: `backend/app/brain/` (the pluggable conversational brain behind the `BrainAdapter` contract — Claude v0 answers questions and proposes confirmation-gated actions; see [brain-adapters.md](brain-adapters.md)), `backend/app/meds/` (dose tracking + photo-based dose verification), `backend/app/exercises/` (cognitive exercise library plus Parker local exercise-session lifecycle rows), `backend/app/evening/` (local recliner/TV evening-loop lifecycle rows), `backend/app/calls/` and `backend/app/voice/stream.py` (legacy Twilio/realtime call scaffolding), and `backend/app/dashboard/` (family/operator API). The v0 demo path is the Parker text/voice/review pipeline above, not the legacy outbound-call loop.

## 2. Capability taxonomy

The single source of truth is `backend/app/parker/policy.py`. Every action type Parker can resolve maps to a risk tier and a confirmation level:

| Tier | Action types | Confirmation | Executable in v0 |
| --- | --- | --- | --- |
| `informational` (read-only) | `research_summary`, `item_search` | none | no (planned) |
| `local_reversible` | `reminder`, `routine_log`, `appointment_note`, `exercise_start`, `media_playlist` | user (voice/tap) | `reminder`, `exercise_start` |
| `external_messaging` | `family_message` | user | yes — **to the local outbox only** (cancellable row; no send path exists in v0) |
| `external_messaging` | `family_escalation` | escalation policy | no (engine exists; not wired through staged actions) |
| `irreversible_external` | `smart_home`, `calendar_change`, `purchase` | human operator | no |
| `prohibited` | `medication_change`, `medical_advice`, `emergency_response`, `privacy_disclosure` | refuse, always | never |

Unknown action types default to the safest non-prohibited handling: blocked from execution, human-operator review required.

`executable_in_v0` is intentionally narrower than the taxonomy: an action type graduates to executable only after it has fixtures in the task taxonomy (`benchmark/data/parker_tasks_v0.jsonl`), passing evals, and tests for its failure modes.

## 3. Safe action protocol

All side-effectful behavior flows through one staged pipeline (`backend/app/parker/pipeline.py`):

```text
capture_intent          conversation tool persists the intent (status: pending)
      ↓
resolve                 due intents become action candidates typed against the policy taxonomy
      ↓
stage                   only policy-executable types stage; everything else is rejected with a reason
      ↓
resurface               due staged actions surface for the user (GET /parker/resurface)
      ↓
confirm                 user/caregiver confirmation recorded (who + when)
      ↓
execute                 only confirmed + policy-executable actions run; all else is blocked with a reason
```

Protocol rules:

1. No execution without an explicit confirm step recorded in the database.
2. Rejections and blocks are persisted with human-readable reasons (auditable, eval-able).
3. The set of executable action types comes from `policy.executable_v0_action_types()` — never inlined in pipeline code.
4. Prohibited types are refused at resolution, regardless of confirmation.
5. Unknown types are treated as irreversible until classified.
6. "Execute" must produce a local, reversible artifact: reminders resurface locally; exercise starts create `local_exercise_sessions` rows with category, prompt card, started/completed/cancelled state, perceived difficulty, and optional caregiver note; evening routines create `local_evening_sessions` rows with idempotent one-row-per-evening state, repair/comfort prompt cards, silence timeout evidence, and caregiver complete/cancel controls; confirmed family messages queue to the local outbox (`outbox_messages`, cancellable via `POST /parker/outbox/{id}/cancel`).
7. Outbound messages carry a two-human gate: the patient confirms (queues to `queued_local`), then a caregiver approves (`POST /parker/outbox/{id}/approve` → `approved_local`, still on-machine). A future sender — which does not exist in v0 — must only ever consider `approved_local` rows behind an explicit config flag.

## 4. Confirmation policy

Confirmation level is a property of the action type, not the conversation:

- **none** — read-only answers (research, item lookup). Clarify if ambiguous, then answer.
- **user** — the patient or caregiver confirms via voice/tap before the action runs. Applies to all local reversible actions and outbound family messages. Confirmation must restate what will happen ("Send Sarah: '…' — yes?").
- **policy** — family escalation follows the configured escalation policy (severity routing, thresholds), not per-event user confirmation, because the triggering condition may be the user's inability to respond.
- **human_operator** — a family member/operator approves outside the conversation (dashboard/message), required for purchases, smart-home, calendar changes, and any unknown action type.
- **refuse** — never confirmable. Medication changes, medical advice, emergency-service substitution, and requests to reveal private credentials/sensitive notes. Refuse, redirect to doctor/family/emergency services when appropriate, and flag as an escalation candidate when the underlying signal warrants it (e.g. "my pills make me dizzy").

Repair-before-confirm: when intent confidence is low, Parker offers 2–3 concrete choices ("Did you mean call Mary, or remind you to call Mary?") instead of asking the user to repeat. Repair quality is a first-class eval dimension.

## 5. Family escalation policy

Current engine (`backend/app/escalation/`):

- Severities: `info`, `warning`, `urgent`, `missed-dose`.
- Routing: `info` → primary caregiver; `warning`/`missed-dose` → caregiver + family; `urgent` → all contacts.
- Auto-promotion: open `warning` escalations promote to `urgent` after 30 minutes and re-notify.
- Lifecycle: open → acknowledged → resolved, with notes and notified-contact audit trail.

Non-response candidates (`backend/app/escalation/candidates.py`): a staged action repeatedly resurfaced (`parker_non_response_resurface_threshold`, default 3) with no confirmation for `parker_non_response_quiet_minutes` (default 30) becomes an open `info` escalation **candidate**, at most once per action, with no notifications dispatched. Candidates surface through the existing `/escalations` review flow and are flagged on every `POST /parker/tick`. They use severity `info` deliberately: `auto_escalate_check` can promote-and-notify `warning` escalations, and a candidate must never silently become a dispatched notification. Precision matters more than recall here — noisy escalation burns family trust — which is why `non_response_escalation` is also a fixture class in the task taxonomy. Still planned: a caregiver acknowledgment step that upgrades a candidate into a real, severity-routed escalation.

## 6. Privacy: local/private vs public/eval-safe

**Local/private only (this repo's runtime, never published):**

- Real names, contacts, phone numbers, schedules (`FAMILY_CONTACTS_JSON`, settings, `.env`).
- Conversation memories, mood entries, dose logs, escalation history (SQLite).
- Any real transcripts or audio. Raw call audio is not stored at all — transcripts and summaries only, and only locally.
- Voice clones (optional, explicit consent, IDs in local config).

**Public/eval-safe (can graduate to a public repo or HF space with approval):**

- Synthetic fixtures: `benchmark/data/dev_v0.jsonl`, `benchmark/data/parker_tasks_v0.jsonl`.
- Evaluators and validators: `benchmark/evaluate_v0.py`, `benchmark/tasks_v0.py`.
- Task taxonomy and benchmark cards: `docs/task-taxonomy.md`, `docs/benchmarks/`.
- The action policy taxonomy itself (`backend/app/parker/policy.py`) — it encodes safety posture, not family data.

Rule of thumb: anything derived from a real person stays local; anything synthetic and behavioral can be public after explicit approval. No HF repos until approved (see benchmark card).

## 7. OpenClaw/Hermes-style action layer

Parker follows the OpenClaw/Hermes pattern: **context** (memory + family model + routines), **tools** (typed, policy-gated actions), **hands/eyes** (voice now; room/TV context later), **purpose** (help the user be understood and supported at home).

Integration shape:

- The conversational agent never executes side effects directly. Tools like `capture_intent` persist intent; the staged pipeline owns resolution, confirmation, and execution. This keeps the LLM at arm's length from side effects.
- The conversational **brain is pluggable** behind the `BrainAdapter` contract (`backend/app/brain/adapter.py`, design doc: [brain-adapters.md](brain-adapters.md)). Parker is the brainstem: deterministic guards refuse *before* any model call, and a brain can only propose actions that re-enter the capture → confirm pipeline as choices. v0 is Claude over the direct Anthropic API; v1 is the OpenClaw/Hermes action backend (design only); realtime speech models are a later family opt-in — all behind the same contract and the same post-response guard.
- Each executable action type in the policy taxonomy maps to a local artifact behind the `execute_staged_action` seam. v0 has reminders plus family-message outbox rows; both stay local, reversible, and confirmation-gated.
- Future Hermes/OpenClaw interop (family agents coordinating, handoffs) happens at the staged-action boundary: an external agent can propose a staged action, but it goes through the same policy/confirmation gates as a voice intent.
- Room/TV context (recliner, TV on, near medication area) enters as *resolution inputs* — signals that adjust timing, channel, and escalation candidacy — never as direct triggers for external actions.

## 8. What not to build yet

- **Multi-tenant / enterprise healthcare infrastructure.** One family. SQLite. No Alembic until the schema stabilizes (`create_tables()` is fine for v0).
- **Voice cloning as a feature track.** Optional, consent-gated, not the thesis. Don't expand `app/voice/clone.py`.
- **Camera/room perception.** Design for the signals (the pipeline already records resurface counts and timestamps); don't build CV.
- **Smart-home, purchases, calendar writes.** Classified in the taxonomy, blocked from execution. No integrations until confirmation UX and evals exist.
- **Automatic escalation from inference.** Escalation candidates from non-response come first as fixtures and evals; the trigger code lands only when precision is measured.
- **Public benchmark launch.** Local-only until explicitly approved (see `docs/benchmarks/voice-benchmark-card-v0.md`).
- **A broad rewrite of the calls stack.** The Twilio/realtime bridge works as historical scaffolding; generalize the voice interface when the new interaction model needs it, not before.

## Appendix: legacy call-first architecture (historical)

The original ParkinsClaw data flow, kept for orientation while those modules still exist:

```text
Scheduler (APScheduler) → Twilio outbound call → patient answers
  → Twilio Media Stream (WebSocket) → FastAPI bridge (app/voice/stream.py)
  → OpenAI Realtime API (speech-to-speech + tools)
  → optional ElevenLabs cloned-voice TTS
  → tool calls: log_medication, record_mood, cognitive_exercise, escalate_to_family, capture_intent
  → call summary + metrics → SQLite → family dashboard
```

Legacy assumptions to treat as historical: scheduled outbound calls as the primary channel, cloned family voice as the default identity, and the old "ParkinsClaw" product label. The active prompts and Parker v0 surfaces have been reframed; remaining legacy references are historical labels in inactive call/voice scaffolding, old benchmark v0 files, or migration-cleanup comments. Do not use those as product strategy anchors.
