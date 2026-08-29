# Living Room First Session — execution plan

Goal: make Parker's existing packaged setup hand a family directly into one truthful wake-gated first interaction, using the current talk loop and Dad Screen.

Done when: wake/open mode and sanitized wake name persist through setup; one final setup action starts the existing talk sidecar and Dad Screen with truthful failure state; deterministic traces prove ambient no-op and one addressed, confirmed local reminder; phase-1 behavior stays green; docs name remaining TCC/package evidence; canonical gates and a fresh reviewer pass.

Context: sequential phase on clean local commit `beca6ff`; same isolated branch/worktree; no remote action.

Non-goals: second runtime/onboarding app, general scheduler, new action/policy surface, clinical claim, live TCC/device install, real patient data/audio, push/merge/deploy.

### F0 — Map setup to the real first turn
Depends on: none

Inspect family config, setup API/UI, desktop sidecar/tray, talk loop, addressing, Dad Screen, outcomes, desktop docs/tests, and phase-1 microphone ownership. Write the exact reuse map before production edits.

### F1 — Red first-session contracts
Depends on: F0

Pin wake/open persistence and sanitization, final-action success/failure truth, ambient no-op, addressed request + invited yes + one local reminder, relaunch persistence, and singular microphone ownership.

### F2 — Minimum implementation
Depends on: F1

Extend existing setup and shell seams only. Preserve current defaults for non-living-room/demo users unless the setup choice deliberately changes them.

### F3 — Packaged truth and handoff
Depends on: F2

Update runbook/desktop/next-slices only to shipped behavior. Add a bounded packaged smoke checklist and explicitly mark real TCC/microphone/first-user items unverified.

### F4 — Independent Gauntlet
Depends on: F2, F3

Fresh read-only UX and engineering critics inspect the actual artifact; fix at most two material gaps; rerun exact checks after the final mutation.

### F5 — Stable local candidate
Depends on: F4

Run focused checks, JS/Rust checks if touched, `TZ=UTC make eval-release-readiness`, `TZ=UTC make test`, and `git diff --check`; account for all files; obtain a final revision-bound verdict; create a second local commit only if coherent and green.
