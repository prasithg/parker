# Parker

Parker is a family-aware, at-home assistant research prototype for people with effortful speech and daily routines that benefit from extra support.

The goal is not “an agent that calls you.” Parker is meant to understand what someone is trying to do, confirm uncertain intent, help with safe routine follow-through, coordinate with trusted family/caregivers when appropriate, and learn from measurable outcomes.

## Current product thesis

Most assistants assume the user speaks clearly, can easily look at a screen, and can quickly correct mistakes. Parker starts from the opposite constraint: speech may be variable, attention and energy may fluctuate, and the safest helpful action is often to slow down, confirm, stage, and follow up.

Parker focuses on:

- effortful-speech interaction and repair;
- reminders and routine follow-through;
- family/caregiver coordination with explicit confirmation boundaries;
- safe local actions before external actions;
- synthetic evals that test whether the system actually helps instead of merely sounding helpful.

## Interaction model

```text
Understand -> Confirm -> Stage -> Act -> Follow up -> Escalate/Coordinate -> Learn
```

Examples of desired behavior:

- “I heard two possibilities. Did you mean A or B?”
- “I can remind you later, write this down, or ask someone for help. Which should I do?”
- “I’m not confident enough to send that yet. Can I show choices first?”
- “This sounds urgent. I can help contact a configured caregiver, but I am not emergency services.”

## Safety boundaries

Parker is not a medical device and does not diagnose, recommend treatment, change medication, replace clinicians, or replace emergency services.

The prototype is designed around conservative action boundaries:

- no medical decisions;
- no medication changes;
- no purchases or irreversible external actions without human approval;
- no automatic external messages without configured confirmation/escalation policy;
- no raw sensitive audio/video retention by default;
- optional voice-cloning or synthetic voice features only with explicit consent.

## What exists now

This repo contains a Python/FastAPI backend with local demo paths, staged action handling, simple routine/exercise flows, and a synthetic benchmark scaffold.

Current implementation emphasis:

- typed and voice-adjacent local demos;
- capture -> resolve -> stage -> confirm -> execute pipeline;
- local-only action surfaces for reminders, exercise/session flows, and staged family-message demos;
- eval reports for task taxonomy, degraded-input replay, caregiver-state legibility, claim-to-metric overclaim checks, construct validity, public-source citation grounding, and repair-quality review.

Some older modules from an early phone-call prototype remain in the tree. They are legacy/inert unless explicitly wired into a future consent-gated flow.

## Repository map

```text
backend/      FastAPI app, Parker pipeline, routine/exercise flows, tests
benchmark/    Synthetic fixtures, evaluation scripts, generated reports
docs/         Architecture notes and runbooks
```

## Local development

```bash
python3 -m pip install -r backend/requirements.txt
python3 -m pytest backend/tests -q
```

Selected demos/evals are exposed through the Makefile where dependencies are installed:

```bash
make repl
make eval-tasks
make eval-interactivity
```

## Public artifact policy

Public artifacts should use synthetic data or consent-cleared examples only. Do not commit real patient audio, private family data, credentials, tokens, or raw transcripts from real users.

## Direction

The next useful work is not a broader chatbot. It is tighter evidence:

- clearer task taxonomy;
- stronger speech-repair evals;
- explicit confirmation/escalation policy;
- caregiver-state legibility tests;
- product demos that show Parker helping safely under ambiguity.
