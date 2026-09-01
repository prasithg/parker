Run the Parker agent delivery workflow for the requested task. This command is for a builder session; it does not make the builder an independent reviewer.

Authoritative workflow: `docs/agent-development-workflow.md`.

## 1. Preflight

- Read `AGENTS.md`, `CLAUDE.md`, `docs/agent-development-workflow.md`, the supplied plan/review/PR, and relevant implementation/tests.
- Run `git fetch` and `git status`.
- Stop if the shared checkout contains changes you did not create.
- Identify the current branch, base revision, PR/Linear pointer, and delivery state.

## 2. State the session contract before editing

Print a compact block:

- Goal and acceptance source.
- Why this is the right change now.
- T0/T1/T2 blast tier and why.
- Expected files/surfaces.
- Explicit non-goals.
- Verification matrix: focused, project, browser/package/device.
- Human/reserved gates.
- What you are least sure about.
- Merge authority for this session.

If a load-bearing decision is missing, ask once. Otherwise proceed without waiting.

A plan already exists unless the supplied context proves otherwise. Patch evidenced gaps; do not create a competing plan. You may propose a gate change, but may not downgrade/defer/reclassify a stated acceptance gate without explicit Pras or Hermes approval.

## 3. Build in coherent slices

- Work on the feature branch, never directly on `main`.
- Prefer a failing reproduction/test before a fix.
- Run the narrow gate after each slice.
- Keep a short task ledger in the active plan/handoff.
- Do not broaden the task to adjacent roadmap ideas.
- Builder panels/workflows are allowed as inner-loop tools, but same-family consensus is not independent review.

At a major phase boundary or near 45–50% context, write a checkpoint/handoff and start a fresh context when practical.

## 4. Verify by changed surface

Always run:

```bash
git diff --check
make test
```

Then run every relevant gate named by the plan and `docs/agent-development-workflow.md`, including real browser/package/device acceptance when the claim requires it. Do not treat a fake upstream, generated report, sidecar asset serve, or CI pass as proof of a real microphone, WKWebView, physical-room, or human-use gate.

## 5. Handoff, review, and merge boundary

Before claiming completion, provide:

- Delivery state.
- Exact revision and base.
- Intent/acceptance source.
- Diff/files summary.
- Commands/results and evidence paths.
- Known failures.
- What remains untested.
- Deliberate deviations and approving authority.
- Human/device gates.
- Next owner/action.

Commit and push a coherent branch/PR when authorized. For T1/T2, do not merge until the required fresh exact-revision independent review and human/device gates are satisfied. `/parker-review` in the builder's own session is degraded inner-loop review, not that gate.

## 6. Compound

For every correction or escaped defect discovered during the session, add the earliest useful regression/fixture/rule and say where the lesson was promoted. Avoid prose-only accumulation and transient global rules.

Arguments/context supplied by Pras or Hermes:

$ARGUMENTS
