# Foundation closure — overnight builder session (PR #40 blockers)

Date: 2026-09-01 (night) → 2026-09-02 (morning review)

Status: active task ledger for the autonomous Fable session Pras started at
`333df4b` on `fable/reachy-companion-take2`. Acceptance source:
[PR #40 independent review](../reviews/2026-09-01-pr40-independent-review.md)
plus the chairman decisions appended to
[the session-3 plan](2026-09-01-companion-session3.md) (`3fe8535`): wake
stays generous for Dad, per-slice gate = strict suite + real CI + fresh
review, power/wake/safety code stays on the branch for Hermes, independent
fixes land from main as their own PRs.

## Ledger

| # | Slice | State | Evidence |
|---|---|---|---|
| 1 | Deterministic test DB + thread exceptions fail + per-thread assertions | **merged** — PR #42 → main `0e852d7` (CI green, fresh-context review PASS); main merged into PR #37's branch at `9e2bd92` | see below |
| 2 | Power: server-authoritative, single owner, acknowledged, fail-closed | implemented on PR #40 branch | `companion_power.py`, router, page, 18 Python + 10 Node pins |
| 3 | Bounded live reconnect | implemented | one retry per activation, then rest + honest note |
| 4 | Missing local wake ASR fails closed | implemented | `unavailable` → power OFF + alert card; never cloud audio |
| 5 | Wake: drop bare `a`, split-syllable recall, same-breath tail handoff | implemented | grammar tests; wake→tail→hello→greeting pinned end to end |
| 6 | Ambient-TV CPU/false-wake soak evidence | evidence produced; adaptive gate shipped **opt-in (off)** (see "Wake soak"); over-TV limit measured and named | `scripts/wake_soak.py` → `benchmark/reports/wake_soak_2026-09-01*.md` |
| 7 | Packaged Tauri opens the companion; WKWebView lifecycle | implemented + **packaged headless probe PASS** (`scripts/packaged_companion_probe.sh`: real Parker.app, scratch home, companion window opened on boot, page/Three.js fetched, `webgl_ready` receipt from the WKWebView, no power claim / wake socket (mic not observed), clean teardown; 16/16 Rust tests) | power/wake click in the packaged window = human gate |
| 8 | Accessible live cards; search/source truth CC-off/CC-on | implemented | two live regions; CC-on "Checked the web · labels"; prompt aligned |

## Gate status for the foundation commit (`e83fe2c`)

1. Full suite with thread exceptions as errors: **1232 passed** on the exact
   revision; concurrency-sensitive set strict ×5 (5/5); Node companion spec
   29/29; Rust 16/16.
2. Real CI on PR #40's head `e83fe2c`: **green** (run 33589169438 — the
   `pull_request` event did not fire for the merge push, so the run was
   dispatched manually on the same head; nothing was re-run to obtain green).
3. Fresh-context review of `e83fe2c`: **NEEDS_FIX** — two real defects the
   builder introduced tonight, both fixed in the follow-up commit below:
   - the adaptive gate treated ~7 s of his own speech as "the room" and
     swallowed a wake at the same loudness (unmeasured by the soak, whose
     positives were isolated from silence). Fix: the gate is now **opt-in
     (`parker_wake_relative_gate = 0`)** — a missed wake costs Dad more than
     CPU costs the machine — and its design engages only after ~20 s of
     continuously loud room against a 25th-percentile background over
     60 s, pinned by `test_a_wake_after_his_own_speech_is_never_gated`;
   - the page replaced the wake frame's words with each post-window tail
     frame ("can you" was lost). Fix: the wake frame's words stay in front;
     the Node pin now uses post-window frames the engine can actually
     produce.
   Non-blocking items fixed too: a boot claim refused by a reload race
   retries once; a refused reconnect after an engine restart re-claims
   (durable ON, unowned) instead of announcing "turned off"; a wake hit
   racing a revoke no longer raises out of the route; refusal after a
   failed registration names the real reason; persistence runs off the
   event loop; `aria-checked` is true only when actually on; expression /
   wake-tail journaling runs off the browser pump (Hermes blocker 7's
   "evidence must not block audio forwarding"). Noted, not changed: the
   owner token rides the websocket query string into the local access log
   (localhost-only, single household).

## Slice 1 — the false-green root cause

Root cause: the `db` fixture was `sqlite:///:memory:` on a `StaticPool` —
one connection shared by every Session on every thread. A reader's
`Session.close()` on another thread is a ROLLBACK on that same connection,
which silently discards a writer's flushed-but-uncommitted transaction.
Measured on the old fixture with the exact interleaving (writer flush →
reader close → writer commit): **row lost in 10/10 rounds, zero
exceptions.** That is the mechanism behind the concurrent-session crash
(`no more rows available` / `Could not refresh CallLog`), the CI failure at
`333df4b` (`_stage_proposal_sync` → `db.refresh(ResolutionResult)`), and
every "unreproducible" realtime flake the verify-after-commit code was
defending against.

Fix (from main, as its own PR per the merge policy):

- `tests/conftest.py`: one file-backed SQLite per test (own temp dir, WAL,
  30 s busy timeout, per-connection isolation exactly like production);
  teardown disposes the engine instead of `drop_all`.
- `backend/pytest.ini`: `PytestUnhandledThreadExceptionWarning` and
  `PytestUnraisableExceptionWarning` are errors.
- `test_concurrent_session_creates_load_the_model_exactly_once`: every
  thread's result/exception is asserted, four distinct sessions, four
  call-log rows.
- `tests/test_db_fixture_isolation.py`: the harness pin — the reader-close
  interleaving must keep the write; three threads get three connections.

Evidence (this checkout, before the tmp-dir move): strict concurrent-create
12/12; the two realtime confirm/finalize tests that failed under review
8/8 strict; converse+wake 81 passed; realtime+scenarios 127 passed; full
suite 1180 passed + 1 practice test that asserted an empty `tmp_path`
(fixed by giving the DB its own temp dir). Suite wall time 69 s locally.

## Slices 2–5, 8 — design decisions (for Hermes's fresh review)

**Power authority (`backend/app/parker/companion_power.py`).** The engine
owns power. `POST /parker/converse/companion/power {on, client_id}`:

- `on` = *claim*: persist first (a failed write → 503 `not_saved`, nothing
  is on), then issue an owner token + generation. Refused with 409
  `elsewhere` while another `client_id` still holds a live wake/realtime
  socket — a screen that is actually listening cannot be silently
  displaced. A stale owner with no socket (permission still pending, dead
  tab, engine restarted) IS displaced by the next claim.
- `off` = *release*: in-memory off and generation bump first, every
  registered socket gets `{"type":"revoked","reason":"power_off"}` and is
  closed, then the flag persists; `saved:false` tells the page the write
  failed while every line is already dead.
- `/converse/wake` and `/converse/realtime` read `?owner=&gen=` and answer
  `revoked` (`power_off` | `not_owner`) before any audio is read. A second
  realtime socket from the owner supersedes the first (the page's own
  reconnect); `MAX_LIVE_BRIDGES` stays 2 only for that handover overlap.
- The settings route refuses `power_on` (400): no page can write power
  behind the authority. GET returns the persisted flags plus the live
  snapshot (`gen`, `owner_client`, `live` socket counts).
- After an engine restart nobody owns power; the booting page claims
  before listening — a pre-restart token never authorizes.

**Page (`companion_ui.py`).** The switch shows ON only after the claim is
acknowledged; 409 → `elsewhere` state ("On another screen" — the switch
here turns Parker off everywhere); 503/unreachable → OFF + alert card,
nothing acquired. Off releases mic/sockets/speech/playback first, then
persists with three bounded retries (1 s, 3 s, 8 s) and a persistent
alert if the write never lands. A `revoked` frame turns this screen off
*without* posting off (that would turn off the new owner) and names why.

**Reconnect.** One retry per activation (reset only by his next wake or a
power flip); a second drop rests dormant with "The line dropped. Say
“Hey Parker” to try again." No third socket, ever, without him.

**Missing local wake ASR.** `unavailable` → `powerOff()` (persisted) +
"Wake listening needs the local voice model on this computer, so Parker
stayed off. Ask the family to run make voice-deps." Deliberate deviation
from the review's "require an explicit user decision": there is no
family-admin surface for such a decision in 0-1 mode, and the only
honest fail-closed state is OFF. Flagged for Hermes.

**Wake grammar (chairman calibration).** Greetings lose bare `a`;
`parker`-like = exact set ∪ edit-distance ≤ 1 ∪ `park…` prefix minus real
park-words; a split syllable (`hey par ker`) joins. `hey darker/marker/
barker/packer/parked` remain accepted extra wakes and are pinned as such
so tightening is a conscious change; `a parker`, `hey park the car`,
`the parker brothers` stay quiet.

**Same-breath tail (pinned handoff contract).** The wake frame carries
`tail` (words after the wake phrase inside the window). After a wake the
lane stays open ≤ 3 s sending `tail` frames (post-wake transcript on each
energetic hop) while mic frames keep feeding it; the page opens the
realtime line, sends `{type:"hello", tail}` as the FIRST frame, then ends
the wake lane and switches the mic to the line. The bridge waits
`HELLO_WAIT_SECONDS` (0.35 s) without ever cancelling a receive; a tail
reshapes the greeting instruction ("skip the standalone greeting — answer
that") and is journaled as `wake_tail`; a late or second hello is ignored;
the tail is capped at 200 chars.

**Cards.** `#card` = `role=status aria-live=polite aria-atomic`; `#alert`
= `role=alert`; failures/errors/guard go assertive, offers/outcomes/
notices polite; one visible at a time. **Search truth.** CC on: "Checked
the web · ≤3 labels ≤40 chars" for 12 s, never URLs; CC off: zero chrome
(Reachy work cue + Parker's spoken acknowledgment); the realtime prompt
no longer promises sources on screen.

**Tauri (slice 7).** `desktop/src-tauri/src/lib.rs`: a `companion` window
(`/parker/converse`, fullscreen) opens on boot once onboarding is complete
and from the tray's first item "Open Parker"; opening it takes the
microphone-transition gate and kills the legacy TALK sidecar; an open
companion blocks the legacy first-session TALK start (`BlockedByCompanion`)
and "Start Listening". Dad Screen stays as "(legacy)". WKWebView review
(report only): wry 0.55.1 auto-grants `requestMediaCapturePermissionFor`
(TCC prompt attributed to Parker.app via the existing
`NSMicrophoneUsageDescription`), autoplay defaults on (no
`mediaTypesRequiringUserActionForPlayback`), `http://127.0.0.1` is a secure
context, nothing disables WebGL/JS. The companion now posts a
`webgl_ready`/`webgl_fallback` receipt so the packaged run is judged from
the engine's records.

**PR #37 sync.** main (with PR #42) merged into `fable/reachy-mini-converse-3d`
at `9e2bd92`; its suite 1144 passed locally and CI is green.

## Wake soak — the evidence Hermes asked for (synthesized, real model)

`scripts/wake_soak.py`: 4 min of TV-like speech (five macOS voices reading
news/sports/ad copy stuffed with parking/parked/Parker Brothers/Peter
Parker/darker/marker/packer/barker) through the real `WakeDetector` and the
real local faster-whisper, in browser-sized 16 kHz frames; then a recall
matrix (8 effortful positives × 3 voices × 2 rates), the review's
confusables, and positives mixed OVER the TV audio at fixed voice/TV SNRs.
Reports: `benchmark/reports/wake_soak_2026-09-01*.{md,json}`.

| variant | inferences/min | CPU (fraction of one core, continuous) | p50 / p95 ms | false wakes / 4 min | recall (48) |
|---|---|---|---|---|---|
| `base`, auto threads (**current production**) | 78 | **2.65** | 464 / 751 | 1 | 46/48 |
| `base`, `cpu_threads=2` | 78 | 1.56 | 560 / 639 | 1 | 46/48 |
| `tiny.en`, auto threads | 78 | 2.21 | 390 / 877 | 0 | 48/48 |
| `base` + adaptive gate 1.3× (**opt-in**, first design) | **13.5** (54 total) | **0.46** | 511 / 721 | 0 | see report |
| `base` + gate 1.3× + `cpu_threads=2` | 13.5 | 0.31 | 662 / 802 | 0 | see report |

(Variants after the baseline ran with other builds/tests on the machine;
their latency columns are inflated by contention, the CPU-seconds and
inference counts are not.)

Findings so far:

- **CPU.** With a TV on, every 0.7 s hop is "energetic", so the model ran
  continuously: ~2.6 cores on the previous defaults, ~1.5 with a 2-thread
  cap, and a smaller model barely helps (tiny.en 2.2 — per-window decode
  overhead, not model size, is the cost). **The lever is inference count:**
  the adaptive relative-energy gate (`WakeDetector(relative_gate=1.3)`,
  available opt-in via `parker_wake_relative_gate`; production default 0)
  runs the model
  only when a hop is 1.3× louder than the room's trailing median — a voice
  near the mic rises above steady TV, the TV never rises above itself:
  312 → 54 inferences, 2.65 → 0.46 cores, 0 false wakes. **Shipped
  opt-in, off by default** after the fresh review found the first design
  gated a wake that followed his own speech; the redesigned gate engages
  only after ~20 s of steadily loud room (a TV) against a low-percentile
  background, so a few seconds of his own talking do not become the
  background (≈24 s of unbroken talk still would, until a 2.4 s pause) —
  enabling it is a room-calibration decision for the family, not a
  default.
- **False wake.** One in four minutes: the TV said "…an actor named Parker
  something. Hey…" and Whisper heard "Hey, I'm Parker something" — a
  greeting two tokens before a parker-like token is a wake by design
  (chairman: generous for Dad). Reported, not "fixed".
- **Recall in quiet.** 46/48 on `base` (48/48 on `tiny.en`); the two
  `base` misses ("hey parka" Fred@175 → "Hey, part of"; "um, hey parker"
  Samantha@175 → "Um, hey park") were a **harness artifact**: the recall
  audio ended with 0.5 s of silence, less than one 0.7 s hop, so the
  detector's final inference ran mid-word and no later hop ever came. A
  live microphone keeps streaming silence, so the lane always gets a hop
  with the whole phrase. The soak now pads 1.6 s: re-run on `base`
  (`wake_soak_2026-09-01_recall-padded.md` — its config is
  `relative_gate=1.3`, `threads=0`, not the production default 0; the gate
  only engages after ~20 s of steadily loud room, which an isolated
  positive never provides, so the recall rows are unaffected by it, but
  the report also ran no soak, so its "0 false wakes" is not evidence):
  **48/48**. The slow/effortful rate (120 wpm) woke every time regardless.
  A re-run at the production gate is queued with the achieved-SNR
  regeneration below.
- **Over the TV — the real-room problem.** Positives mixed INTO continuous
  TV speech (`wake_soak_2026-09-01_overtv-sweep.md`, base model):

  | voice / TV (REQUESTED, see caveat) | "hey parker" | "hey parker, can you help me" |
  |---|---|---|
  | +12 dB (he is much louder) | 0/2 | 2/2 |
  | +6 dB | 0/2 | 2/2 |
  | 0 dB (equal) | 0/2 | 0/2 |
  | −6 dB | 0/2 | 0/2 |

  (Caveat, 2026-09-02 F8: the dB column is what that harness *requested*,
  not what the mix achieved — it capped the voice gain at 3× and mixed
  with a saturating add, so +12 landed at roughly +4 to +8 dB depending
  on the voice, and for one voice the +6 and +12 rows are the same audio
  counted twice. A morning correction claimed 1/2 on the bare phrase at
  +6/+12 from a same-harness re-run, `wake_soak_2026-09-02_baseline-overtv.md`,
  but that report was never committed and is not on this tree, so the
  rows above are the checked-in `overtv-sweep` numbers. `wake_soak.py`
  now attenuates the TV bed instead of over-driving the voice, labels
  every row with the achieved SNR, excludes duplicate mixes, and reports
  INCOMPLETE rather than PASS when a section did not run; the regenerated
  `wake_soak_v1` report supersedes this table.) Whisper keeps transcribing the TV through
  a short overlapping phrase; a longer same-breath utterance gives it
  enough voice to switch. Honest product statement until a wake path
  robust to overlapping speech exists (a dedicated small wake model on
  consented samples, or TV-feed cancellation): **dormant wake works in a
  quiet room; over TV speech it needs him clearly louder than the TV and
  works best with a full sentence ("Hey Parker, can you…"); it does not
  work at equal loudness.** That is the evening human gate's real question
  and a next slice — not a grammar tuning. A burst-focused second look was
  tried (`fable/wake-burst-window`): against the corrected baseline it
  showed **no measurable gain** (2/4 → 2/4), so it is not shipped.
- **Same-breath tail.** The wake window itself rarely holds the request
  ("Hey Parker, can you help me" → window heard "Hey Parker, Ken"); the
  post-wake tail lane is what carries it (recall rows' "tail after wake"
  column), which is why the handoff contract exists.

## Follow-on slices started tonight (stacked, each with its own PR)

- **PR #43** `fable/spoken-session-end` (on PR #40): spoken session end →
  wind-down → dormant; "Resting — say Hey Parker" label; the room dims to
  rest ([plan](2026-09-02-spoken-session-end.md)).
- **PR #44** `fix/search-worker-date-grounding` (from main, per the
  merge policy): the search worker knows today's date/zone.
- **PR #45** `fable/my-day-worker` (on PR #43): the local "my day" tool —
  medicine times by name, reminders he set, family notes, and the honest
  "no calendar" limit ([plan](2026-09-02-my-day-worker.md)); fix round
  after its fresh review (limit line unconditional; honest store failure).
- **PR #46** `fable/reachy-motion-vocabulary` (on PR #45): the beat layer
  from Hermes's motion reference — staged wake beat, acknowledgment,
  phrase micro-nods from real transcript punctuation, restrained outcome
  beats, idle weight shift, `advance()` for numeric verification
  ([plan](2026-09-02-reachy-motion-vocabulary.md)).
- `fable/wake-burst-window` (PR #47, experiment, opt-in): a second look at
  just the loud burst when the last 1.3 s of the window is ≥1.25× louder
  than what came before. Its fresh review caught that the "0/4 → 2/4"
  claim compared against a non-same-harness baseline; the same-harness
  baseline is already 2/4, so the burst window shows **no measurable
  gain** on this synthetic set (+12% CPU, no extra false wakes). Kept as
  a recorded negative result; not merged.

Each stacked PR had a fresh-context review and a fix round where it
returned NEEDS_FIX; every fix round added the pin that would have caught
it. Every head was run on real CI (dispatched manually where the
`pull_request` event did not fire).

## Next-slice candidates surfaced tonight (not started)

- `benchmark/evaluate_curiosity_loop_v0.py` still runs threads over a
  StaticPool in-memory DB — the exact class PR #42 removed from the suite.
- Wake over TV speech: dedicated wake model (openWakeWord/custom "hey
  parker" on consented samples) or TV-feed cancellation; the soak's
  over-TV rows are the acceptance test.
- `--as-of` + `--write-report` mutual exclusion in the readiness evaluator
  (reviewer nit).

## Morning summary (2026-09-02, end of the overnight session)

Delivery state per PR — every code head ran the full suite with thread
exceptions as errors, had a real CI run, and a fresh-context review; every
NEEDS_FIX became a fix commit with the pin that would have caught it.

| PR | Branch (stacked on) | State | Fresh review | Human gate |
|---|---|---|---|---|
| #42 | `fix/deterministic-test-db` (main) | **merged** `0e852d7` | PASS | — |
| #44 | `fix/search-worker-date-grounding` (main) | **merged** `be91ecc` | PASS | one real lookup on a "what's on tonight" question |
| #37 | `fable/reachy-mini-converse-3d` (main) | synced with main `9e2bd92`, CI green | Hermes owed | real mic |
| #40 | `fable/reachy-companion-take2` (#37) | head `cb277c6` (code `a454b27`), CI green | NEEDS_FIX → fixed → narrow NEEDS_FIX → fixed; **Hermes owed** (two same-family cycles used) | real mic, evening false-wake watch, power/wake click in the packaged window |
| #43 | `fable/spoken-session-end` (#40) | head `3f9bc4b` (grammar follow-up `2bf9bf5`), CI green | NEEDS_FIX → fixed → **PASS** | "OK, thanks" ends a real session; mid-conversation thanks does not |
| #45 | `fable/my-day-worker` (#43) | head `6ae0743` (`b157781` follow-up), CI green | NEEDS_FIX → fixed → **PASS** | "what do I have today" in a real session |
| #46 | `fable/reachy-motion-vocabulary` (#45) | head `11e10d4` + sr-status nit, CI green | NEEDS_FIX → fixed → **PASS** (note: the live off/dormant pose now visibly sinks — the head-drop spring had never been stepped) | clips judged against the reference / the physical Reachy |
| #47 | `fable/wake-burst-window` (#40) | **closed** — recorded negative result | NEEDS_FIX (valid) | — |

Merged to main tonight: #42, #44. Everything touching power, wake,
confirmation, or the bridge stays on the stack for Hermes per the chairman
policy. Worktrees left under `~/Operations/worktrees/` for the stack
branches (`parker-3d-sync`, `parker-session-end`, `parker-my-day`,
`parker-reachy-motion`); the main checkout is clean on
`fable/reachy-companion-take2`.

Deliberate deviations (yours to accept): fail-closed OFF for missing wake
ASR; the single-owner power contract replaced the "two live lines" deck;
the adaptive gate ships opt-in (off) after its first design gated a wake
after his own speech; the burst window is not shipped (no measured gain).

What remains untested, in one place: real microphone in the room (wake,
tail handoff, spoken confirmation cycle, spoken session end), evening
false-wake watch with Dad's voice, the power/wake click inside the
packaged WKWebView (the headless probe covered boot/WebGL/teardown only),
the voice audition, the Reachy clips against the physical robot.

Next owner: Hermes — fresh cross-family review of PR #40 at `a454b27`
(+ docs), then #43/#45/#46 in stack order; Pras — the human gates above.
