# Agent instructions for Parker

This repo is the living public application/prototype for Parker, a family-aware at-home assistant for effortful speech, Parkinson's-adjacent routines, family coordination, and safe action.

## Product north star

Parker helps people with effortful speech be understood, stay connected, and get useful things done at home.

Voice is the main interface and the first wedge, but Parker is a system, not just a call bot:

```text
Understand -> Confirm -> Act -> Follow up -> Escalate/Coordinate -> Learn
```

Build on OpenClaw/Hermes patterns: context, tools, hands/eyes, and purpose. The core assistant should understand the user and home/family context, then call safe tools or escalate appropriately.

## Do not anchor on stale framing

Older docs/code may say ParkinsClaw is primarily a scheduled phone-call product using a cloned family voice. That is historical context, not the current product vision.

Voice cloning may be optional with explicit consent. It is not the core value proposition.

Use the product name Parker in new docs unless referring to legacy filenames/code.

## Safety boundaries

Parker must not:
- diagnose;
- recommend treatment;
- make medication changes;
- make medical decisions;
- replace emergency services;
- send external messages or escalate without configured confirmation/escalation policy;
- store raw sensitive audio/video by default;
- use cloned voices without explicit consent;
- purchase items or take irreversible external actions without human approval.

Parker may support:
- reminders and routine follow-through;
- appointment preparation;
- communication repair;
- family/caregiver coordination;
- safe entertainment/education actions;
- YouTube playlists/research/item search without purchasing;
- user-approved notes and summaries.

## Folder contract

This repo belongs in `~/Development` because it is code/code-adjacent.

Use:
- `~/Knowledge/parker` or `~/Knowledge/research/personal-brand/parker` for research, pitch notes, and product synthesis;
- `~/Operations` for run manifests, reviews, and agent logs;
- this repo for code, repo docs, tests, fixtures, and implementation plans tied directly to code.

## Release/update rules

Parker should be continuously updated, tested, released, and made talk-about-able.

Agents should not wait for Pras to manually request commits when a slice is genuinely ready. Commit/push when there is a coherent milestone, green tests/evals, and docs that match the real state. Do not push midstream half-finished work just to reduce a dirty tree.

A Parker change is ready when:
1. the behavior/docs form a coherent feature, eval, release, or public-positioning milestone;
2. relevant backend tests and evals pass, or failures are explicitly understood and documented;
3. README/docs/report files describe what actually works now, not aspirations;
4. safety boundaries are still explicit for external actions, medical claims, privacy, and family escalation;
5. the public surface is something Pras can confidently talk about.

If work is promising but not ready, leave it on a branch with a short status note in `docs/next-slices.md` rather than pushing to `main`.

## Coding-agent rules

Before broad changes:
1. Read README.md, AGENTS.md, CLAUDE.md, REVIEW-PREP-2026-06-03.md, docs/architecture.md, benchmark/README.md, backend/app/parker/pipeline.py, backend/app/escalation/, backend/app/memory/, backend/app/exercises/, backend/app/conversation/, backend/tests/.
2. Map actual code state vs product vision.
3. Preserve current passing tests unless intentionally changing behavior with updated tests.
4. Add evals/tests before expanding action capability.
5. Keep irreversible/external actions behind explicit approval boundaries.
6. Do not read `.env` or secrets.
7. Do not send real messages/calls, make purchases, or use live APIs unless explicitly instructed.

## Preferred next build direction

Tax expensive coding agents on architecture/eval work first:
- system map;
- capability taxonomy;
- action protocol;
- confirmation/escalation policy;
- synthetic task/eval harness;
- implementation slices with acceptance tests.

Avoid a broad rewrite. Convert vision into executable seams, tests, and docs.
