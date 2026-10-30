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
**Hermes has ALREADY delivered the first reference**:
`docs/references/2026-09-01-reachy-mini-motion-reference.md` (source-backed
motion vocabulary — staged wake beat, attentive/thoughtful/inquiring
poses, sleep silhouette, 50 Hz emotes library notes, and a
no-asset-copying license rule; untracked in the shared checkout as of
this writing — the Hermes lane owns committing it). The builder session
consumes it into: antenna emote library, orientation/engagement cues,
richer idle life, and transition beats, all still downstream of the
semantic expression state.

## State of the branch (context for a fresh session)

- Branch `fable/reachy-companion-take2` (PR #40, stacked on PR #37's
  branch; PR #39 = grammar hotfix). Do not merge — independent Hermes
  review pending on #37/#40. **CI update (2026-09-01 ~22:10Z,
  superseding the earlier caveat): a Hermes lane merged PR #39 to main,
  fixed the stacked-PR CI trigger, and merged main + PR #37's base into
  this branch (`4a19bf8`) — CI then RAN on PR #40 and PASSED (run
  33564781769, 2m17s). The "extend the workflow trigger" slice is
  already done upstream; drop it from the slice order. Always
  `git pull --ff-only` at session start — this branch is shared with
  the Hermes lane.**
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

(The CI-trigger fix landed upstream — start directly at 1.)

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

## Hermes independent review amendment (2026-09-01)

Review: [PR #40 independent review](../reviews/2026-09-01-pr40-independent-review.md).
Official motion input: [Reachy Mini motion and expression reference](../references/2026-09-01-reachy-mini-motion-reference.md).

The original proposed slice order is superseded by the review order below:

1. **CI is no longer a builder task.** Hermes merged PR #41 (`ff4cd01`), which runs CI for every PR plus manual dispatch, merged PR #39 (`1c40275`), synchronized main into PR #37 and PR #40, and triggered the first real PR #40 CI run.
2. **Fix the strict concurrent-session false-green before feature work.** Make spawned-thread errors fail the test/CI and remove the shared SQLite write/refresh race. Independent repetition failed 4 of 15 runs; a full suite run was contaminated into a later spoken-confirmation failure.
3. **Make power server-authoritative, single-owner, acknowledged, and fail-closed.** Revoke all companion wake/realtime sessions on off, reject stale/second tabs, expose persistence failure, bound live reconnects to one attempt, and never open continuous cloud audio merely because local wake ASR is unavailable.
4. **Tighten wake before relying on evening dormancy.** Current grammar wakes on `hey darker`, `hey marker`, `hey barker`, `hey packer`, `hey parked`, and `a parker`; replace broad edit-distance/greeting acceptance with an evidence-backed confusion set, preserve the request tail in `Hey Parker, <request>`, and add ambient-TV CPU/false-wake evidence.
5. **Make the packaged app open the companion.** The current Tauri first-session/tray path still opens `/parker/screen`; verify power/wake/WebGL/permission/teardown through the actual person-facing WKWebView path.
6. **Fix accessible action/error cards and search truth.** Exact staged/result/error text needs atomic live semantics. Keep CC-off zero-chrome with unmistakable Reachy/spoken work cues; show bounded source labels in CC-on mode; align the realtime prompt with both.
7. **Stop for human/device review.** Return exact revision, real stacked CI, strict-concurrency evidence, multi-tab/persistence-failure tests, wake soak results, real-mic results, packaged WKWebView evidence, and remaining untested scope for fresh Hermes review.

Do **not** implement session-ending, My Day, voice-default, or another expressiveness pass in this foundation session. Those become later slices after the gates above pass; the full ordering is in the independent review.

A separate fast current-web spike is planned in [2026-09-01-fast-current-web-search-spike.md](2026-09-01-fast-current-web-search-spike.md). It benchmarks Parallel Turbo against Exa Instant and the existing Claude worker, and changes the realtime turn to route silently before any current-fact audio.

## Chairman decisions after the review (2026-09-01 evening)

Pras read the independent review and overrode two points. This section supersedes the "foundation closure only" order above and the review's wake wording.

### Wake: calibrate for Dad, do not tighten

This is a Parkinson's user. Words that sound like Parker after a greeting must wake him: `hey parka`, `hey par... ker`, a slurred or trailing second syllable, long pauses between words. While powered-on and dormant, a missed wake is worse than an occasional extra wake — the mic is already held locally, nothing streams to the cloud, and the worst case is Parker perking up and hearing nothing.

Keep:

- the greeting gate (a bare "parker" from the TV must not wake);
- privacy fail-closed: missing local wake ASR never opens continuous cloud audio;
- the request tail after the wake phrase (`Hey Parker, can you help me` keeps `can you help me`).

Remove only accidental tokens such as bare `a` as a greeting. Do not shrink the Parker-like set to a purist confusion list. Evidence: an ambient-TV false-wake/CPU soak plus a recall matrix of effortful positives, then calibration against Dad's real voice in the room.

### Run long and compound; install the gate loop instead of shrinking scope

The problem with the prior all-night session was not endurance; it was that nothing external forced a stop when work was declared done but was not (the concurrency bug reported green locally, then failed real CI). The fix is one long autonomous session with a real gate between slices, not a short session.

Per-slice gate — all three required before the next slice starts:

1. full backend suite with spawned-thread exceptions failing the run; concurrency-sensitive tests repeated at least 5x;
2. push; the real GitHub CI check on the PR passes;
3. a fresh-context Fable review (`/parker-review` in a new context) of that exact diff returns PASS; NEEDS_FIX findings become the next task before moving on.

Never rerun CI to obtain green. Fix the root cause.

Merge policy (Pras, 2026-09-01): slices that pass all three gates may merge to main without waiting for Hermes, **except** anything touching power, wake, safety/guard, confirmation, or action execution — those stay on the branch for Hermes/human review. Interpretation for the current stack: PR #37 and PR #40 contain power/wake/safety code and stay unmerged; new independent slices (CI/test fixes, docs, accessibility semantics, search date grounding, legibility labels) should land as their own PRs from main where they do not depend on the stack.

Stop only at genuine human/device gates: real microphone in the room, TV-room false-wake soak with Dad's voice, packaged WKWebView, voice audition. Everything before those runs unattended.

### Overnight backlog (dependency order)

1. Deterministic CI: fix the shared-SQLite concurrent-session race; thread errors fail tests; repeated strict passes.
2. Power: server-authoritative, single-owner, persistence-acknowledged, fail-closed across tabs/restarts; bounded reconnect.
3. Wake calibration as above, including fail-closed on missing ASR and same-breath request preservation; ambient-TV soak evidence.
4. Packaged Tauri opens the companion; real WKWebView power/wake/WebGL lifecycle evidence (device gate — checkpoint and continue past it if it cannot be verified headlessly).
5. Accessible live semantics for action/error cards; search/source truth aligned for CC-off and CC-on.
6. Spoken session end → wind-down → dormant, per the review's hard-ender/soft-closer design; mid-conversation thanks never hangs up; barge-in during goodbye resumes listening.
7. Dormant-vs-engaged legibility (label + scene dimming); search worker date grounding.
8. Local reminders/my-day worker with honest limits.
9. Voice: audition note for Pras only; no default change without his selection.
10. Reachy expressiveness pass from `docs/references/2026-09-01-reachy-mini-motion-reference.md`.
11. Fast current-web spike per `2026-09-01-fast-current-web-search-spike.md`: silent route-then-answer turn and the Parallel Turbo vs Exa Instant vs Claude benchmark on Pras's US Open questions. Deliver the benchmark; no default provider switch without Pras seeing results.

Write a checkpoint handoff after each slice (exact revision, gate evidence, merged PRs, deviations, untested scope) so a context reset or morning review can resume without re-deriving.
