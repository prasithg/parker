# Handoff: the fast-voice orchestrator architecture

Date: 2026-08-30
Status: **BUILT** same day (branch `fable/fast-voice-orchestrator`) — see
`docs/brain-adapters.md` ("The realtime lane: the fast-voice
orchestrator") for the shipped design, `app/parker/realtime_workers.py` +
`app/parker/realtime.py` for the code, `backend/tests/test_realtime.py`
for the pinned contract, and `docs/personas/ravi.md` for the north-star
persona. Q1: taxonomy v1 = context + search (actions kept on their own
path). Q2: two-step function ack + system-item injection, one gated
response.create emitter, question+age echoed for the stale judgment; late
results after close are dropped (Pras). Q3: realtime-lane-only. Q4:
receipts logged under parker.realtime; `make live-voice-probe` prints
them. Q5: fake upstream grew thread-safe feed(); ordering, guard, idle,
and persistence contracts are all pinned. This doc stays as the idea's
origin record.
Prior context: the 2026-08-29/30 sessions (curiosity harness → general web
search → streaming/presence → gpt-realtime lane), all merged at `0069320`.

## Pras's idea, verbatim spirit

> A realtime or super-fast model holds the conversation, while background
> worker threads are getting context or doing tool calls. The main
> conversation thread asks follow-ups etc., and when the background agents
> come back, the main super-fast agent steers the convo, answers the
> question, or does the action.

The shape: **conversation never blocks on work**. The fast front model owns
presence, repair, and pacing; slow things (web search, deeper reasoning,
pipeline actions, OpenClaw skills) run behind it and their results are
*injected into the live conversation* when ready, letting the front model
steer — "Right, found it: the Bulldogs won by three" mid-chat, or "That
reminder's on the screen now."

## What already exists to build on (repo-grounded)

- **The live lane is running.** `app/parker/realtime.py` — browser ↔ Parker
  relay ↔ `gpt-realtime-2.1`, semantic VAD (low eagerness), barge-in,
  `propose_action`-only tool surface staged through the pipeline, post-hoc
  transcript guard, Dad-screen mirror. `OPENAI_API_KEY` verified working
  2026-08-30 (`realtime_available: true`).
- **The injection point exists.** The bridge already sends
  `conversation.item.create` (function_call_output) + `response.create` —
  exactly the mechanism a background worker's result would use to hand the
  front model something new to say. A worker result can be delivered as a
  function output or a system-ish conversation item, then `response.create`
  lets the fast model narrate it.
- **The slow lane is the existing brain.** `ClaudeBrainAdapter` with web
  search + citations→sources (`app/brain/claude.py`) is the natural first
  background worker: give the realtime session a `look_that_up` /
  `get_context` function; the bridge acks instantly (front model says "let
  me check while we talk"), runs the Claude search in a thread, and injects
  the answer + sources when it lands. Sources go to the screen (the live
  lane currently has none — a known gap, verifier finding).
- **Actions already follow this pattern.** `_stage_proposal_sync` is a
  background worker in miniature: async function call → threadpool → result
  injected back. Generalize it.
- **Guards**: the post-hoc transcript guard and the one-policy-per-lane rule
  (adversarial round 2) must cover anything a worker injects — a worker
  result is brain output and gets screened like brain output.

## Questions for the next session's planning pass

1. Worker taxonomy v1: just `search` + `propose_action`, or also
   `remember`/context-recall? (Smallest set that proves the steer-back.)
2. Injection contract: function_call round-trip vs unsolicited conversation
   items — which keeps gpt-realtime coherent when results arrive mid-turn or
   after topic change? (Stale-result policy: the front model should be told
   *what the question was*, so it can drop an answer the conversation moved
   past — the Stop-generation lesson applies here.)
3. Does the patient Start/Done loop get the same pattern (streamed cue "let
   me check" already exists there), or is this realtime-lane-only?
4. Latency budget: front-model ack < 1 s; worker results whenever — measure
   with the existing receipts.
5. Eval: extend `test_realtime.py`'s fake-upstream pattern — scripted worker
   delays, result-after-topic-change, guard on injected text.

## Boundaries unchanged

Same safety envelope: workers can read and propose, never execute without
the confirmation pipeline; medical/emergency/finance guards on everything
spoken; no new lanes per subject (general search stays the worker, per
Pras 2026-08-30).
