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

Legacy/internal name that may still appear in older files:

```text
ParkinsClaw
```

Use “Parker” in user-facing docs unless referring to historical code/files.

Useful related project areas:

- private Parker application: this repo;
- public eval/tooling candidates: `variable-speech-agent-evals`, `assistive-agent-evals-*`, or similar under `~/Development/open-source`;
- research and pitch notes: `~/Knowledge/parker` or `~/Knowledge/research/personal-brand/parker`;
- run logs/manifests/reviews: `~/Operations`.

## Current repo state

This repo currently contains a Python/FastAPI backend scaffold with:

- call scheduling/handling modules;
- conversational agent prompts/tools;
- medication/routine tracking;
- memory/capture storage;
- escalation modules;
- exercise modules;
- Parker capture -> resolve -> stage -> resurface pipeline;
- synthetic benchmark scaffold for transcript -> structured intent/safety JSON;
- tests around the current backend behavior.

The existing code still reflects an earlier phone-call/voice-clone framing in some modules and docs. Treat that as historical context, not the product north star.

## Stack

| Layer | Current/possible tech |
| --- | --- |
| Backend | Python / FastAPI |
| Storage | SQLite for v0 |
| Voice/calls | Twilio and/or local voice interface |
| Realtime model | OpenAI Realtime or comparable realtime multimodal model |
| TTS/voice | Optional; consent required for any cloned voice |
| Family/operator view | Dashboard/API, currently scaffolded |
| Eval harness | Synthetic transcript/task-card benchmark |
| Action layer | OpenClaw/Hermes-style tools and agent handoff protocols |

## Setup

The backend standardizes on Python 3.11 in `backend/.venv`.

```bash
# Backend
make backend-venv
make test
make run
```

Manual backend flow:

```bash
cd backend
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload
```

Do not commit real `.env` files or secrets.

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
- family escalation precision/noise;
- reminder follow-through;
- appointment-note quality;
- YouTube/research/action relevance;
- privacy/safety boundary adherence;
- whether the user wants to use it again.

Synthetic data first. No real patient audio or private family data in public artifacts without explicit approval.

## Near-term Fable 5 task

The best next long-horizon coding-agent task is not to build all of Parker.

It is to produce a repo-grounded system architecture and eval/action protocol that reconciles:

- current code;
- updated Parker vision;
- OpenClaw/Hermes action layer;
- family escalation;
- voice-first interface;
- future room/TV context;
- safety boundaries;
- a concrete implementation plan.

See `AGENTS.md` and `CLAUDE.md` before running Claude Code.

## License

Personal project. Not licensed for distribution.
