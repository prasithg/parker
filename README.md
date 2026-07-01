# Parker

**Parker is a personal assistant that actually understands people with Parkinson's — and gets real things done for them, with family curating what it can do.**

My dad uses his voice for almost everything — typing is hard, and getting harder. His Google Home understands him about half the time and can't hold a conversation. Parker is the assistant he deserves: it understands him on his hard days, asks instead of guessing when it isn't sure, and does real things — reminders, messages to family, playlists, research — while his family adds skills and keeps guardrails. Think of it as an OpenClaw-style agent setup where the family are the administrators and he is simply the user.

The first user is one family member. The goal is bigger: every person with Parkinson's should be able to have a personal agent that learns *their* voice and *their* mannerisms — locally, with consent — and gets better the more they use it. If this works for one family, the same harness, evals, and patterns should work for yours.

## Who does what

- **The user** talks. That's the whole interface. No configuration, no dashboards, no typing.
- **The family administrator** curates: connects accounts, adds and approves skills, reviews anything risky on the caregiver page, and owns the guardrails.
- **Parker** understands, repairs, and confirms — then acts only within what the administrator has enabled.

## The North Star

> Understood on the first try, or after one repair question, **at least 90% of the time** — versus roughly 50% for a stock voice assistant today.

This is measured, not vibes: a real-audio eval harness runs recorded dysarthric and Parkinson's speech through the actual pipeline and reports intent recovery with and without Parker's repair protocol (`make eval-audio-real`). Current numbers live in [benchmark/reports/](benchmark/reports/).

## The core loop

```text
Understand -> Confirm -> Act -> Follow up -> Escalate/Coordinate -> Learn
```

The product advantage is not perfect recognition. The advantage is what Parker does when recognition is imperfect:

- "I heard two possibilities. Did you mean A or B?"
- "I'm not sure enough to send that. Can I show choices?"
- Never acting on ambiguous input; confirming before anything runs; keeping every v0 action reversible.

## What using Parker should feel like

Today (working locally): "Remind me to water the plants this evening." "Tell Sarah the physio visit went well." "Start my speech exercise." — captured, confirmed, staged for family approval where appropriate.

Where it's headed (family-curated skills): "What's this video about? Make me a playlist like it." "I want to look at homes in the Leander area — find a few and open them on my computer." "When is my next appointment, and what should I remember to ask?"

## How Parker learns (the flywheel)

Every repair exchange is a naturally labeled example: what ASR heard, what Parker offered, what the person confirmed. With explicit consent (off by default, pinned off by test), Parker stores those exchanges **locally** and climbs the [adaptation ladder](docs/adaptation-ladder.md):

1. a **personal lexicon** (family names, daily words) that biases speech recognition immediately;
2. **n-best repair choices** — alternate hypotheses become concrete options, so "Tell Sarah…" survives being misheard as "There a…";
3. few-shot exemplars from that person's history, and eventually a per-user fine-tune corpus.

No raw audio is ever stored. The person's data stays in their house. Deploying Parker to more people improves the shared harness, evals, and skills — not a central model trained on anyone's voice without asking.

## Powering the voice loop

Local-first is the default: on-device Whisper (faster-whisper), no cloud required, transcripts as the only artifact. For the live conversational experience, families can opt into frontier realtime speech models (e.g., the OpenAI Realtime / gpt-realtime family — a bridge already exists in `voice/stream.py`) when latency and conversational quality matter more than full local processing. That is an explicit administrator choice with documented trade-offs, never a silent default; stored data stays local either way.

## Why this matters

Most assistants assume the user speaks clearly, holds a phone, looks at a screen, and can easily correct mistakes. Parker assumes a different reality:

- speech may be soft, delayed, slurred, quiet, inconsistent, or tiring;
- the person may be in a recliner, near a TV, or far from a screen;
- family and caregivers are part of the workflow;
- the assistant has to follow through, not merely answer;
- uncertainty needs repair, confirmation, and sometimes escalation.

The goal is not to replace family. The goal is to help the person be understood, support routines, reduce avoidable family load, and keep life interesting — playlists, research, hobbies, and messages, not just medication-adjacent chores.

## For developers and families

This is a living public project and it wants collaborators:

- **Families**: the [runbook](docs/runbook.md) walks through running the local demo end to end with zero external services, and the [pilot recording protocol](docs/pilot-recording-protocol.md) shows how to (consensually) measure Parker against your person's actual voice.
- **Developers**: the eval harness is the front door. Every claim in this README maps to a runnable eval; `make test` (360 tests) plus `make eval-grant-readiness` reproduces the evidence. The action layer is deliberately small and policy-gated — adding a skill means adding it to the taxonomy with its safety tier, not bolting on a webhook.
- **Researchers**: fixtures derived from public dysarthria corpora (TORGO, EasyCall, SJTU, and others) are metadata-only in-repo; the harness design and construct-validity guards are documented in [benchmark/README.md](benchmark/README.md).

## Naming and repo map

Working product name: **Parker**. (An earlier prototype was called ParkinsClaw; that name and its scheduled-call/cloned-voice framing are retired — only historical docs mention them.)

- Parker application/prototype: this repo;
- research and pitch notes: kept outside the public code repo until ready to publish;
- run logs/manifests/audio artifacts: operational workspace, never committed here.

## Current repo state (v0, pilot-ready)

The local v0 loop works end to end with no external services and no real sends:

- **Input ladder** — typed (`make repl`), scripted demo (`make demo`), audio file (`make demo-voice AUDIO=…`), live microphone (`make talk`), continuous voice conversation (`make talk-loop`). Voice transcription is fully on-device (faster-whisper); no audio is retained beyond the input file.
- **Capture → resolve → stage → confirm → execute pipeline** — every action confirmed before execution; v0's executable surface is reminders, local exercise sessions, and *local-only* family messages.
- **Repair under uncertainty** — ambiguous effortful speech gets 2 numbered choices plus "none of these". With `ANTHROPIC_API_KEY` set, choices are model-generated and grounded in the utterance (claude-haiku); without it, a deterministic fallback keeps everything working. Alternate ASR hypotheses (n-best) become evidence-based choices that carry their parsed recipient/subject.
- **Learning flywheel v0** — consent-gated repair-event capture (`REPAIR_EVENT_CAPTURE_CONSENTED`, default off), personal lexicon ASR biasing (`PERSONAL_LEXICON`), documented in [docs/adaptation-ladder.md](docs/adaptation-ladder.md).
- **Local recliner/TV evening loop** — one `local_evening_sessions` row per calendar evening; optional offer/decline, unclear-response repair choices, engaged/completed/timed-out states, and caregiver review controls.
- **Family message outbox with two human gates** — patient confirms → `queued_local` → caregiver approves → `approved_local`. There is **no send path in the codebase at all**; cancel works from either state.
- **Caregiver review page** — `/parker/review/ui` aggregates everything awaiting a human decision, with opt-in HTTP Basic auth (`DASHBOARD_PASSWORD`).
- **Non-response escalation candidates** — review-only, never auto-dispatched.
- **Real-audio eval harness** — `make eval-audio-real` runs real public-corpus and synthetic clips (audio stays in the Operations workspace, never in-repo) through local ASR and the actual routing, scored against each clip's oracle transcript: intent recovery with/without repair and with/without n-best, unsafe-capture gate, per-condition/language breakdowns.
- **Synthetic eval suite** — task-taxonomy eval (`make eval-tasks`, 24 fixtures / 0 safety-critical misses including medical/medication/emergency/privacy/purchase red-team cases), interactivity trace evals (`make eval-interactivity`, `make eval-demo-interactivity`), degraded-input replay (`make eval-degraded-input-replay`), audio Autodata metadata fixtures (`make eval-audio-autodata`, 29 fixtures / 23 hard negatives / 0 unsafe), caregiver-state legibility proxy, claim→metric overclaim guard, construct-validity matrix guard, public-source citation guard, grant-readiness rollup, and repair-choice quality spot-check.
- 360 backend tests as of the n-best repair + flywheel slice (2026-07-01).

Some inert legacy modules from an earlier phone-call prototype remain (`calls/`, `voice/stream.py`, `meds/`); they are not wired into the v0 demo path.

## Stack

| Layer | v0 (shipped) | Possible later |
| --- | --- | --- |
| Backend | Python 3.11 / FastAPI | — |
| Storage | SQLite (`backend/parker.db`), all local | — |
| Speech-to-text | faster-whisper on-device, personal-lexicon biasing, optional dep | realtime cloud speech (family opt-in), voice-activity end-pointing |
| Repair choices | n-best hypothesis probing + claude-haiku (opt-in), deterministic fallback | few-shot from consented repair history |
| Learning | consent-gated repair-event capture, personal lexicon | mined lexicon suggestions, per-user fine-tunes |
| Family/caregiver view | `/parker/review/ui` single-file page, opt-in Basic auth | richer admin/skills dashboard |
| Eval harness | real-audio harness + full synthetic suite (see above) | pilot-voice longitudinal tracking, human-graded repair quality |
| Voice out / live loop | none in v0 (no send path exists) | TTS, wake/VAD, realtime models (gpt-realtime family) |

## Setup

The backend standardizes on Python 3.11 in `backend/.venv`.

```bash
make backend-venv    # venv + deps
make test            # full backend suite should pass (360 tests as of 2026-07-01)
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

## Operating cadence

Parker is a living public project. The repository should move when a meaningful feature, eval, demo, or documentation milestone is ready — not while a slice is half-finished, and not only after manual prompting.

Ready-to-publish work should include:

- tests or evals that prove the changed behavior;
- README/docs updates that describe the real current state;
- safety notes for any new action surface;
- public-facing language suitable for posts, demos, and grant/research conversations.

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

- effortful-speech intent understanding (now measured on real audio);
- uncertainty calibration and clarification quality;
- confirmation-before-action behavior;
- changed-mind/interruption handling;
- latency/turn budget under safety gates;
- caregiver/operator state legibility;
- family escalation precision/noise;
- reminder follow-through;
- action relevance (playlists, research, appointments);
- privacy/safety boundary adherence;
- whether the user wants to use it again.

Synthetic and public-corpus data first. No real patient audio or private family data in public artifacts without explicit approval; pilot voice samples follow the consent terms in [docs/pilot-recording-protocol.md](docs/pilot-recording-protocol.md).

## Where to start reading

- [docs/runbook.md](docs/runbook.md) — scripted walkthrough of everything v0 does, plus pilot setup.
- [docs/adaptation-ladder.md](docs/adaptation-ladder.md) — how Parker learns a person's voice, and what it refuses to collect.
- [docs/next-slices.md](docs/next-slices.md) — the implementation log: every shipped slice with rationale and what was deliberately deferred.
- `AGENTS.md` and `CLAUDE.md` — read before running coding agents in this repo.

## License

Not yet licensed for distribution. Open-sourcing for other families and developers is the stated goal; a license will be chosen before the public launch.
