# Parker - Claude Code context

You are working in `~/Development/personal/parkinsons-assistant`.

## Product vision

Parker is a personal assistant that actually understands people with Parkinson's — and gets real things done for them, with family curating what it can do.

Role split (load-bearing): the person with Parkinson's is the *user* — voice is their whole interface, zero configuration. Family members are the *administrators and skill builders* — they connect accounts, curate/approve skills, and own guardrails through the review surfaces. Think OpenClaw-style agent, family-administered.

North Star metric: understood on the first try or after one repair question ≥90% of the time (stock voice assistants sit near 50% for the pilot user). Measured by the real-audio eval harness (`make eval-audio-real`), later on consented pilot voice samples.

Voice is the main interface and the first wedge. Parker's broader thesis is variable-speech understanding, repair under uncertainty, safe action, follow-through, family coordination, per-user learning from consented local usage data, and eval-backed usefulness. The project's trajectory is public: attract other families and developers so more deployment and more usage improve the shared harness, evals, and skills — never a central model trained on anyone's voice without consent.

Live-loop direction: local-first ASR stays the default; families may opt into frontier realtime speech models (OpenAI Realtime / gpt-realtime family) for the conversational loop as an explicit administrator choice.

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

Release/update rule: Parker is a living public project. Commit/push ready milestones with green tests/evals and current docs; do not wait for Pras for in-scope ready work, but do not push midstream half-finished slices.

## Multi-session working agreement

Multiple Claude Code sessions (plus a Hermes agent) may share this working
directory simultaneously. Rules for every agent session:

- On start, check `git fetch && git status`. If the tree is dirty with
  changes you did not make, STOP and ask — another live session may own
  them. Never stash, hard-reset, discard, or commit someone else's work.
- Do all non-trivial work on a feature branch (one branch per session);
  merge to main only with a clean tree after a fresh `git pull --ff-only`.
- Destructive git commands (hard resets, stashing, checkout/restore
  discards, forced cleans, force pushes, autostash rebases, forced branch
  deletion) are denied by the PreToolUse hook in `.claude/settings.json`.
  If you hit the block, the answer is coordination, not workaround — a
  human can always run the command manually. Note: the guard matches the
  whole Bash command string, so avoid quoting those literal git
  incantations inside commit messages or heredocs.
- The SessionStart hook prints the shared-checkout state; read it before
  touching anything.

## Workspace contract

- Code/docs tied to implementation: this repo under `~/Development`.
- Research/vision/pitch synthesis: `~/Knowledge/parker` or `~/Knowledge/research/personal-brand/parker`.
- Run manifests/reviews/logs: `~/Operations`.

## Preferred Fable 5 work style

Do discovery first, then design, then small code/docs/eval changes.

Do not broad rewrite. Do not overbuild enterprise healthcare infrastructure. This is one-family v0 first, with a public eval/tooling path later.

Lead final responses with TLDR, files changed, tests run, and next recommended implementation slice.
