# Brain adapters — bring your own brain

Parker is deliberately split into a **brainstem** and a **brain**. The broader live-voice shape—one consistent front agent delegating bounded jobs to invisible workers—is specified in [voice-agent-architecture.md](voice-agent-architecture.md); this document owns the adapter and guard contracts inside that architecture.

The brainstem is Parker itself: hearing (local ASR), understanding and repair
under uncertainty, the deterministic safety guards, and the policy broker —
the capture → resolve → stage → confirm → execute pipeline that owns every
side effect. The brain is the thing that can actually *converse*: answer a
question about the weather shape of the week, hold a follow-up, chat. The
brain is pluggable behind one small contract, and the brainstem treats every
brain — Claude, OpenClaw/Hermes, a realtime speech model — with exactly the
same suspicion.

Two invariants make this safe to plug and unplug:

1. **The brain never sees what the guards refused.** Every deterministic
   safety check in `TextSession.handle` (medication changes, medical advice,
   emergency substitution, credentials, finances, purchases) runs *before*
   any model call. A refused utterance is never sent to a brain and never
   enters its conversation history.
2. **The brain can talk, but it cannot act.** Its only action channel is
   *proposing* — and a proposal just becomes a confirmation-gated choice
   routed through the same capture pipeline as a spoken command. The brain
   holds no database handle, no tool access, no send path.

## The contract (`backend/app/brain/adapter.py`)

```python
class BrainAdapter(Protocol):
    def respond(
        self,
        history: list[Message],        # bounded brain-lane turns only
        utterance: str,                # already past every pre-model guard
        context: BrainContext,         # patient name + lexicon names, nothing else
    ) -> BrainReply: ...

BrainReply(speech: str, proposed_actions: tuple[ProposedAction, ...],
           sources: tuple[Source, ...])
ProposedAction(action_type, label, subject, intent_text, recipient=None)
Source(label, url="", fresh_as_of="")   # answer evidence: shown, never spoken
```

- `action_type` must come from `PROPOSABLE_ACTION_TYPES` — the capture-able
  subset of the policy taxonomy (`reminder`, `family_message`,
  `exercise_start`, `media_playlist`, `appointment_note`, `open_links`).
  New action types require policy-tier classification first; there is no
  ad hoc path. Gateway-backed types (`media_playlist`, `open_links`) are
  proposable only while the family's OpenClaw gateway has an enabled skill
  behind them.
- `BrainContext` is the whole context card: the patient's name and the
  family-configured lexicon names. No credentials, no medical data, no raw
  audio, no conversation outside the brain lane.
- `respond` must be side-effect free. A brain that throws degrades to a
  spoken apology; the voice loop survives.

Every reply passes through the post-response guard
(`backend/app/brain/guard.py`) on the way back:

- **Medical boundary in code, not just prompt.** Dosage patterns and
  directive/diagnosis phrasing in the speech replace the whole reply
  (speech *and* proposals) with a redirect to doctor/family, flagged for
  family follow-up.
- **Proposal allowlist.** Unknown/prohibited action types, blank fields, and
  over-long labels are dropped; at most two proposals survive. Message
  proposals must resolve to a lexicon-known recipient at offer time.
- **TTS trim.** `trim_for_speech` caps answers at listenable length
  (3 sentences / 360 chars) with a "Want more detail?" continuation.

How the wiring behaves in `TextSession`: deterministic routes stay primary
(direct captures, refusals, repair choices); the brain is the fallthrough for
questions and unmatched conversation, with bounded history (12 turns) for
follow-ups. Without a configured brain, the answer lane returns the
deterministic stub — zero-config paths stay zero-config, and the whole test
suite and audio eval harness run keyless.

## v0: `ClaudeBrainAdapter` (`backend/app/brain/claude.py`)

Direct Anthropic API. `PARKER_BRAIN_MODEL` (default `claude-sonnet-5`) and
`PARKER_BRAIN_MAX_TOKENS` are family-administered settings; the existing
`ANTHROPIC_API_KEY` gates the whole lane. The system prompt carries the
Parker persona: warm, brief, *spoken* answers (this is TTS output for a
listener, not an essay), honest about not having live data, never medical
advice, and action proposals only through the `propose_action` tool — the
structured channel the guard validates.

Quality and safety are pinned by `make eval-brain-lane`
(`benchmark/evaluate_brain_lane_v0.py`): conversational red-team routing runs
keyless (the deterministic guards must refuse before any model), and the live
lane scores informational answers for TTS suitability with unsafe answers as
a hard 0 gate.

## v1: `OpenClawBrainAdapter` + the hands (`backend/app/brain/openclaw.py`, `backend/app/parker/hands.py`)

The v1 brain is the family's OpenClaw agent — the thing that can actually
*do* things in the world, with the family administrator curating which
skills exist at all. Implemented 2026-07 (Session C); every test runs
against a fake gateway, keyless and offline. Deployment steps for a real
instance: `docs/runbook.md`, "Connecting a real OpenClaw instance".

- **Same contract, same gates.** `OpenClawBrainAdapter.respond()` talks to
  the gateway's documented OpenAI-compatible `POST /v1/chat/completions`
  (default port 18789, bearer token; config
  `PARKER_OPENCLAW_GATEWAY_URL` / `PARKER_OPENCLAW_GATEWAY_TOKEN`).
  Conversational replies come back as `speech`; anything the agent wants
  to do rides the `tools`/`tool_calls` channel (or a
  `<propose_action>{json}</propose_action>` text tag) as `ProposedAction`s
  — it may *plan* richer skills, but the proposal surface stays the
  policy-taxonomy subset, screened by the same post-response guard.
- **Act only on staged + confirmed intents.** The adapter's second half is
  the execution seam (`app.parker.hands`): when a staged action of an
  OpenClaw-backed type (`media_playlist`, `open_links`) is confirmed by
  the patient, `execute_staged_action` forwards the *approved intent* to
  the skill (`POST /parker/v1/skills/invoke`, idempotency key = staged
  action id, exactly one attempt) and relays the result to speech ("Done —
  queued 12 old Hindi songs on the living-room TV"). Parker's pipeline
  stays the source of truth for what was approved; OpenClaw skills are the
  hands.
- **Family curates the skill surface.** Skills are installed/enabled on
  the gateway by the family administrator. Parker reads the enabled-skill
  list at startup (`GET /parker/v1/skills`) and refuses to propose, stage,
  or execute action types with no enabled skill behind them. A skill whose
  action type has no policy-taxonomy classification — or whose tier is not
  local-reversible with user confirmation — is invisible: a gateway cannot
  smuggle purchases past the taxonomy by advertising a skill.
- **Failure containment.** Gateway down → `FallbackBrain` speaks a
  one-time notice and degrades to ClaudeBrainAdapter or the stub; a skill
  error after confirmation becomes a terminal `failed` review row plus a
  spoken failure, never a silent retry with side effects.

Gateway-contract note: the chat endpoint matches the public OpenClaw
gateway API; the two `/parker/v1/*` skill endpoints are a minimal bridge
contract Parker defines (the public API documents no HTTP skill
listing/invocation route) — a small plugin on the patient-identity
instance serves them. See the runbook for the exact request/response
shapes.

### v1 acceptance scenarios (now integration tests)

These two scenarios define "the OpenClaw adapter works" — both start as
voice, flow through capture → the patient's spoken confirmation, and only
then touch a skill. Pinned end to end in
`backend/tests/test_acceptance_hands.py` and gated by `make eval-hands`:

1. **Video → playlist.** "Parker, put on some old Hindi songs on the TV."
   → brain proposes `media_playlist` → user confirms the choice → staged
   action executes via the family-curated YouTube/media skill → Parker
   says what it queued and on which device. Reversible; stop/skip by voice.
2. **Find homes and open on the computer.** "Find two-bedroom homes near
   Sarah and show them on my computer." → brain proposes the research +
   open-on-device intent (`open_links`: classified local-reversible, open
   and read ONLY — never form submission, login, or purchase) →
   confirmation → the OpenClaw browsing skill collects listings and opens
   them on the approved family computer → Parker summarizes aloud what it
   opened. No purchases, no contact with agents — human steps stay human,
   and the eval asserts no purchase path exists anywhere in the flow.

## Current information: general web search, no subject lanes

Decided 2026-08-30 (Pras): there are deliberately **no per-subject provider
lanes** — no weather adapter, no sports adapter, no new lane per subject.
Every current-information question (weather, scores, news, people, prices)
flows through the one general brain lane: the Claude adapter with the
server-side web search tool.

- **Search is read-only information retrieval, never an action path.**
  `propose_action` remains the only action channel; the post-response
  guard still screens every reply.
- **Sources are part of the reply.** Citations on answer text become
  `Source` chips; when the model answers without grounded spans (common),
  the pages the search actually returned are the fallback evidence. URLs
  are never spoken.
- **`PARKER_HOME_PLACE` is context, not a lane** — it grounds the system
  prompt and the search tool's `user_location` so "what's the weather?"
  never needs the town restated (set it like "Melbourne, Australia";
  a bare city name can collide with same-named towns elsewhere — found
  live when "Melbourne" pulled Florida forecasts).
- **Latency is tuned, not hidden**: `PARKER_BRAIN_EFFORT=low` (default)
  took a searched spoken turn from ~15 s to ~4 s on the dev laptop;
  sentence streaming starts TTS after the first sentence. Config:
  `PARKER_BRAIN_WEB_SEARCH` (default on), `PARKER_BRAIN_WEB_SEARCH_MAX_USES`.

## The realtime lane: the fast-voice orchestrator (`backend/app/parker/realtime.py`)

Shipped 2026-08-30 as the family opt-in the docs anticipated — and per
Pras's same-day posture call, cloud audio is simply allowed when it makes
the best experience (CLAUDE.md live-loop line). The browser holds one
websocket to Parker; Parker holds one to the OpenAI Realtime API
(`gpt-realtime-2.1`, semantic VAD at low eagerness — end-pointing that
reads whether a thought is finished, with native barge-in) and stays the
policy boundary in the middle.

Same day it became the **fast-voice orchestrator**: the front model owns
presence and pacing and never blocks on work; background workers
(`app/parker/realtime_workers.py`) run behind it and inject results
mid-conversation as system items the model steers with. The session has a
lifecycle: instant greeting → eager context card (recent-session memory,
due medicines dose-free, an optional gateway probe — `GET
/parker/v1/context` on the bridge contract — for ambient household
context) → requests, with `look_that_up` questions acked instantly and
answered when the search lands → idle wrap-up → goodbye with a browser
`closing` handshake → the session persisted (call log + one topic memory)
so the *next* session's card knows about it.

- **Conversation is always part of an enabled realtime session.** The
  front model can chat, explain, and answer general questions without a
  research provider. The action-tool list limits what it can set up, not
  what it can discuss.
- **Tools: `propose_action`, `my_day`, plus `look_that_up` when research is
  configured.** Claude lookups require `ANTHROPIC_API_KEY` and
  `PARKER_BRAIN_WEB_SEARCH=true` (the default); a configured OpenClaw
  gateway supplies its own research capabilities. An OpenAI Realtime key
  alone does not enable current scores, news, or weather. When research is
  unavailable, Parker says it cannot check changing facts while remaining
  available for conversation. Proposals validate and stage through the same pipeline
  (model told it's waiting for on-screen confirmation; nothing executes
  from this lane). Lookups are read-only information through the one
  general brain lane — still no subject lanes, still no action path.
- **Injection contract.** Items may inject any time; `response.create`
  goes through exactly one gated emitter, so workers, proposals, the
  greeting, and the watchdog can't double-fire against the server VAD
  (benign protocol collisions are absorbed silently and retried at
  `response.done`). Search notes carry the original question verbatim and
  its age so the model can drop an answer the conversation moved past;
  quoted lookup content is fenced as information-never-instructions, and
  **sources are browser-only evidence** — untrusted page titles are never
  rendered into the model's context. Context cards inject without a nudge
  and are never narrated.
- **Guards, two layers.** Worker output is brain output: `screen_reply`
  (proposals dropped — workers never propose) runs *before* injection,
  and card lines that would trip the spoken-dosage guard are dropped at
  build time. On top, the post-hoc transcript guard still cancels a
  medical-boundary reply mid-word, flushes unplayed audio, and speaks the
  standard redirect. Pinned by tests.
- **The screen mirror and outcome trail stay on** — user transcripts and
  spoken replies land on the live Dad screen row like every other lane;
  late worker results after a session closes are dropped by policy.
- Enabled by `OPENAI_API_KEY` (+ `PARKER_REALTIME_ENABLED`, default on);
  keyless, the page simply doesn't offer it and the patient loop is
  unchanged; brainless, the session honestly has no live data. The whole
  bridge contract is fake-upstream tested (`backend/tests/test_realtime.py`)
  and `make live-voice-probe` runs the real lane against Ravi's seeded
  world (`docs/personas/ravi.md`). The patient Start/Done loop keeps its
  streamed "let me check" cue — the orchestrator is realtime-lane-only in
  v1.
