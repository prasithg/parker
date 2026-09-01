# Parker agent delivery workflow

Status: active repository workflow for sustained Claude Fable, Hermes, Codex, and other coding-agent sessions

Date: 2026-09-01

## Why this exists

Parker already had good plans, tests, branches, CI, handoffs, and ambitious same-session review. The missing piece was an explicit delivery contract that proves which phase a task reached and prevents a builder from silently redefining the gate it was asked to satisfy.

This workflow combines four useful patterns:

- compound engineering: do not teach the same lesson twice; promote stable lessons into repo instructions, tests, evals, or fixtures;
- the Matt Pocock-style delivery lifecycle: specification, plan/tickets, implementation, verification, review, and durable learning are distinct states;
- the PrasClaw/Hermes loop: intent, blast-radius routing, builder evidence, cross-family review, negative-space review, and explicit untested surfaces;
- Fable prompting: one clear outcome, authoritative source files, few load-bearing constraints, visible phase state, and concrete evidence rather than instruction volume.

The repository and PR are the system of record. Chat context is not.

## The delivery state machine

Every substantive task moves through these states:

```text
specified
  -> planned
  -> implemented
  -> verified
  -> independently_reviewed
  -> human_accepted       # when the task has a human/device/product gate
  -> merged
  -> compounded
```

These are evidence states, not progress adjectives.

| State | Required evidence |
|---|---|
| `specified` | Goal, acceptance source, scope, non-goals, blast tier, human/reserved decisions. |
| `planned` | Repository-grounded plan with dependencies, likely files, acceptance checks, exact verification, and rollback/failure behavior where material. |
| `implemented` | Coherent diff on a feature branch; every changed file belongs to the intent. |
| `verified` | Required commands actually ran with exit status/output; browser, packaged, device, or live gates are either passed or still explicitly open. |
| `independently_reviewed` | Fresh exact-revision review report from a different model family or Hermes; findings cite evidence and state untested scope. |
| `human_accepted` | Pras completed any reserved product/device/privacy/public gate named in the task. |
| `merged` | PR merged after required CI/review/gates; exact merge revision recorded. |
| `compounded` | Escaped lessons became a regression test, fixture, architecture rule, workflow update, eval case, or an explicit decision that no durable update is warranted. |

A task may not claim a later state without the evidence for every earlier state. A plan, test list, or self-review does not by itself advance the task.

## Blast-radius routing

Classify before implementation.

### T0 — low-risk and reversible

Examples: documentation, comments, local drafts, isolated tests, non-executing fixtures.

Required:

- clear intent;
- relevant validation;
- one independent check or strong deterministic evidence;
- untested surface stated.

### T1 — persistent developer or product infrastructure

Examples: local utilities, eval tooling, packaging scripts, non-auth admin/review UI, internal process commands.

Required:

- intent block and plan;
- focused plus project-level gates;
- fresh independent review, preferably cross-family;
- PR and green CI;
- human acceptance if a real packaged/device behavior is part of the claim.

### T2 — Parker user-facing assistive/control-plane work

Examples: microphone/wake/session ownership, realtime voice, repair/confirmation, action execution, medication/medical boundaries, family messages, personal audio/data, public claims/releases.

Required:

- intent block and repository-grounded execution plan;
- explicit negative space and human/device gates;
- builder inner-loop evidence;
- fresh cross-family review and refuter/second lens when useful;
- exact real-path acceptance where the plan requires microphone, Tauri/WKWebView, physical room, or human use;
- Pras's product/right-change gate before merge when named;
- no auto-merge by the builder.

Parker's core voice and assistive flows are T2 even when the code diff looks small.

## Required intent block

Every substantive PR or task packet includes:

```markdown
## Intent
- Goal:
- Acceptance source:
- Why this is the right change now:

## Decisions
- Decision and rationale:
- Alternatives rejected:

## Scope
- Expected surfaces/files:
- Explicit non-goals:

## Risk
- Blast tier and why:
- What the builder is least sure about:
- Human/device/reserved gates:

## Verification plan
- Focused gates:
- Project gates:
- Real browser/package/device gates:
- What will remain untested:
```

No intent block means the change is not ready for review.

## Session workflow

### 1. Preflight

- Read `AGENTS.md`, `CLAUDE.md`, this workflow, the active plan/review, and relevant code/tests.
- Run `git fetch` and `git status`.
- Stop if the shared checkout contains changes from another session.
- Confirm the task's branch, base revision, Linear/PR pointer, and current delivery state.

### 2. Wayfind and specify

- Trace the real code/UI/data flow before proposing architecture.
- Restate the goal and done criteria in a compact intent block.
- Name assumptions and unresolved decisions.
- Classify T0/T1/T2.
- If the task already has a plan, do not replace it with a second plan; patch only evidenced gaps.
- A builder may document a proposed gate change but may not unilaterally downgrade, defer, or reclassify an acceptance gate. Pras or Hermes must explicitly accept the change.

### 3. Plan

Use `docs/plans/YYYY-MM-DD-<slice>.md` for a substantive slice. Each phase/task needs:

- objective and dependency;
- likely files/surfaces;
- observable acceptance checks;
- exact verification command or real acceptance procedure;
- material negative space;
- human/device gate when automation cannot prove the claim.

Expand only the next executable phase. Avoid a speculative project plan for every future idea.

### 4. Implement in small slices

- One feature branch per logical change.
- Prefer tests/reproductions that fail before the fix.
- After each coherent slice, run the narrow gate before moving on.
- Keep a short task ledger in the plan/handoff: `pending`, `in_progress`, `verified`, `blocked`, or `deferred_by` with the approving authority.
- Do not use a large agent panel to replace direct code reading or real acceptance evidence.
- At a major phase boundary or around 45–50% of Fable's context window, write a checkpoint/handoff and start a fresh implementation or review context when practical.

### 5. Verify

Verification is selected by changed surface, not by one universal command.

Minimum repository checks:

```bash
git diff --check
make test
```

Add the relevant gates:

- voice/realtime/scenarios: `make eval-voice-scenarios` plus focused tests;
- desktop/Tauri: Rust tests/compile and the documented packaged smoke;
- JavaScript/UI: syntax/unit tests, exact desktop+narrow browser captures, console/overflow/focus/reduced-motion/no-WebGL checks;
- microphone/wake/barge-in: real microphone and lifecycle acceptance when claimed;
- actions: duplicate/retry/Stop/confirmation negative space;
- public/evidence claims: release-readiness and source artifact inspection.

A generated report is not proof of the physical or user-facing behavior it proxies.

Builder evidence must include:

- exact revision;
- commands and exit/results;
- browser/package/device evidence paths;
- changed files/diff scope;
- known failures;
- untested surface;
- deliberate deviations and who authorized them.

### 6. Review

Builder self-review and Fable multi-agent panels are useful inner-loop tools but count as same-family/degraded review. Builder self-review does not satisfy T1/T2 independent review alone.

The independent reviewer receives:

- frozen exact revision/diff;
- intent block and acceptance source;
- verification packet;
- known deviations and open human gates.

The reviewer returns `PASS` or `NEEDS_FIX`, with:

- acceptance coverage;
- blockers with `file:line`, impact, and minimal fix;
- test-integrity and negative-space assessment;
- evidence checked;
- explicit untested surface;
- blast tier.

Required acceptance gates must not be pre-labeled as “not findings” merely because the builder could not run them. They remain open gates until the authorized owner changes the contract.

### 7. Fix and re-review

- Fix verified blockers only.
- Add a regression test for each reproduced defect when practical.
- Re-run the affected and project gates.
- Obtain a fresh exact-revision review of the final bytes.
- Two fix/review cycles is the normal limit; a third means reconsider the design rather than stacking patches.

### 8. Merge and handoff

- PR description and comments carry the intent, evidence, verdict, human gates, and exact revision.
- CI green is necessary, not sufficient for T2.
- Fable may commit and push its branch. It must not merge T1/T2 work without the required independent verdict and human/device gate.
- Record the exact merge revision and remaining unverified scope in the plan/Linear issue.

### 9. Compound

After a Pras correction, reopened PR, real-user failure, or independent-review escape, ask:

1. What did the builder/reviewer learn too late?
2. What earlier gate should have caught it?
3. Does the lesson belong in a regression test, scenario, plan template, architecture doc, `.claude` command, `AGENTS.md`, skill, or only the run note?
4. What proves the next session actually uses the new guardrail?
5. What should expire instead of becoming permanent policy?

Do not merely add prose. Prefer an executable regression or invocation proof. Do not promote transient facts into global instructions.

## Review effectiveness

A green review means the named evidence satisfied that reviewer; it is not proof of correctness. When a passed change is later reopened or corrected:

- mark it as a verifier escape in the task/review record;
- name the missed surface and why the gate passed;
- patch the earliest useful gate;
- verify the new guardrail against the escaped case and at least one negative/routing boundary.

Periodically inspect escapes before adding more reviewers. The objective is lower false-pass rate, not larger agent panels.

## Fable prompting contract

A good Parker Fable prompt is direct and bounded:

- one outcome;
- exact authoritative plan/review/PR paths;
- current branch/base and delivery state;
- 3–6 load-bearing constraints;
- evidence required before each state transition;
- hard human/reserved gates;
- explicit merge authority;
- expected handoff shape.

Avoid “work all night and make it better” as the whole contract. Long autonomous runs are acceptable only when the exit gates and authority are explicit.

Use the repo command:

```text
/parker-session <goal, source plan/review/PR, and any chairman decision>
```

Use `/parker-review` only in a fresh read-only reviewer context. A builder invoking it on its own diff is useful inner-loop evidence but not the independent Hermes/cross-family gate.

Initial invocation and routing evidence for this workflow is recorded in [`docs/reviews/2026-09-01-agent-workflow-eval.md`](reviews/2026-09-01-agent-workflow-eval.md). Re-evaluate after 3–5 substantive PRs using escape/reopen evidence rather than adding more ceremony preemptively.

## Completion footer

Every substantive handoff ends with:

```markdown
Delivery state:
Blast tier:
Exact revision:
Intent/acceptance source:
Evidence checked:
Independent review:
Human/device gates:
What remains untested:
Deliberate deviations and approving authority:
Next owner/action:
```
