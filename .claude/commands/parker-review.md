Review the current Parker change as a fresh-context independent reviewer. Do not edit files, commit, push, merge, or use live external services unless the supplied review contract explicitly requires a named read-only fetch.

Authoritative workflow: `docs/agent-development-workflow.md`.

This command is intended for a fresh reviewer context. If you built the change, state `same-family builder self-review` and treat the result as degraded inner-loop evidence—not the independent Hermes/cross-family gate.

Return exactly one verdict: `PASS` or `NEEDS_FIX`.

## Review inputs

Require and inspect:

1. `AGENTS.md`, `CLAUDE.md`, `docs/agent-development-workflow.md`, the active Linear issue or supplied intent, and relevant architecture/tests.
2. The intent block: goal, acceptance source, decisions, alternatives, expected scope, non-goals, blast tier, least-certain area, tests, and untested surface. If it is missing on substantive work, return `NEEDS_FIX` without reconstructing the builder's intent for it.
3. `git status`, frozen base/head revisions, exact diff, and whether every changed file belongs to the intent.
4. Builder evidence: commands/results, browser/package/device artifacts, known failures, deviations, and human gates.
5. Negative space: external actions still require confirmation, medical boundaries remain intact, local/cloud data claims are truthful, Stop/interruption and retry semantics hold, and unrelated behavior is unchanged.

## Blocking rules

Block only on:

- an unmet stated requirement or acceptance criterion;
- a correctness, concurrency, privacy, safety-boundary, accessibility, or data-loss defect;
- missing error handling at a changed I/O boundary;
- a new regression or a test that can pass while the behavior fails;
- documentation/completion claims that exceed the evidence;
- a required real browser, microphone, packaged, physical, or human gate still open;
- a builder-deferred/reclassified gate without explicit Pras or Hermes approval;
- missing intent/review evidence required by the task's blast tier.

For every blocker, cite the file/hunk, explain user/release impact, and give a concrete minimal fix. Keep optional improvements separate and non-blocking.

Do not accept these substitutions:

- same-family panel consensus for cross-family review;
- green unit tests for a required real-device or human gate;
- generated reports for observed user behavior;
- sidecar asset serving for actual WKWebView/WebGL rendering;
- a plan or handoff that says a gate is deferred when the acceptance source requires it.

## Verification expectations

Re-run or directly inspect the highest-value evidence where possible. Test the failure interleavings most likely to be missed by happy paths. Look for seeded negatives and routing boundaries, not only positive fixtures.

A `PASS` means the supplied revision satisfies the stated contract for its blast tier. It does not mean untested scope vanished.

## Output

- Verdict: `PASS` | `NEEDS_FIX`
- Reviewer mode: cross-family independent | same-family independent | builder self-review (degraded)
- Blast tier: T0 | T1 | T2, with one-line rationale
- Acceptance coverage: criterion-by-criterion `PASS` | `PARTIAL` | `FAIL`
- Blocking findings: numbered, or `None`
- Evidence checked: commands/files/URLs/screenshots/exact revisions
- What I did not check: explicit untested surface
- Human/device gates: passed/open/not applicable
- Optional suggestions: separate and brief

Arguments/context supplied by the caller:

$ARGUMENTS
