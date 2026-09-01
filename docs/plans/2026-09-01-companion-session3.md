# Session 3 handoff: companion fixes from Pras's second real-mic test

Date: 2026-09-01 (evening test on `fable/reachy-companion-take2` @ `5509b0e`)

Status: chairman feedback logged verbatim + diagnosed against the REAL
session journal (call_log 41, 23 exchanges, ~4.5 min; the receipts/journal
lane built for exactly this worked). Next builder session starts here with
`/parker-session` — do NOT re-derive; the diagnosis below cites evidence.

## Pras's feedback (verbatim intent)

1. "Much more dynamic — I actually felt like I could talk through things."
2. **Bug**: "when the conversation clearly ended reachy eyes didn't stop."
3. **Confusion** "between being powered on and actively engaged/listening."
4. "Couldn't tell if web search was turned on — didn't seem to trigger for
   US Open watch times / who's playing."
5. Reachy "looks much better but still not as interactive or expressive as
   the real-life Reachy" — research real Reachy videos/screenshots to
   model from; **Pras will have Hermes gather reference material**.
6. Parker's **voice**: male, distinguished, a bit serious — something that
   resonates with an elder person.

## Diagnosis (from the journal — evidence, not guesses)

### 2. Eyes didn't stop at the clear end — CONFIRMED, root cause known

Call 41's final receipts: he said **"OK, thanks."** (seq 121, t=232 s) →
Parker replied → drained → transition to **`listening`** (seq 122) — the
last event. Nothing recognizes spoken session-enders; only the 90 s + 30 s
idle ladder winds down, so Reachy sat bright-eyed in active listening
after a clearly finished conversation until he power-cycled (call 42
starts 39 s later with a fresh greeting).

**Fix direction** (brief already specifies): explicit spoken end phrases
("that's all", "goodbye", "stop", "thanks, that's it") end the session →
wind-down → dormant. Design decision for the builder: deterministic
phrase set on the user transcript (like the confirmation grammar) vs. an
`end_conversation` tool the model calls on clear goodbyes (bridge still
verifies against a deterministic whitelist before closing — the model
never unilaterally hangs up). Also consider shortening the idle ladder
now that dormancy exists (90 s is long).

### 4. Web search — it RAN; it was invisible + two real gaps

The journal shows 6 `lookup_ack` + 8 `injection` events. "Tennis I can
watch today" → look_that_up("What tennis matches are scheduled for this
evening, especially at the US Open…") → real answer injected and spoken
(seq 31–37: "Tonight at the US Open, the main evening session features
Novak Djokovic…"). So search is ON and working. What failed:

- **Invisible**: the companion shows no cue that a lookup ran (the
  antenna work-glow is subtle; source chips were deliberately removed
  from the companion; CC was off). He couldn't tell. Fix: a clearer
  work cue (e.g. distinct antenna pattern + a CC-level "checked the
  web · source" line when CC is on; maybe a small transient chip even
  with CC off — decide against the zero-UI contract carefully).
- **"What do I have today"** (seq 12–18): the model called the SEARCH
  worker for his personal calendar; the worker honestly answered "no
  access to a calendar." Parker HAS local reminders/schedule data —
  a `my day / reminders` worker (local, read-only) is the missing lane,
  and the instructions should steer personal-schedule questions to it,
  never to web search.
- **The worker doesn't know the date** (seq 113: "I don't have a
  reliable read on today's exact date") — ground `run_search_worker`
  with the local date/time the way the front session's `clock_line`
  already does. Small, high-value fix.

### 3. Dormant vs engaged confusion

Both states show the switch label "Parker is on"; dormant vs listening
differ only in pose/eye glow. Fixes: dormant switch label becomes
"Resting — say “Hey Parker”"; make dormancy read unmistakably asleep at
a glance (dim the scene lighting itself in dormant, not just the eyes;
brighten on wake) so powered-on-resting vs engaged-listening can never
be confused. Keep SR/CC text aligned.

### 6. Voice

`settings.openai_realtime_voice` default is currently `marin`. Switch the
default to a male, distinguished, slightly serious voice for the
gpt-realtime family — audition `cedar`, `ash`, `echo` (a one-line .env /
settings change; family-administered). Pras should hear 2–3 and pick.

### 5. Reachy expressiveness (research task)

Current character is primitives-built from a text brief. Next level needs
visual reference: real Reachy Mini videos/screenshots (Pollen Robotics
YouTube, Hugging Face demos, reviewer footage — the "sad/happy/curious"
antenna emotes, the head-lean tracking, idle sway, wake/sleep beats).
**Pras will have Hermes collect reference material** (frames/clips/notes)
— the builder session should consume that into: antenna emote library,
gaze-tracking-toward-voice behavior, richer idle life, and transition
beats, all still downstream of the semantic expression state.

## State of the branch (context for a fresh session)

- Branch `fable/reachy-companion-take2` (PR #40, stacked on PR #37's
  branch; PR #39 = grammar hotfix). Do not merge — independent Hermes
  review pending on all three. **CI caveat (found 2026-09-01 evening):
  GitHub CI has NEVER run on this branch — the workflow triggers only
  for PRs targeting main, and PR #40's base is a feature branch. Local
  `make test` is green (1179), but a stacked PR gets no CI. Next
  session: extend the workflow trigger to all PRs (or re-base #40 once
  #37 merges) before trusting any green badge.**
- Shipped so far: companion surface (power+CC only) at /parker/converse;
  lab harness at /parker/converse/lab; spoken yes/no confirmation with
  contract binding + action_result truth; Reachy v2 character; local
  "Hey Parker" wake (energy-gated faster-whisper spotting, dormancy,
  the pop, wind-down back to dormant); real-audio probe
  `scripts/wake_probe.py` (PASS).
- Evidence lanes that made this diagnosis possible: expression receipts
  + session journal (`realtime_session_events`), viewable at
  /parker/sessions/ui and via read-only sqlite on `backend/parker.db`.
- Suite: 1179 passed at `5509b0e`. Node specs: companion 16/16, lab 3/3,
  expression 47/47, wake 25.

## Proposed session-4 slice order

1. Spoken session end → wind-down → dormant (+ shorter idle ladder);
   pin "OK thanks" ambiguity carefully (mid-conversation thanks must NOT
   hang up — only clear enders).
2. Dormant-vs-engaged legibility (label + scene-level dimming).
3. Search visibility cue + worker date grounding + local
   reminders/my-day worker for personal-schedule questions.
4. Voice default + audition note for Pras.
5. Reachy expressiveness pass from Hermes-gathered reference.

## Human gates (rolling)

Real-voice wake in the room over an evening (false-wake watch), the full
conversation cycle with spoken end, packaged WKWebView.
