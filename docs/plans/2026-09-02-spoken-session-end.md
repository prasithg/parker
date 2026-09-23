# Plan: spoken session end → wind-down → dormant

Date: 2026-09-02 (overnight, after the foundation-closure gates)

Status: planned; implementation starts only after PR #40's foundation
commit (`e83fe2c`) passes real CI and the fresh-context review. Backlog
item 6 of the chairman decisions in
[2026-09-01-companion-session3.md](2026-09-01-companion-session3.md);
design constraints from the Hermes review's "Session ending" section.

## Problem

Call 41 (Pras's real-mic test): he said **"OK, thanks."**, Parker
answered, and the line sat bright-eyed in *listening* until he
power-cycled — only the 90 s + 30 s idle ladder ends a session. A
conversation that clearly ended must wind down to dormancy on its own,
without ever hanging up on someone mid-thought.

## Contract (v1)

Everything below is server-side in the bridge (`realtime.py`); the page
already returns to dormancy on the existing `closing` frame.

1. **Hard enders** — deterministic phrases on his transcript (same
   normalisation as the confirmation grammar); each ends the session
   unconditionally:
   `goodbye parker` / `bye parker` / `good night parker` / `that's all`
   / `that's all parker` / `that's it for now` / `that's it, thanks` /
   `i'm done` / `go back to sleep` / `go to sleep` / `stop listening` /
   `you can rest now`. A bare `stop`, `thanks`, `ok thanks`, `bye` are
   **not** hard enders (they occur mid-conversation or mean "stop
   talking").
2. **Soft closer** — gratitude (`thanks`, `thank you`, `ok thanks`,
   `thanks parker`, `that's helpful`, `great, thanks`) counts as a
   probable end only when ALL hold: the previous assistant turn was a
   substantive answer (≥ 6 words) that did not end with a question; no
   spoken-confirmation offer is pending; no lookup worker is in flight;
   no wrap-up/goodbye is already underway. Then Parker says one short
   goodbye and rests. Otherwise gratitude is just conversation.
3. **The goodbye** — one injected system item ("He is done — say one
   short, warm goodbye under ten words, no question, mention he can say
   Hey Parker any time") + one nudge; on that response's `done` the bridge
   sends `closing` (the existing drain handshake) and the page returns to
   dormancy with wake re-armed. Reuses `_goodbye_requested` /
   `_closing_sent`; the idle ladder must not re-fire on top.
4. **Barge-in during the goodbye cancels dormancy** — `speech_started`
   already stands the goodbye down before `closing` is sent; pin it for
   both ender kinds. After a cancelled goodbye the next gratitude may
   trigger the soft closer again (it is a fresh turn).
5. **Pending state before dormancy** — a hard ender with an open
   spoken-confirmation offer expires the offer first (`action_result
   expired`, journaled `reason: "he ended the session"`; the action stays
   staged on the family review surface, nothing runs). In-flight workers
   are cancelled by the existing shutdown policy (late results dropped).
   A soft closer never fires while an offer or worker is open (rule 2).
6. **Idle ladder unchanged** (90 s wrap-up + 30 s goodbye) — Hermes:
   keep the Parkinson-friendly window conservative until real data says
   otherwise.
7. **Journal** — `session_end` event with `kind: hard|soft`, the
   matched phrase, and what was pending, so the session review shows why
   the line closed.

## Non-goals

Dormant-vs-engaged label/scene dimming (backlog 7), My Day worker,
voice default, expressiveness, any change to the confirmation grammar or
guards, page changes.

## Verification

Bridge pins (fake upstream, keyless): hard ender → goodbye instruction +
`closing` after its `response.done`; `OK thanks` after a substantive
answer → soft goodbye → closing (Pras's exact case); `thanks` after a
question → nothing; `thanks` while a lookup is in flight → nothing, and
the result still injects; `thanks` with an offer pending → nothing (the
offer stays open); hard ender with an offer pending → offer expired first,
then goodbye; barge-in during the goodbye → no `closing`, listening
continues; bare `stop` → not an ender; `session_end` journaled with kind
and phrase. Full suite; companion Node spec unchanged (closing → dormant
already pinned). Human gate: a real-mic evening where "OK, thanks" ends
the session and a mid-conversation "thanks" does not.

## Implementation and evidence (2026-09-02, branch `fable/spoken-session-end`)

Implemented as planned in `realtime.py` (`spoken_session_end`,
`_maybe_end_session`, `_SESSION_END_INSTRUCTION` / `_SOFT_CLOSE_INSTRUCTION`,
`session_end` journal event; barge-in during the goodbye clears the end),
plus backlog item 7's legibility half that needed no page redesign:

- the switch reads **"Resting — say “Hey Parker”"** while dormant (only an
  open line says "Parker is on");
- the renderer dims the whole room to rest: a `sceneLight` spring drives
  the hemisphere/key/rim lights (offline 0.28, dormant 0.36, awake 1.0);
  verified from the scene's own `debug()` readout in a real browser —
  key light 0.53 offline → 0.68 resting → 1.9 listening, eye ember and
  head drop unchanged, zero console errors.

Deck `tests/test_scenarios_session_end.py` (9 scenarios): grammar pins
(whole-utterance/ending match only — "I'm done with the tennis, what
about golf?" is a question); "OK, thanks." after a real answer → soft
goodbye → `closing` (call 41's case); "that's all" → goodbye → `closing`;
"thanks" after Parker asked a question → nothing; "thanks" with a lookup
in flight → nothing and the result still injects; "thanks" with an offer
pending → the offer stays open and his later "yes" executes; "that's all"
with an offer pending → the offer expires first (`action_result expired`,
journaled), nothing runs; speaking during the goodbye → no `closing`,
listening continues, a later "that's all" ends it; bare "stop" → nothing.
Full suite 1241 passed on the worktree before the legibility edits;
re-run recorded in the PR.

Human gate: a real-mic evening where "OK, thanks" ends the session and a
mid-conversation "thanks" does not; the resting label/dimming judged on the
living-room screen.

## Fix round (2026-09-02, after the fresh review of `304db95`)

The review found the ending match fired on questions and reports that
merely end with an ender phrase ("should I go to sleep?", "I can't go to
sleep", "you said that's all") — the hang-up-mid-thought failure. Fixes
(`92c82d4` … `ace3ed7`): a transcript ending in `?` is never an exit; an
ender is the whole utterance or its ending after a bounded whitelist lead
(≤ 3 words) with bounded trailers peeled in every order; wider ender list
("that's all for today", "goodbye", "good night", "we're done", "i'm
finished", "go to sleep parker"); gratitude as a small regex; the
watchdog's stand-down clears the end kind; S03/S08 feed `done()` so their
"no closing" proof is real; S09 pins barge-in during the soft goodbye; S10
pins "Should I go to sleep?". Five realtime tests that raced the test
client's cancel-on-exit now wait for the ack they judge (the CI flake).

## Fix round 2 (2026-09-02, PR #43 independent review → Phase 0 integration)

The review's blocker: bare `thanks` / `thank you` classified as gratitude
and, after any six-word non-question answer with nothing pending, began a
goodbye — a common mid-conversation acknowledgment, and a Parkinsonian
pause after it must never read as completion. Reproduced on the
integration tree (bare "thanks" → `sounds finished` injected → `closing`;
a follow-up that landed after the pause was lost because the page had
returned to dormancy).

Change (`realtime.py`): contract rule 2 is narrowed to **compound closers
only**. A new `_SOFT_CLOSER_RE` is the sole top-level classifier: an
acknowledgment lead (`ok`/`okay`/`alright`/`all right`/`great`/`perfect`/
`good`/`wonderful`/`lovely`/`fine`/`right`) plus `thanks`/`thank you`
(optionally `so much`/`very much`/`a lot`), or `that's helpful, thanks` /
`that helps, thank you`, optionally followed by `Parker`. Bare `thanks`,
`thank you`, `thanks Parker`, `thanks so much`, and `that's helpful` alone
are conversation. The broad `_GRATITUDE_RE` stays as the LEAD matcher for
hard enders (`thank you so much Parker, that's all` still exits); the
context gates in `_maybe_end_session` are unchanged and never see bare
gratitude.

Deck: grammar rows updated (compound closers vs bare gratitude); S03/S04/
S05 now speak `OK, thanks.` so the question, lookup, and offer gates are
what hold (bare "thanks" would pass them vacuously); new S02b pins bare
`thanks`/`thank you` after a substantive answer → no goodbye, and a later
question is answered normally with no `session_end` event; new S02c pins
`that's helpful, thanks` → soft goodbye → `closing`.

Untested here: a real Parkinson-length pause (the deck represents it as a
bounded negative wait; the 90 s + 30 s idle ladder is the only real-time
hazard and is unchanged). Human gate unchanged: a real-mic evening where
"OK, thanks" ends the session and a mid-conversation "thanks" followed by
a long pause and a follow-up does not.
