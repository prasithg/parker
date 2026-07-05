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

## Bring your own brain

Parker itself is the brainstem — the ear, the mouth, the repair questions, and the policy gate that owns every action. The thing that actually *converses* is a pluggable brain behind one small contract ([docs/brain-adapters.md](docs/brain-adapters.md)): with an `ANTHROPIC_API_KEY` configured, Claude answers the everyday questions a Google Home fumbles today — what day it is, how long rice takes, a follow-up about Saturday — in one to three spoken sentences, with the medical boundary enforced in code *after* every reply, not just requested in the prompt. The brain never sees an utterance the safety guards refused, and it cannot set a reminder or send a message; it can only propose, and a proposal comes back as a confirmation choice through the same pipeline as everything else. Without a key nothing changes: the answer lane stays a deterministic stub and every test and eval runs offline. The v1 brain is the family's own OpenClaw agent: implemented against the documented gateway contract (OpenAI-compatible chat + a small skill bridge) with a fake gateway in every test — connecting a real patient-identity instance is a documented deployment step in the runbook, and family-enabled skills are the only hands Parker will use.

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
- **Developers**: the eval harness is the front door. Every claim in this README maps to a runnable eval; `make test` (602 tests) plus `make eval-release-readiness` reproduces the evidence. The action layer is deliberately small and policy-gated — adding a skill means adding it to the taxonomy with its safety tier, not bolting on a webhook.
- **Researchers**: fixtures derived from public dysarthria corpora (TORGO, EasyCall, SJTU, and others) are metadata-only in-repo; the harness design and construct-validity guards are documented in [benchmark/README.md](benchmark/README.md).

## Naming and repo map

Working product name: **Parker**. (An earlier prototype was called ParkinsClaw; that name and its scheduled-call/cloned-voice framing are retired — only historical docs mention them.)

- Parker application/prototype: this repo;
- research and pitch notes: kept outside the public code repo until ready to publish;
- run logs/manifests/audio artifacts: operational workspace, never committed here.

## Current repo state (v0, pilot-ready)

The local v0 loop works end to end with no external services and no real sends:

- **Input ladder** — typed (`make repl`), scripted demo (`make demo`), audio file (`make demo-voice AUDIO=…`), live microphone (`make talk`), continuous voice conversation (`make talk-loop`). Voice transcription is fully on-device (faster-whisper); no audio is retained beyond the input file.
- **Capture → resolve → stage → confirm → execute pipeline** — every action confirmed before execution; the local executable surface is reminders, local exercise sessions, and *local-only* family messages, plus policy-gated OpenClaw skills (`media_playlist`, read-only `open_links`) when the family's gateway enables them.
- **Repair under uncertainty** — ambiguous effortful speech gets 2 numbered choices plus "none of these". With `ANTHROPIC_API_KEY` set, choices are model-generated and grounded in the utterance (claude-haiku); without it, a deterministic fallback keeps everything working. Alternate ASR hypotheses (n-best) become evidence-based choices that carry their parsed recipient/subject.
- **Conversational brain (opt-in)** — with `ANTHROPIC_API_KEY`, questions and unmatched conversation route to a pluggable `BrainAdapter` (Claude v0) with bounded follow-up history; brain-proposed actions become confirmation choices, never direct captures, and a deterministic post-response guard re-checks the medical boundary on every reply ([docs/brain-adapters.md](docs/brain-adapters.md)). Keyless, the answer lane stays a deterministic stub.
- **Voice out + latency line** — `make talk-loop` speaks answers aloud (macOS `say`, config-gated), end-points recording with an energy VAD, and prints a per-turn latency line (ASR + routing → when speech starts).
- **Learning flywheel v0** — consent-gated repair-event capture (`REPAIR_EVENT_CAPTURE_CONSENTED`, default off), personal lexicon ASR biasing (`PERSONAL_LEXICON`), documented in [docs/adaptation-ladder.md](docs/adaptation-ladder.md).
- **Local recliner/TV evening loop** — one `local_evening_sessions` row per calendar evening; optional offer/decline, unclear-response repair choices, engaged/completed/timed-out states, and caregiver review controls.
- **Family message outbox with capability-level trust** — the family allowlists contacts once (`PARKER_FAMILY_CONTACTS`); a confirmed message to a listed contact releases on the patient's own yes (`released_local`, visible in review — awareness, not an approval queue), anyone else stays behind per-message approval (`queued_local` → `approved_local`). There is **no send path in the codebase at all**; cancel works from every live state.
- **Caregiver review page** — `/parker/review/ui` aggregates everything awaiting a human decision, with opt-in HTTP Basic auth (`DASHBOARD_PASSWORD`).
- **Non-response escalation candidates** — review-only, never auto-dispatched.
- **Real-audio eval harness** — `make eval-audio-real` runs real public-corpus and synthetic clips (audio stays in the Operations workspace, never in-repo) through local ASR and the actual routing, scored against each clip's oracle transcript: intent recovery with/without repair and with/without n-best, unsafe-capture gate, per-condition/language breakdowns.
- **Synthetic eval suite** — task-taxonomy eval (`make eval-tasks`, 24 fixtures / 0 safety-critical misses including medical/medication/emergency/privacy/purchase red-team cases), interactivity trace evals (`make eval-interactivity`, `make eval-demo-interactivity`), degraded-input replay (`make eval-degraded-input-replay`), audio Autodata metadata fixtures (`make eval-audio-autodata`, 33 fixtures / 26 hard negatives / 0 unsafe), caregiver-state legibility proxy, claim→metric overclaim guard, construct-validity matrix guard, release-readiness rollup, repair-choice quality spot-check, brain-lane safety eval (`make eval-brain-lane`, keyless red-team routing gate + live TTS/quality lane, unsafe as a hard 0), and the hands lane (`make eval-hands`, proposal → patient confirmation → skill execution over a fake OpenClaw gateway incl. off-allowlist/unknown-type/gateway-error edges, 8/8 with unsafe as a hard 0).
- 603 backend tests as of the SLURP music/media repair slice (2026-07-05).

Some inert legacy modules from an earlier phone-call prototype remain (`calls/`, `voice/stream.py`, `meds/`); they are not wired into the v0 demo path.

## Stack

| Layer | v0 (shipped) | Possible later |
| --- | --- | --- |
| Backend | Python 3.11 / FastAPI | — |
| Storage | SQLite (`backend/parker.db`), all local | — |
| Speech-to-text | faster-whisper on-device, personal-lexicon biasing, optional dep | realtime cloud speech (family opt-in), voice-activity end-pointing |
| Repair choices | n-best hypothesis probing + claude-haiku (opt-in), deterministic fallback | few-shot from consented repair history |
| Learning | consent-gated repair-event capture, personal lexicon | mined lexicon suggestions, per-user fine-tunes |
| Conversational brain | `BrainAdapter` contract; Claude v0 (`claude-sonnet-5`, opt-in via `ANTHROPIC_API_KEY`), OpenClaw gateway adapter + skill execution seam (fake-gateway tested; real instance is a deployment step), deterministic stub keyless | realtime speech models (family opt-in) |
| Family/caregiver view | `/parker/review/ui` single-file page, opt-in Basic auth | richer admin/skills dashboard |
| Eval harness | real-audio harness + full synthetic suite (see above) | pilot-voice longitudinal tracking, human-graded repair quality |
| Voice out / live loop | macOS `say` TTS + energy-VAD end-pointing in `make talk-loop`, per-turn latency line; no external send path exists | wake word, realtime models (gpt-realtime family) |

## Parker as an app (beta)

Parker is installable as a macOS menu-bar app — drag a dmg to
Applications, no Python, no terminal. A Tauri v2 shell bundles the whole
engine as a sidecar binary; onboarding is a guided wizard (mic
permission, voice picker, plain-language consent, one-time local
speech-model download), and daily use is a tray menu: Start/Pause
Listening, the Dad Screen, Family Review, the Daily Digest. Unsigned
beta, Apple silicon; acceptance-tested end-to-end from the dmg,
including a spoken conversation confirmed with a spoken "Yes, go
ahead". Build it with `make sidecar && cd desktop/src-tauri && cargo
tauri build`; the full lifecycle (install → onboard → update →
uninstall) is in [docs/desktop.md](docs/desktop.md), the architecture
in [docs/desktop-architecture.md](docs/desktop-architecture.md).

## Setup

The backend standardizes on Python 3.11 in `backend/.venv`.

```bash
make backend-venv    # venv + deps
make test            # full backend suite should pass (602 tests as of 2026-07-04)
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
- public-facing language suitable for posts, demos, and research conversations.

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

[MIT](LICENSE). Parker is not a medical device and provides no medical advice; the safety boundaries above are design commitments, not clinical claims.
