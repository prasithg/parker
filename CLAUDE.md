# Parker - Claude Code context

You are working in `~/Development/personal/parkinsons-assistant`.

## Product vision

Parker is a family-aware, room-aware, action-capable home assistant for people whose speech, routines, movement, and support needs are changing.

One-line pitch:

Parker helps people with effortful speech be understood, stay connected, and get useful things done at home — with family-aware safeguards and an OpenClaw/Hermes-style action layer.

Voice is the main interface and the first wedge. But Parker is not just a transcription app, a phone-call bot, a medication reminder, or a voice clone demo.

Core loop:

```text
Understand -> Confirm -> Act -> Follow up -> Escalate/Coordinate -> Learn
```

## Important correction

Older repo docs/code may frame the project as “ParkinsClaw,” scheduled outbound calls, and cloned family voice. Treat that as legacy/historical context. Do not make voice cloning the main product thesis.

Voice cloning may be optional and requires explicit consent. The current value prop is understanding, repair, action, follow-through, family coordination, and eval-backed usefulness.

## Architecture direction

Parker should build on OpenClaw/Hermes patterns:
- context/memory about the user, family, routines, and preferences;
- tools/hands for safe actions;
- eyes/context later for room/TV/recliner workflows;
- purpose: help the user be understood and supported at home;
- family network: escalate, summarize, coordinate, and hand off when appropriate.

Useful action classes:
- reminders and routines;
- family/caregiver messages after confirmation;
- appointment preparation;
- speech/movement exercises;
- YouTube playlists and educational videos;
- research summaries;
- item search such as Amazon lookup, without purchasing;
- smart-home actions only when safe and approved.

## Safety boundaries

Never implement behavior that:
- diagnoses;
- recommends treatment;
- changes medication;
- makes medical decisions;
- replaces emergency services;
- sends external messages/escalations without policy and confirmation;
- stores raw sensitive audio/video by default;
- purchases items or takes irreversible external actions without human approval;
- reads `.env`, credentials, secrets, private keys, cookies, or tokens.

## Current repo reality

The repo contains a Python/FastAPI backend with modules for calls, conversation, medication tracking, memory, escalation, exercises, Parker capture/resolve/stage/resurface pipeline, and a synthetic benchmark scaffold.

The next valuable work is to align repo docs, architecture, task taxonomy, and eval scaffolding around the updated system vision before expanding features.

## Commands

```bash
make backend-venv
make test
make run
```

Run tests before final response if you modify code.

## Workspace contract

- Code/docs tied to implementation: this repo under `~/Development`.
- Research/vision/pitch synthesis: `~/Knowledge/parker` or `~/Knowledge/research/personal-brand/parker`.
- Run manifests/reviews/logs: `~/Operations`.

## Preferred Fable 5 work style

Do discovery first, then design, then small code/docs/eval changes.

Do not broad rewrite. Do not overbuild enterprise healthcare infrastructure. This is one-family v0 first, with a public eval/tooling path later.

Lead final responses with TLDR, files changed, tests run, and next recommended implementation slice.
