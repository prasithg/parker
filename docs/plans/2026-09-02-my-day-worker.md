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

## Fix round (2026-09-02, P0.3 — date-grounded My Day)

The fresh review of the my_day slice reproduced (red run of
`tests/test_my_day_worker.py` against the old worker, real clock): a
reminder set ten days ago for this afternoon was dropped, "new reminder
due next month" set just now was spoken, and a 20-day-old family note
"…appointment to tomorrow at two" was narrated as a present-tense plan:

```text
Right now it is Wednesday, 2 September 2026, 4:17 PM EDT.
A reminder (waiting for his yes): new reminder due next month.
A note the family left: Sarah moved the neurologist appointment to tomorrow at two.
Parker keeps no calendar — only the reminders and notes written down here.
```

Root cause: `_my_day_reminder_lines` selected on `created_at >= utcnow()-2d`
and never read `execute_after`, so "his day" was really "what he set
recently", in naive UTC; `_my_day_note_lines` consumed the context-card
bullets (`- [memory_type] content`), which carry no date, and
keyword-matched relative words.

What changed (`app/parker/realtime_workers.py`, the my_day section only):

- Clock seam: `run_my_day_worker(make_db, *, now=None)` and
  `local_date_line(now=None)` — one aware home-local instant feeds the
  date line and both windows. The production call
  `run_my_day_worker(_make_db)` is unchanged.
- Storage decode: naive-UTC columns ↔ `home_timezone()` via
  `_to_stored`/`_from_stored` (the rollups' convention).
- Reminders by DUE time on the local day `[today, day_after)`: today →
  "— today at 4 PM.", tomorrow → "— tomorrow at 12:30 AM.", undated
  (`execute_after IS NULL`, still the two-day recency rule — the only
  shape the realtime lane stores) → "— no time on record.", still-open
  past-due (staged/confirmed, unbounded age) → "— still open, was due
  earlier today at 9 AM / yesterday at … / on 30 August at 10 AM." Order
  today, tomorrow, undated, open (most recently due first); cap 6. An
  executed reminder counts only when undated-and-recent or its time is
  still ahead — a past-due executed one was delivered at his yes and is
  the digest's done list, not his day.
- Notes straight from `ConversationMemory`: `source != 'realtime'`
  (Parker's own session summaries are not family notes), seven days back,
  cap 4, same plan keywords. "A note the family left
  {today|yesterday|on Monday}: …"; when not written today and the text
  contains today/tomorrow/tonight: " — written on Monday, so its
  “tomorrow” counts from then; the date is uncertain." Older notes are
  omitted from My Day (the context card via `_memory_lines` is untouched).
- Unchanged: the "(set)" / "(waiting for his yes)" literals, the per-line
  and whole-speech dose guard, the 11-line cap with its "…and N more"
  line, `MY_DAY_LIMIT_LINE` last, the honesty branches, `render_my_day_item`.

Pins: eight tests in `tests/test_my_day_worker.py` — the review repro;
the 23:30-local edge where both rows share a stored UTC date; overdue/open
with ordering; past-due executed excluded; undated voice reminder
labelled; a stale "tomorrow" note dated within a week and dropped beyond;
the dose-free regression; failing-source honesty. `tests/test_realtime.py
-k my_day` (6) unchanged and green.

Untested / caveats:

- `home_timezone()` is a fixed offset captured at call time: the
  tomorrow boundary across a DST switch is off by an hour.
- A naive `now` is treated as machine-local by `astimezone`; nothing
  passes one today.
- Only the first relative word in a note is named in the suffix.
- Which overdue reminders survive the 6-line cap when many pile up is
  pinned only as "most recently due first".
- Human gate: in a live session, with a dated reminder on record, ask
  "what do I have today" and hear the due day, not the set-date.

Adjacent defects reported, NOT fixed here (outside this slice's files):

1. `pipeline._coerce_datetime` (`app/parker/pipeline.py:425-428`) is bare
   `datetime.fromisoformat`: an offset-bearing ISO due time
   (`2026-09-02T16:00:00-04:00`) is stored as naive 16:00, which the UTC
   convention reads four hours early; a naive ISO input is ambiguous.
   One-line normalisation (aware → UTC → naive); text lane only.
2. The realtime lane stores no due time: `_stage_proposal_sync`
   (`app/parker/realtime.py:699-745`) builds
   `{intent_text, requested_action, subject[, recipient]}` with no
   `due_at`, and `PROPOSE_ACTION_TOOL` (`app/brain/claude.py:29`) has no
   `when` field — so every spoken reminder now reads "no time on record",
   with the date living only in the subject text. Adding `when` collides
   with the resolve gate `due_at IS NULL OR due_at <= now`
   (`pipeline.py:87`): a future due_at never stages and the session would
   report "nothing waiting". Needs a product decision (`due_at` as
   resurface-at vs due-at) in its own slice.
