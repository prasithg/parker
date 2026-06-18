# Parker

Parker is a family-aware, room-aware, action-capable home assistant for people whose speech, routines, movement, and support needs are changing.

The first private user is one family member. The long-term project is bigger: learn what actually helps at home, then turn those lessons into safety-minded tools, evals, and open patterns for other families.

## One-line pitch

Parker helps people with effortful speech be understood, stay connected, and get useful things done at home — with family-aware safeguards and an OpenClaw/Hermes-style action layer.

## The product thesis

Voice is the main interface and the first wedge, because being understood is the daily pain.

But Parker is not just a phone-call bot, a medication reminder, a transcription app, or a voice clone demo.

Parker is a system:

```text
Understand -> Confirm -> Act -> Follow up -> Escalate/Coordinate -> Learn
```

It should eventually combine:

- voice-first interaction for people with variable or effortful speech;
- repair under uncertainty instead of forcing endless repetition;
- simple visual/TV confirmation cards when voice alone is not enough;
- room/context awareness with explicit privacy boundaries;
- family/caregiver routing and escalation;
- useful actions through an OpenClaw/Hermes-style tool layer;
- structured evals that measure whether the system actually helps.

## Why this matters

Most assistants assume the user speaks clearly, holds a phone, looks at a screen, and can easily correct mistakes.

Parker assumes a different reality:

- speech may be soft, delayed, slurred, quiet, inconsistent, or tiring;
- the person may be in a recliner, near a TV, or far from a screen;
- family and caregivers are part of the workflow;
- the assistant has to follow through, not merely answer;
- uncertainty needs repair, confirmation, and sometimes escalation.

The goal is not to replace family. The goal is to help the person be understood, support routines, reduce avoidable family load, and make useful actions easier.

## What Parker should do

### 1. Understand effortful speech

Parker listens for intent, not just clean dictation.

When uncertain, it repairs:

- “I heard two possibilities. Did you mean A or B?”
- “Do you want me to call Mom, remind you later, or write this down?”
- “I’m not sure enough to send that. Can I show choices?”

The product advantage is not perfect recognition. The advantage is what Parker does when recognition is imperfect.

### 2. Confirm before acting

Parker should not act on ambiguous input.

Before sending a message, escalating to family, changing a schedule, or starting a non-trivial task, Parker should confirm the intent in a low-friction way.

For v0, only reversible or low-risk actions should be executable without a human/operator step.

### 3. Take useful actions

Parker builds on the OpenClaw/Hermes idea: an assistant with context, tools, and a purpose.

Useful action classes include:

- create reminders;
- prepare appointment notes;
- send confirmed family/caregiver messages;
- start speech or movement exercises;
- launch YouTube videos or playlists;
- research a topic and summarize it;
- look up items on Amazon or similar sites, without purchasing;
- check calendars and routines;
- trigger approved smart-home actions;
- log completed routines;
- escalate when behavior/non-response suggests help may be needed.

### 4. Understand home context carefully

A future Parker may use camera/room context, but only with explicit family-approved settings, minimal retention, and clear purpose.

Possible context signals:

- the user is in the recliner;
- the TV is on;
- a reminder has not received a response;
- the user may be trying to get up;
- the user is near the medication area;
- the user is engaged with or ignoring a prompt.

This must be privacy-first. No surveillance theater. No hidden data collection.

### 5. Coordinate with family

Parker is not single-player software.

It should model:

- primary user;
- spouse/caregiver;
- children/support people;
- emergency contacts;
- escalation preferences;
- who can see what;
- who gets notified for which situations;
- which actions require confirmation.

Longer term, Parker should be able to coordinate with other family agents, including Pras’s Hermes/OpenClaw-style assistants.

### 6. Keep life interesting

Usefulness is not only safety/routines.

Parker should help with fun and agency:

- make a YouTube playlist;
- play music;
- find a speech exercise video;
- research a subject the user is curious about;
- help with hobbies;
- prepare a story, message, or question for family;
- suggest a low-friction activity at the right time.

## Naming and repo map

Current repo path:

```text
~/Development/personal/parkinsons-assistant
```

Working product name:

```text
Parker
```

Legacy/internal name, now fully retired from the codebase (only historical docs mention it):

```text
ParkinsClaw
```

Useful related project areas:

- private Parker application: this repo;
- public eval/tooling candidates: `variable-speech-agent-evals`, `assistive-agent-evals-*`, or similar under `~/Development/open-source`;
- research and pitch notes: `~/Knowledge/parker` or `~/Knowledge/research/personal-brand/parker`;
- run logs/manifests/reviews: `~/Operations`.

## Current repo state (v0, pilot-ready)

The local v0 loop works end to end with no external services and no real sends:

- **Input ladder** — typed (`make repl`), scripted demo (`make demo`), audio file (`make demo-voice AUDIO=…`), live microphone (`make talk`), continuous voice conversation (`make talk-loop`). Voice transcription is fully on-device (faster-whisper); no audio is retained beyond the input file.
- **Capture → resolve → stage → confirm → execute pipeline** — every action confirmed before execution; v0's executable surface is reminders and *local-only* family messages.
- **Repair under uncertainty** — ambiguous effortful speech gets 2 numbered choices plus "none of these". With `ANTHROPIC_API_KEY` set, choices are model-generated and grounded in the utterance (claude-haiku); without it, a deterministic fallback keeps everything working.
- **Family message outbox with two human gates** — patient confirms → `queued_local` → caregiver approves → `approved_local`. There is **no send path in the codebase at all**; cancel works from either state.
- **Caregiver review page** — `/parker/review/ui` aggregates everything awaiting a human decision, with confirm/execute/cancel/approve buttons and opt-in HTTP Basic auth (`DASHBOARD_PASSWORD`).
- **Non-response escalation candidates** — review-only, never auto-dispatched.
- **Eval harness** — task-taxonomy eval (`make eval-tasks`, 0 safety-critical misses), interactivity trace eval (`make eval-interactivity`, 7 synthetic scenarios / 0 unsafe misses), Parker-generated demo trace eval (`make eval-demo-interactivity`, 7/7 current-product synthetic scenarios / 0 unsafe misses after cancel-only draft/outbox steering landed), degraded-input replay eval (`make eval-degraded-input-replay`, Parker repair 100% vs. no-repair 0% on 3 synthetic held-out transcript fixtures), and repair-choice quality spot-check (`make eval-repair`).
- 242 backend tests as of the latest cancel-only draft/outbox steering QA pass (2026-06-18).

Some inert legacy modules from an earlier phone-call prototype remain (`calls/`, `voice/stream.py`, `meds/`); they are not wired into the v0 demo path.

## Stack

| Layer | v0 (shipped) | Possible later |
| --- | --- | --- |
| Backend | Python 3.11 / FastAPI | — |
| Storage | SQLite (`backend/parker.db`) | — |
| Speech-to-text | faster-whisper, fully on-device, optional dep | voice-activity end-pointing |
| Repair choices | claude-haiku (opt-in via `ANTHROPIC_API_KEY`), deterministic fallback | multi-turn grounding |
| Family/caregiver view | `/parker/review/ui` single-file page, opt-in Basic auth | richer dashboard |
| Eval harness | task-taxonomy eval + reference/Parker-generated interactivity trace evals + degraded-input replay + repair-quality spot-check | human-graded repair content |
| Voice/calls | none in v0 (no send path exists) | Twilio, realtime models |
| TTS/voice clone | none in v0 | optional, consent-gated only |

## Setup

The backend standardizes on Python 3.11 in `backend/.venv`.

```bash
make backend-venv    # venv + deps
make test            # full backend suite should pass (242 tests as of 2026-06-18)
```

**Fastest demo** (three commands, zero config):

```bash
make demo            # fresh DB + seeded family day + effortful-speech replay
make run             # uvicorn on http://localhost:8000
# open http://localhost:8000/parker/review/ui as the caregiver
```

**Talk to it** (optional on-device voice):

```bash
make voice-deps      # faster-whisper + sounddevice, local only
make talk-loop       # continuous voice conversation, Ctrl-C to stop
```

For a real pilot — env file, model-enhanced repair choices, review-page password — follow **"Pilot setup"** in [docs/runbook.md](docs/runbook.md). Copy `backend/.env.example` to `backend/.env`; never commit real `.env` files or secrets.

## Safety boundaries

Parker is healthcare-adjacent but is not a clinician.

Parker must not:

- diagnose;
- recommend treatment;
- make medication changes;
- make medical decisions;
- replace emergency services;
- send messages or escalate without the configured confirmation/escalation policy;
- store raw sensitive audio/video by default;
- use cloned voices without explicit consent;
- purchase items or make irreversible external actions without human approval.

Parker may support:

- reminders;
- routine follow-through;
- appointment preparation;
- communication repair;
- family/caregiver coordination;
- safe entertainment/education actions;
- user-approved notes and summaries;
- evals and logs that help the family understand what works.

## Evaluation agenda

Parker should be evaluated by usefulness, not demo sparkle.

Important eval categories:

- effortful-speech intent understanding;
- uncertainty calibration;
- clarification quality;
- confirmation-before-action behavior;
- changed-mind/interruption handling;
- latency/turn budget under safety gates;
- caregiver/operator state legibility;
- family escalation precision/noise;
- reminder follow-through;
- appointment-note quality;
- YouTube/research/action relevance;
- privacy/safety boundary adherence;
- whether the user wants to use it again.

Synthetic data first. No real patient audio or private family data in public artifacts without explicit approval.

## Where to start reading

- [docs/runbook.md](docs/runbook.md) — scripted walkthrough of everything v0 does, plus pilot setup.
- [docs/next-slices.md](docs/next-slices.md) — the implementation log: every shipped slice with rationale and what was deliberately deferred.
- `AGENTS.md` and `CLAUDE.md` — read before running coding agents in this repo.

## License

Personal project. Not licensed for distribution.
