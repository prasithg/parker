# Agent instructions for Parker

Parker is a voice-first, family-administered personal assistant that understands people with Parkinson's and other effortful speech, and gets real things done for them at home. This repo is its living public application/prototype: a Python/FastAPI backend (calls, conversation, medication tracking, memory, escalation, exercises, the Parker capture/resolve/stage/resurface pipeline) plus synthetic and real-audio eval harnesses.

## Product

- Role split (load-bearing): the person with Parkinson's is the *user*—voice is their whole interface, zero configuration. Family members are the *administrators and skill builders*—they connect accounts, curate/approve skills, and own guardrails through the review surfaces. Think OpenClaw-style agent, family-administered.
- North Star: successful interactions without human assistance—the user prefers Parker over a stock assistant, and Parker measurably improves with use (thesis metrics: learning velocity, revealed preference). Communication/Perception capability target: understood on the first try or after one repair question ≥90% of the time (stock voice assistants sit near 50% for the pilot user), measured by `make eval-audio-real`, later on consented pilot voice samples.
- Core loop: `Understand -> Confirm -> Act -> Follow up -> Escalate/Coordinate -> Learn`. Parker is a system built on OpenClaw/Hermes patterns, not a call bot: context/memory about the user, family, routines and preferences; tools/hands for safe actions; eyes/context later for room/TV/recliner workflows; a family network to escalate, summarize, coordinate and hand off.
- Thesis: variable-speech understanding, repair under uncertainty, safe action, follow-through, family coordination, per-user learning from consented local usage data, and eval-backed usefulness.
- Live-loop direction (Pras, 2026-08-30): best experience wins—send whatever data a lane needs (cloud audio included). Local-first ASR is a nice-to-have fallback, not a rule; frontier realtime speech models (OpenAI Realtime / gpt-realtime family) are a first-class lane, family-administered like every other setting.
- The trajectory is public: attract other families and developers so more deployment and usage improve the shared harness, evals, and skills—never a central model trained on anyone's voice without consent.
- Legacy framing: older docs/code may call the project "ParkinsClaw" and frame it as scheduled outbound calls in a cloned family voice. That is historical. Use the name Parker in new docs except for legacy filenames/code. Voice cloning is optional, requires explicit consent, and is not the value proposition.

## Safety boundaries (shipped product behavior)

Parker never ships, and agents never implement, behavior that:
- diagnoses;
- recommends treatment;
- changes medication;
- makes medical decisions;
- replaces emergency services;
- sends external messages or escalations unless the configured confirmation/escalation policy permits it *and* the send was explicitly confirmed;
- stores raw sensitive audio/video by default;
- uses cloned voices without explicit consent;
- purchases items or takes irreversible external actions without human approval;
- reads `.env`, credentials, secrets, private keys, cookies, or tokens.

Parker may support: reminders and routines; appointment preparation; communication repair; speech/movement exercises; family/caregiver coordination, including messages after confirmation; safe entertainment/education such as YouTube playlists and educational videos; research summaries; item search such as Amazon lookup, without purchasing; user-approved notes and summaries; smart-home actions only when safe and approved.

## 0-1 mode (Pras, 2026-08-17)

Parker is a 0-1 product. Until it demonstrably works in daily home use, build
value-added features only: understanding, repair, actions, learning,
deployment. Do NOT build safety/admin/compliance *infrastructure* ahead of a
working product — no consent frameworks, retention/deletion machinery,
revocation tooling, admin permission surfaces, auth hardening, or audit
dashboards. A five-minute family conversation beats a consent framework.

Data posture: keep everything, locally, like any personal agent keeps its
history. Do not add new redaction/retention lanes or extend existing ones.
"Keep everything" does not override the safety boundary above: raw sensitive
audio/video is still not stored by default.

Scope note: the runtime safety boundaries listed above (medical refusals,
confirmation-before-action, no send paths) are shipped product *behavior*
with pinned tests — they stay as they are. This rule defers safety
*infrastructure*, it does not dismantle the shipped guards; don't expand
that surface unprompted either.

Build style: discovery, then design, then small code/docs/eval changes. Convert the vision into executable seams, tests and docs rather than a broad rewrite. This is one-family v0 first, with a public eval/tooling path later.

## Agent boundaries

- Never read `.env`, credentials, secrets, private keys, cookies, or tokens.
- Do not send real messages/calls, make purchases, or use live APIs unless explicitly instructed.
- Add evals/tests before expanding action capability, and keep irreversible/external actions behind explicit approval boundaries.
- Preserve current passing tests unless intentionally changing behavior with updated tests.
- `docs/strategy/operating-model.md` § Decision rights lists what agents may do on their own and what needs Pras's (chairman) approval, such as clinical-efficacy claims, release of identifiable/private data, and public posts in his voice.

## Releases

Parker should be continuously updated, tested, released, and made talk-about-able. Standing authority in this repo: commit/push a coherent milestone without waiting for Pras to ask only when its relevant backend tests and evals are green and README/docs match what actually works now. A milestone with a failing relevant test or eval does not qualify for this grant, even when readiness item 2 below accepts the failure as understood and documented; keep it on its branch with a status note, as for unready work. Never push midstream half-finished work just to reduce a dirty tree.

A Parker change is ready when:
1. the behavior/docs form a coherent feature, eval, release, or public-positioning milestone;
2. relevant backend tests and evals pass, or failures are explicitly understood and documented;
3. README/docs/report files describe what actually works now, not aspirations;
4. safety boundaries are still explicit for external actions, medical claims, privacy, and family escalation;
5. the public surface is something Pras can confidently talk about.

If work is promising but not ready, leave it on a branch with a short status note in `docs/next-slices.md` rather than pushing to `main`.

## Multi-session working agreement

Several agent sessions (Claude Code sessions plus a Hermes agent) may share this working directory at once.

- On start, run `git fetch && git status`. If the tree has changes you did not make, stop and ask—another live session may own them. Never stash, hard-reset, discard, or commit someone else's work.
- Do all non-trivial work on a feature branch (one branch per session); merge to main only from a clean tree after a fresh `git pull --ff-only`.
- Destructive git operations—hard resets, stashing, checkout/restore discards, forced cleans, force pushes, autostash rebases, forced branch deletion—are off-limits in this shared checkout. If one is genuinely needed, the answer is coordination: ask Pras to run it manually.

## Where to look

- `docs/strategy/` — vision, capability briefs, roadmap, and the active experiment (`experiments/LOG.md`, `experiments/EXP-*.md`); use it to pick the next slice. Shipped slices are logged in `docs/next-slices.md`.
- `docs/architecture.md` — system map, for changes that cross modules.
- `backend/app/parker/pipeline.py` — the capture/resolve/stage/resurface pipeline; `backend/app/{conversation,escalation,memory,exercises}/` hold the matching modules.
- `benchmark/README.md` — the synthetic benchmark. The Makefile's `eval-*` targets run the eval harnesses; `eval-release-readiness` bundles the release evals.
- Commands: `make backend-venv`, `make test`, `make run`.
- Code, repo docs, tests, fixtures, and code-tied plans live in this repo; research/pitch synthesis in `~/Knowledge/parker` or `~/Knowledge/research/personal-brand/parker`; run manifests, reviews, and agent logs in `~/Operations`.

## Reporting

Lead final responses with a TLDR, then files changed, tests/evals run, and the next recommended implementation slice.
