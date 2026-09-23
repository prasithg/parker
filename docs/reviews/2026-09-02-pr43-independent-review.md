# Independent review — PR #43 spoken session ending

Date: 2026-09-02
Reviewer: Hermes
Target: `3f9bc4b1fb243008ec0b3027450bc7fb818ff345`
Verdict: **NEEDS_FIX**

## Blocker: bare gratitude is not a conservative end signal

`spoken_session_end()` classifies bare `thanks` and `thank you` as gratitude. `_maybe_end_session()` then begins a goodbye and closes whenever the previous assistant response contains six words and does not end in `?`, provided no offer/lookup is open. That is still a common mid-conversation acknowledgment, especially for a user who may pause before adding a follow-up.

The implementation therefore optimizes the observed “OK, thanks” case by broadening it to phrases the product requirement explicitly treats conservatively. Barge-in can cancel the goodbye, but Dad may not be able to interrupt quickly and a Parkinsonian pause must not be interpreted as completion.

Narrow automatic soft ending to evidence-backed compound closers such as the exact reported `OK, thanks`/`that's helpful, thanks` forms. Bare `thanks`/`thank you` should remain conversational until real-mic evidence supports more. Add tests proving:

- `OK, thanks` after a substantive answer winds down;
- bare `thanks` and `thank you` after a substantive answer do not close;
- a delayed follow-up after thanks is not lost;
- hard enders, pending offers, lookup gating, and barge-in behavior remain unchanged.

The required real-mic gate must then exercise both the successful close and a mid-conversation thank-you followed by a Parkinson-friendly pause.

## What passed

- Exact-head remote CI succeeded.
- Focused session-end/realtime/converse deck: `117 passed`.
- The final stacked backend suite also passed.
- Hard enders are bounded to whole utterances/approved leads; questions such as “Should I go to sleep?” do not close.
- Pending confirmations expire before a hard end, and barge-in during a requested goodbye is represented and tested.
- Resting copy and room dimming make dormant state more legible without adding controls.

## Stack state

This head inherits PR #40's strict power-off and wake-tail blockers. Even after this unique fix, it must be merged/rebased onto the final reviewed foundation and reverified at the resulting SHA.

Review modified no implementation files.
