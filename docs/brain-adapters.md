# Brain adapters — bring your own brain

Parker is deliberately split into a **brainstem** and a **brain**.

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

BrainReply(speech: str, proposed_actions: tuple[ProposedAction, ...])
ProposedAction(action_type, label, subject, intent_text, recipient=None)
```

- `action_type` must come from `PROPOSABLE_ACTION_TYPES` — the capture-able
  subset of the policy taxonomy (`reminder`, `family_message`,
  `exercise_start`, `media_playlist`, `appointment_note`). New action types
  require policy-tier classification first; there is no ad hoc path.
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

## v1 design: `OpenClawBrainAdapter` (not implemented)

The v1 brain is the family's OpenClaw/Hermes agent — the thing that can
actually *do* things in the world, with the family administrator curating
which skills exist at all. This section is the design contract for a later
session; **no OpenClaw code exists in this repo yet.**

Shape:

- **Same contract, same gates.** `OpenClawBrainAdapter.respond()` talks to
  the local OpenClaw gateway (localhost HTTP/WebSocket to the family's
  Hermes instance). Conversational replies come back as `speech`; anything
  the agent wants to do comes back as `ProposedAction`s — it may *plan*
  richer skills, but the proposal surface stays the policy-taxonomy subset.
- **Act only on staged + approved intents.** The adapter's second half runs
  at the execution seam, not the conversation seam: when a staged action of
  an OpenClaw-backed type is confirmed (and, for external effects, approved
  by a caregiver), the executor forwards the *approved intent* to the
  OpenClaw skill and relays the result back to speech ("The playlist is on
  the TV"). Parker's pipeline stays the source of truth for what was
  approved; OpenClaw skills are the hands.
- **Family curates the skill surface.** Skills are installed/enabled in
  Hermes/OpenClaw by the family administrator. Parker discovers the enabled
  skill list at startup and refuses to propose action types with no enabled
  skill behind them. An OpenClaw skill with no policy-taxonomy mapping is
  invisible to the brain.
- **Failure containment.** Gateway down → the adapter reports it and the
  answer lane degrades to ClaudeBrainAdapter or the stub; a skill error
  after confirmation surfaces as a spoken failure plus a review-page row,
  never a silent retry with side effects.

### v1 acceptance scenarios

These two scenarios define "the OpenClaw adapter works" — both start as
voice, flow through capture → confirm, and only then touch a skill:

1. **Video → playlist.** "Parker, put on some old Hindi songs on the TV."
   → brain proposes `media_playlist` → user confirms the choice → staged
   action executes via the family-curated YouTube/media skill → Parker
   says what it queued and on which device. Reversible; stop/skip by voice.
2. **Find homes and open on the computer.** "Find two-bedroom homes near
   Sarah and show them on my computer." → brain proposes the research +
   open-on-device intent (informational tier + a local device action) →
   confirmation → the OpenClaw browsing skill collects listings and opens
   them on the approved family computer → Parker summarizes aloud what it
   opened. No purchases, no contact with agents — human steps stay human.

## Later: realtime speech models

Families may opt into a frontier realtime speech model (OpenAI Realtime /
gpt-realtime family) for the conversational loop as an explicit
administrator choice. That is still just a `BrainAdapter`: the realtime
session owns audio-in/audio-out, but proposals re-enter the same policy
gate, and the pre-model guards still screen the transcript before the turn
is committed. Local-first ASR remains the default.
