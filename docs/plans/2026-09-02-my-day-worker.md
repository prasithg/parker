# Plan: the "my day" worker — his own day from Parker's records

Date: 2026-09-02 (overnight; backlog item 8 of the chairman decisions in
[2026-09-01-companion-session3.md](2026-09-01-companion-session3.md);
Hermes: "a local `my reminders`/`my day` worker is valuable but a separate
worker slice. Name its limits honestly: Parker has local reminders, not a
general calendar, until a real calendar source exists.")

## Problem

Call 41 (Pras's session-3 test, seq 12–18): "what do I have today" was
routed to the web-search worker, which honestly answered that it had no
access to a calendar. Parker does hold local facts about his day —
medicine times, reminders he set through Parker, notes the family left —
and nothing read them back to him.

## Contract (v1)

- A new realtime tool **`my_day`**, always offered (local; no brain or
  key needed), described so the front model uses it for anything about
  HIS own day — schedule, appointments, reminders, when his medicines are
  — and never `look_that_up` for those. The realtime addendum steers the
  same way.
- The worker (`run_my_day_worker`) reads, locally and read-only:
  1. today's local date/time line (the same grounding as the search
     worker);
  2. every active medicine's scheduled times — **names and times only,
     never a dose** (the same rule as the context card);
  3. reminders he set through Parker in the last two days — waiting for
     his yes, or already set — by subject;
  4. family/context notes that read like plans (appointment, tomorrow,
     Friday, "at …"), at most four.
  Every line is screened by the spoken medical-boundary guard; a session
  with nothing on record says so plainly; the note always ends with the
  limit: *"Parker keeps no calendar — only the reminders and notes written
  down here."*
- Delivery mirrors the search worker: instant `working` ack, `working`
  frames (`kind: my_day`) for the Reachy work cue, the result injected as
  a fenced system item with a plain-words instruction, nudged once,
  journaled as an `injection` from worker `my_day`. Never a source list;
  never a URL.

## Non-goals

A real calendar source (Google/Apple), editing reminders from this lane,
dose information of any kind, changing the confirmation pipeline, page
changes (the existing work cue covers the wait).

## Verification

`tests/test_realtime.py`: `my_day` offered without a brain and the prompt
steers to it; a seeded medicine + family note come back as names, times
("8 AM, 2 PM and 8 PM"), and the note — never the dose; nothing on record
→ says so; a reminder he confirmed in-session appears as "(set)". Full
suite green. Human gate: ask "what do I have today" in a real session
and hear Parker's notes, not a web search.

## Fix round (2026-09-02, after the fresh review of `182bad3`)

The review found the limit line could be truncated on a busy day and that a
failed store produced "nothing is on record" — Parker denying reminders he
holds. `7cb29e1`: the cap is applied before the unconditional limit line; a
store failure is an error result whose item says Parker could not read his
notes (never "nothing on record"); a single failing source adds an honest
partial line; confirmed reminders read as set; plan-like notes come only
from the memory bullets (never the concerns section) with the prefix
stripped; the in-flight key is namespaced. Two pins added (busy day; store
failure).
