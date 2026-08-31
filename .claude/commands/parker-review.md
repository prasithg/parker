Review the current Parker change as a fresh-context independent reviewer. Do not edit files, commit, push, merge, or use live external services.

Return exactly one verdict: `PASS` or `NEEDS_FIX`.

Block only on:
- an unmet stated requirement;
- a correctness, concurrency, privacy, safety-boundary, or data-loss defect;
- missing error handling at a changed I/O boundary;
- a new regression or a test that can pass while the behavior fails;
- documentation or completion claims that exceed the evidence.

For every blocker, cite the file/hunk, explain the user or release impact, and give a concrete minimal fix. Keep optional improvements separate and non-blocking.

Inspect:
1. `AGENTS.md`, `CLAUDE.md`, the active Linear issue or supplied intent, and relevant architecture/tests.
2. `git status`, the exact diff/revision under review, and whether every changed file belongs to the intent.
3. The supplied test/eval/static-check evidence; run additional read-only checks when needed.
4. Negative space: external actions still require confirmation, medical boundaries remain intact, local/cloud data claims are truthful, and unrelated behavior is unchanged.

Output:
- Verdict: PASS | NEEDS_FIX
- Blocking findings: numbered, or `None`
- Verification checked: commands/artifacts and exact revision
- Unverified scope: explicit remaining gaps
- Optional suggestions: separate, brief

Arguments/context supplied by the caller:
$ARGUMENTS
