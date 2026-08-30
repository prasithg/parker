# Functional Phrase Bridge — execution plan

Goal: connect Parker Voice Practice to one real everyday phrase through Parker’s existing local ASR, bounded repair, and normal confirmation path.

Done when: the isolated branch has a reviewable, tested, locally coherent vertical slice; canonical UTC gates pass; a fresh critic inspects the actual final artifact; and the first-user protocol is ready without claiming real deployment or clinical benefit.

Context:

- Base: `0d57832` (`feat: add local voice practice data loop`)
- Worktree: `/Users/prasithgovin/Operations/worktrees/parker-functional-phrase-2026-08-28`
- Current strategic mandate: deploy EXP-001, prioritize usefulness over further eval/admin polish, and make one personalized functional phrase the next bridge from Voice Practice into real ASR/repair/action evidence.

Constraints / non-goals:

- No second assistant pipeline, broad rewrite, exercise catalog, clinical/therapy claim, cloud upload, new central collection/consent program, message sender, purchase path, live home deployment, public action, push, or merge.
- The person controls pacing and can stop after any saved round.
- Existing policy/confirmation boundaries remain the only action authority.

Verification:

- Focused new tests first.
- `TZ=UTC make test`
- `TZ=UTC make eval-release-readiness`
- `git diff --check`
- revision-bound read-only critic of UI/API/data/policy behavior.

Parallel lanes: read-only architecture, UX/accessibility, and negative-space review only. One integration owner writes this worktree.

### T0 — Trace the real reuse path
Depends on: none

Objective: identify the smallest route from browser microphone capture to the existing transcriber and `TextSession`/repair/confirmation seams.

Likely paths:

- `backend/app/parker/practice_ui.py`
- `backend/app/parker/practice_router.py`
- `backend/app/exercises/voice_practice.py`
- `backend/app/voice/transcribe.py`
- `backend/app/conversation/textloop.py`
- `backend/app/parker/screen.py`
- `backend/tests/test_practice.py`

Acceptance:

- `architecture-note.md` maps call flow, state ownership, temporary/retained audio semantics, and codec feasibility.
- A bounded spike proves the chosen seam, or records a hard blocker and narrower honest fallback.

Verification:

- Existing focused practice tests pass before edits.

Risks / unchanged behavior: no source changes before the reuse plan; no live credentials or private audio.

### T1 — Pin the Functional Phrase contract red
Depends on: T0

Objective: express the user flow and authority/privacy invariants as failing tests.

Likely paths:

- `backend/tests/test_practice.py`
- a focused new test file only if current conventions justify it.

Acceptance:

- One voluntary phrase step after a saved sustained-voice round.
- Manual start/stop and skip/Finish.
- One family-configurable or safe local default phrase.
- Injected/local transcription enters the existing repair/confirmation behavior.
- Ambiguity cannot execute; action remains behind existing patient confirmation.
- Default path retains no raw audio; optional local retention does not leak artifact internals.
- Existing sustained-`ah` flow remains valid.

Verification:

- New tests fail for missing behavior, not setup noise.

### T2 — Implement the minimum coherent vertical slice
Depends on: T1

Objective: build the real user-facing bridge without duplicating ASR, routing, policy, screen, or outcome logic.

Likely paths: determined by T0; prefer the paths above and existing shared services.

Acceptance:

- End-to-end local flow works with injected/local ASR.
- UI remains large, keyboard-operable, manually paced, and clear about local/no-default-retention behavior.
- The user can finish without the phrase step.
- No external action capability or policy relaxation is added.

Verification:

- Focused tests green.
- Local API/UI smoke with synthetic or injected audio only.

Rollback: isolated unpushed branch; revert only worker-owned commits if needed.

### T3 — Make it first-user-ready, not just code-complete
Depends on: T2

Objective: add the smallest honest install-day/three-session protocol and synchronize claims required by the shipped behavior.

Likely paths:

- `docs/runbook.md`
- `docs/strategy/experiments/EXP-001-understand-and-learn.md`
- `README.md` only if behavior materially changes the current-state section.

Acceptance:

- Protocol records voluntary use, first-attempt understanding, repair completion, abandonment, wrong action, and preference.
- It explicitly says local prototype/one-person evidence, not therapy or population/clinical proof.
- No speculative roadmap prose or generic safety/admin framework.

Verification:

- Documentation statements map to code/tests and current product boundaries.

### T4 — Independent Gauntlet review and bounded repair
Depends on: T2, T3

Objective: have fresh-context critics inspect the actual artifact and close the largest evidenced gap.

Acceptance:

- At least one UX/accessibility critic and one engineering/policy negative-space critic are read-only.
- One integration owner fixes no more than two material gaps.
- A new read-only verdict is obtained after the final mutation.

Verification:

- Verdict includes exact artifact/revision, evidence, recheck, and PASS/NEEDS_FIX/BLOCKED.

### T5 — Finalize a local release candidate
Depends on: T4

Objective: freeze the final tree, run canonical gates, account for every changed file, and leave a morning-ready handoff.

Acceptance:

- Focused tests, `TZ=UTC make test`, `TZ=UTC make eval-release-readiness`, and `git diff --check` pass, or one exact blocker is documented.
- `final-report.md`, `dashboard.html`, `status.json`, and `side-effects.jsonl` are current.
- Coherent green work may be committed locally; nothing is pushed, merged, deployed, or published.

Verification:

- Stable `HEAD`/diff before and after final evidence.
- Worktree clean after local commit, or every remaining file is explained.
