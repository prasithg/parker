# Independent review — PR #45 My Day worker

Date: 2026-09-02
Reviewer: Hermes
Target: `6ae0743f261c0e08eb3adfcbf0f181ee9ef67a2a`
Verdict: **NEEDS_FIX**

## Blocker: My Day is not date-grounded

`_my_day_reminder_lines()` filters staged reminders by `StagedAction.created_at >= now - 2 days`. It does not select by `execute_after`/the captured intent's due time. A direct probe inserted an older reminder due today and a newly created reminder due next month. The worker said:

> A reminder (set): new reminder due next month.

It omitted the reminder due today.

The plan-like note heuristic similarly selects free-text bullets containing relative words such as `today`, `tomorrow`, weekdays, or `coming`, without requiring a date/provenance that makes those words current. An old “appointment tomorrow” note can therefore be spoken as part of today's plan.

For a feature named My Day, this is a truthfulness failure rather than a ranking preference.

Fix the worker to:

- use a local-time day window and reminder `execute_after`/captured `due_at` semantics;
- include an older record due today and exclude a new record due outside the requested horizon;
- use only structured notes with an absolute date/time, or explicitly label undated recent notes as date-uncertain rather than presenting them as today's plans;
- preserve the no-dose rule and the unconditional no-calendar limitation;
- test timezone edges, overdue/open reminders, tomorrow, and the exact old-due-today/new-due-next-month reproduction.

## What passed

- Exact-head remote CI succeeded.
- The final stacked backend suite passed.
- The local worker is read-only, keyless, uses the existing typed worker delivery path, includes names/times but no medication dose, and reports store failures honestly.
- Busy-day truncation preserves the limitation line; partial source failures are surfaced rather than converted to “nothing on record.”

## Stack state

This head inherits the open PR #40 and #43 blockers. Rebase/merge it only onto their final reviewed revisions and rerun its date-grounding deck plus the final union suite.

Review modified no implementation files.
