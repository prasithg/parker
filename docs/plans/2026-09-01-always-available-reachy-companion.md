# Product/architecture brief: always-available virtual Reachy companion + session lab

Date: 2026-09-01

Status: chairman direction; architecture and product intent approved, implementation to be decomposed by evidence

Owner for development/test sessions: Claude Fable

Overseer and independent planning/strategy/review owner: Hermes — GPT-5.6 SOL, acting AI CEO and extension of Pras

## Executive decision

Parker's primary experience is not a push-to-talk form. It is an **always-available voice companion embodied as Reachy Mini**.

The product has two intentionally separate interfaces:

1. **Companion interface** — a virtual simulation of the eventual physical Reachy experience. It shows only Reachy plus one power control. When powered on, Parker waits locally for "Hey Parker," wakes into a continuous full-duplex conversation, then returns to a dormant/inactive state when the session truly ends. When powered off, Parker does not listen, wake, stream, process, or respond.
2. **Session lab** — a separate family/admin/reviewer interface for transcripts, played speech, worker/action history, outcomes, corrections, and evidence. It is where humans can inspect what happened and teach Parker without burdening the person-facing experience.

The current Start/Done/type/choice-heavy page is useful as a developer and accessibility harness. It is not the target primary interface. Requiring a tap for each turn destroys the continuous-agent experience Parker is trying to create.

"Always available" does not mean Parker should talk continuously. It means Parker is **present, easy to wake, and continuously conversational once awake**. Silence is allowed. Parker should never chatter to fill space.

## Interface A: virtual Reachy companion

### Visual contract

The default screen contains:

- one 3D Reachy Mini embodiment;
- one clearly legible power control;
- no visible transcript, type input, numbered choices, developer timing table, source list, family details, or admin controls;
- no persistent status prose competing with Reachy;
- nonvisual accessible labels and state announcements even when visible text is absent.

A future **CC/Subtitles** setting may display what Parker heard and what audio actually played. It is off by default and is not required in the first companion slice.

Family/admin/testing controls remain available through the separate session lab or a clearly separate developer route. They are not hidden inside the companion scene.

### Power semantics

Power is a real product state, not a cosmetic switch.

#### Powered off

When off:

- microphone tracks are stopped;
- wake-word processing is stopped;
- realtime sockets and audio contexts are closed;
- browser TTS and queued playback are cancelled;
- background voice-session jobs are cancelled or safely detached according to policy;
- proactive voice offers are suppressed;
- Reachy appears physically off/inert;
- "Hey Parker" and ambient speech produce no response;
- the setting persists across app/window restart so Parker does not silently re-enable listening.

The family/admin session lab may remain accessible while voice power is off. The power control governs the companion's sensing/speaking/runtime, not the ability to review prior local records.

#### Powered on but dormant

When on and dormant:

- a **local** wake-word detector listens for the configured wake phrase;
- no continuous cloud audio stream is open;
- no general ASR or conversation model receives ambient speech;
- Reachy shows a calm, low-motion dormant state—not an active-listening claim;
- a visible/nonvisual cue makes it clear that wake-word listening is enabled;
- ambient TV/conversation silently no-ops unless the wake phrase is detected.

Local wake detection matters for privacy, cost, responsiveness, and the promise that ambient household speech is not continuously sent to a provider. The detector must be evaluated on effortful/variable speech, room distance, TV noise, and false wakes; do not copy another person's VAD/wake thresholds.

#### Activating

When "Hey Parker" is detected:

1. mint a new voice-session generation;
2. visibly wake Reachy;
3. open/activate the full voice runtime;
4. provide a brief immediate acknowledgement;
5. enter active listening without requiring another wake phrase or button;
6. journal activation latency and wake evidence.

If the runtime cannot activate, Parker gives one honest brief failure indication and returns to dormant. It must not look active while unavailable.

### Active conversational session

Once awake, Parker is full duplex:

```text
active listening
  -> hearing the person
  -> understanding / front response work
  -> speaking
  -> active listening
```

The user does not tap Start/Done and does not repeat "Hey Parker" for every turn. Semantic turn detection must tolerate pauses, restarts, trailing speech, and effortful delivery. The person can interrupt Parker naturally; Stop-to-silence and played-speech accounting remain hard contracts.

The front Parker agent stays consistent while invisible workers search, load context, reason, or stage actions. Worker progress can change Reachy's expression, but workers do not speak directly.

### "Constantly talking" clarified

The intended product quality is **continuous conversation**, not uninterrupted audio output:

- Parker keeps the conversational floor available while background work runs;
- Parker acknowledges slow work, then continues naturally;
- Parker follows relevant context and follow-ups without requiring new UI actions;
- Parker may stay quiet while the user thinks;
- Parker does not repeatedly prompt, nag, or fill silence;
- background results that arrive during the session are steered back through the same front voice;
- no worker result may cause surprise speech after the session has returned to dormant.

### Session ending and return to dormancy

"The session appears ended" must become an explicit state machine, not an arbitrary silence timer. Parkinson's-related pauses make aggressive endpointing unacceptable.

A session may end through:

- explicit phrases such as "that's all," "goodbye," "stop," or a configured equivalent;
- the physical/virtual power control;
- a long inactivity policy after one gentle optional wrap-up;
- terminal runtime failure;
- app/window closure.

Rules:

- ordinary within-turn pauses never end the session;
- a short between-turn silence does not end the session;
- the idle/wrap-up threshold is family-administered and measured, not guessed;
- Parker asks at most one low-pressure wrap-up question;
- speech during wrap-up cancels dormancy and resumes the same session;
- Reachy completes audible playback before a normal goodbye close, unless interrupted;
- returning to dormant closes the cloud/realtime session and leaves only local wake detection;
- pending read-only work is cancelled or saved for later review; it may not speak after dormancy;
- staged actions remain durable on the review surface but never execute merely because the voice session ended.

After dormancy, the next "Hey Parker" starts a fresh generation while loading bounded, provenance-tagged continuity from prior sessions.

### Companion semantic state

Use orthogonal semantic state, not a giant animation enum:

```text
power: off | on
availability: dormant | activating | active | closing | unavailable
conversation: listening | hearing | thinking | talking | yielding
work: none | context | search | reasoning | action
attention: none | waiting_for_choice | waiting_for_confirmation
guard: none | repair | redirect | error
```

The renderer is downstream. It may visualize only state proven by real runtime signals. No timer may invent thinking, work, execution, or completion.

The same semantic state should drive:

- the virtual 3D Reachy now;
- the future physical Reachy Mini later;
- accessible nonvisual state descriptions;
- bounded session-review receipts.

### Wake-word and audio architecture gaps

Before calling the companion always available, Parker needs:

- a local wake-word component and model-selection/evaluation spike;
- explicit mic ownership between dormant wake detection and the active realtime runtime;
- suppression of Parker's own output from wake/VAD input;
- generation-safe transfer from wake detector to active session;
- false-wake and missed-wake metrics;
- restart recovery and persisted power state;
- packaged Tauri/macOS microphone and wake acceptance;
- a no-cloud-during-dormancy verification path.

A touch-to-wake developer/accessibility fallback may remain available outside the visually minimal primary experience, but it does not replace the wake-word goal.

## Interface B: session lab

The session lab is separate because the person-facing companion should not look like a debugging dashboard.

### Primary users

Near term:

- Pras/family administrators;
- Parker developers/testers;
- the person using Parker, if they choose to review or correct a session.

Far future, only through an explicit program:

- trained speech/language or domain reviewers;
- research annotators operating under a defined protocol;
- model/evaluation teams receiving only the data and uses each participant approved.

### Session record

A reviewable session should expose, with clear provenance:

- wake and activation events;
- what ASR/realtime transcription heard;
- what Parker generated;
- what speech actually played before interruption;
- semantic Reachy states and transitions;
- worker dispatch, acknowledgement, completion/failure, sources, and staleness;
- repair choices and the user's selection;
- staged actions, confirmation decisions, execution outcome, and failures;
- Stop, interruption, line drop, dormancy, and power-off events;
- client-measured latency marks;
- interaction outcome and later reuse.

Original records are append-only. A correction does not silently rewrite what happened.

### Correction model

A reviewer can add bounded, provenance-tagged corrections such as:

- corrected transcript;
- intended meaning or task;
- wrong repair choice / preferred repair;
- wrong answer or missing evidence;
- preferred spoken response;
- incorrect visual/semantic state;
- incorrect action status;
- session-end or wake-word mistake;
- "this was fine" / useful outcome;
- free-text note linked to the exact event.

Every correction records:

- reviewer role;
- timestamp and protocol version;
- target event/turn/session;
- original value;
- corrected/preferred value;
- confidence or uncertainty;
- permission/use lane;
- whether Parker later reused it.

The near-term learning path is:

```text
human correction
  -> local suggestion / evaluator case
  -> family approval where needed
  -> deterministic regression
  -> measured reuse in a later session
```

Do not call this RLHF merely because a human corrected something. Initially it is product feedback, local personalization, and evaluation data.

### Future expert review and model learning

Expert review is a plausible future learning engine, but it is not a current product feature or open program.

Before any outside expert can hear or inspect identifiable session material, require:

- voluntary, separate participant consent;
- exact outgoing-payload preview;
- selected-session/selected-clip contribution rather than blanket collection;
- third-party voice review and disposition;
- named reviewer role, training, protocol, and accountability;
- controlled access and audit;
- export, correction, deletion, and withdrawal behavior;
- separation of service use, diagnostics, research contribution, and model/publication use;
- a written ethics/HRPP/IRB determination when applicable;
- explicit policy for whether raw audio, transcript, derived labels, evaluator cases, or model gradients/weights may be used.

Potential outputs, in increasing order of consequence:

1. local corrections and personalization;
2. synthetic/public regression cases inspired by failure modes;
3. controlled hidden evaluator cases;
4. preference/rubric labels for model comparison;
5. supervised fine-tuning or preference optimization on explicitly permissioned data;
6. public models/weights only under separate governance.

The product must earn voluntary use before Parker builds an expert-labeling operation.

## Product separation and navigation

Recommended routes/surfaces:

- `/parker/converse` — minimal companion experience;
- `/parker/sessions/ui` — authenticated session lab;
- a separate developer/test harness for Start/Done, typing, fixture-driving, and timing diagnostics.

Do not overload the companion with a hidden accordion containing the entire admin system. Link/navigation between surfaces belongs to family/admin setup, not the primary user scene.

## Critical decisions and gaps

### Decisions captured

- One primary virtual Reachy interface plus one session lab.
- One power control is the only visible primary control.
- Power off means no listening or response.
- Power on means local wake-word dormancy, not continuous cloud streaming.
- "Hey Parker" starts a continuous session; no per-turn buttons.
- Session end returns to dormant, not fully off.
- Text/transcripts are absent visually by default; CC/subtitles is a future optional mode.
- Current Start/Done/type controls become a separate harness/fallback, not the flagship.
- Corrections feed local reuse and evaluators first; outside-expert/model learning is future and separately governed.

### Open implementation decisions for evidence

- wake-word engine/model and whether personalization is needed for the first user;
- family-configured alternative wake phrases;
- dormancy/wrap-up timing and resume window;
- prewarming strategy that reduces wake latency without streaming ambient audio;
- whether read-only worker jobs finish silently or cancel at dormancy;
- how power state persists and is restored after app restart;
- the minimum accessible escape/wake fallback without visually reintroducing a control dashboard;
- whether subtitles show only played speech or both generated and played text, with played speech visually authoritative;
- physical Reachy connection protocol and movement limits—later, not in the current UI PR.

## Success metrics

Companion:

- wake recall on real first-user speech and room conditions;
- false wakes per hour/day;
- wake-to-first-acknowledgement P50/P95;
- continuous-session completion without touch assistance;
- first-try / one-repair success;
- interruption-to-silence;
- false/early session endings and failed dormancy;
- re-wake success;
- zero response while powered off;
- zero cloud audio while dormant;
- voluntary starts and preference over the stock assistant.

Session lab:

- time to understand what happened;
- corrections linked to the correct event;
- correction-to-regression conversion;
- correction reuse in a later session;
- family maintenance minutes;
- no disagreement between played speech, action truth, and displayed state.

## Recommended delivery sequence

### R0 — close current review

- hotfix PR #36 spoken-selection grammar;
- fix PR #37 semantic/lifecycle blockers;
- record expression transitions;
- complete real-mic and packaged WKWebView acceptance;
- merge the honest Reachy/state foundation.

### R1 — companion shell and true power

- split the companion from developer/session surfaces;
- reduce the primary scene to Reachy + power;
- implement persisted off/on-dormant/active state;
- verify power-off stops every sensing/speech resource;
- preserve accessible nonvisual state.

### R2 — local wake and continuous session

- spike/evaluate local "Hey Parker" detection on real room/audio;
- transfer audio ownership from wake detector to active realtime session;
- remove per-turn Start/Done from the primary path;
- implement conservative wrap-up/dormancy/resume;
- instrument wake/session-end metrics.

### R3 — session lab correction loop

- make complete session truth reviewable;
- add append-only turn/event corrections and reviewer provenance;
- convert accepted corrections into local reuse suggestions and evaluator cases;
- measure correction reuse.

### R4 — physical Reachy parity

- drive a real Reachy Mini from the same semantic state contract;
- define motor limits, disconnect behavior, and physical power semantics;
- compare virtual and physical sessions.

### Far future — expert program

- only after voluntary repeat use, protocol quality, governance, security, and ethics gates;
- begin with a very small controlled cohort and hidden evaluator labels;
- do not launch a broad RLHF/data operation ahead of product value.

## Scope discipline

Do not put all implementation into PR #37. PR #37 should close its current correctness/acceptance contract and establish the semantic/renderer foundation. The companion/power/wake architecture is a follow-up product slice, even though this brief lives on the same branch for the next Fable planning session.

The product north star is not a beautiful avatar. It is a person who can say "Hey Parker," converse naturally without touching a screen, end the conversation gracefully, return later, and find that Parker understood and learned.
