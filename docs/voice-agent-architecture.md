# Parker voice-agent architecture: one voice, many hands

Status: accepted architectural direction; implementation remains evidence-gated

Date: 2026-08-31

## Decision

Parker should feel like **one continuous, consistent agent** even when many models, tools, or remote processes help it.

```text
one person-facing Parker
  owns voice, presence, turn-taking, repair, pacing, and spoken truth
        |
        +-- delegates bounded jobs to invisible workers
        |
        +-- receives typed, policy-screened results
        |
        `-- decides whether, when, and how to bring a result back into the conversation
```

The short version is **one voice, many hands**. The fast front agent must never wait silently for slow work. It acknowledges the request, keeps the conversation available, and incorporates worker results when they arrive. Workers do not become new audible personalities, speak directly, or execute side effects.

This is the architecture Pras described before the external references arrived: "the realtime or super-fast model holds the conversation" while background agents get context, call tools, or do deeper work, then inject their results so the front agent can steer. The shipped origin record is [`docs/plans/2026-08-30-voice-orchestrator-handoff.md`](plans/2026-08-30-voice-orchestrator-handoff.md).

This document is the source of truth for the broader voice-agent shape. [`docs/brain-adapters.md`](brain-adapters.md) remains the source of truth for today's adapter and guard contracts.

## Why this direction is now load-bearing

The first human-testing lap established that the fallback Start/Done lane's robotic browser TTS and numbered-choice behavior did not feel alive enough to evaluate the product. The live realtime lane already has the better interaction primitives—natural voice, semantic end-pointing, barge-in, presence, and background search/context—but it was buried.

The next voice architecture must therefore optimize for:

1. a natural, consistent Parker voice;
2. patient turn-taking that tolerates pauses and variable speech;
3. immediate presence rather than silence while work runs;
4. reliable tool use and say/do consistency;
5. one action/confirmation policy regardless of which model does the thinking;
6. client-measured voice-to-voice latency and interruption behavior;
7. swappable front and worker models without a broad application rewrite;
8. a reviewable trail of what was heard, spoken, delegated, injected, staged, interrupted, and remembered.

## External reference architecture 1: the single-agent stack

[Bootoshi's 2026-08-31 post](https://x.com/KingBootoshi/status/2094498865617453232) states the same product desire: speak to one consistent agent that handles the present, tracks projects, and delegates work. Its attached diagram shows this concrete stack:

```text
1. Audio capture
   FaceTime on iPhone -> Mac mini, restricted to an authorized E.164 identity

2. Ingress and validation
   Silero VAD (diagram settings: min_volume 0.40, confidence 0.8)
   Voice-recognition gate removed for the current experiment

3. Speech recognition
   Streaming Nemotron 3.5 ASR / FastConformer

4. Intelligence
   PhoneLLM Alpha 1 NVFP4 via vLLM on a DGX Spark
   Complex background tasks delegated to a GH200 running 16 Hermes agents

5. Speech synthesis
   MagpieTTS with streaming sentence/phrase aggregation

6. Audio output
   FaceTime audio back to the user's ear
```

The reusable idea is not FaceTime, a DGX Spark, or 16 workers. It is the split between a fast, always-present conversational path and a slower delegated-work plane behind one identity.

Parker should not copy the diagram's VAD thresholds, hardware, worker count, or authentication shortcut without its own evidence. A fixed `min_volume` that works for one speaker can reject quiet or effortful speech. One family v0 does not need 16 workers or a distributed GPU cluster.

## External reference architecture 2: Pipecat agents

Pipecat's [distributed-agents architecture](https://docs.pipecat.ai/pipecat/learn/distributed-agents) supplies useful implementation primitives for this pattern:

- `PipelineWorker` owns the realtime media pipeline.
- `BusBridgeProcessor` replaces an in-pipeline LLM with a bridge to agent workers.
- `LLMWorker` hosts a model and tools.
- `WorkerRunner` owns workers and their bus.
- A worker registry supports readiness/discovery through `registry.watch(...)`, `watch_workers()`, or `@worker_ready`.
- The same worker code can run in one process on `AsyncQueueBus`, or across processes/machines by swapping in `RedisBus` or `PgmqBus`.
- Distributed workers on the same channel discover one another automatically. Every agent on that channel sees the channel's messages, so sessions or applications require separate channels.
- `job()` dispatches one bounded request; `job_group()` fans out to several workers and collects responses.
- Jobs carry IDs, payloads, timeouts, responses, progress updates, completion, errors, and cancellation.
- Context-manager cancellation propagates to outstanding workers. Worker-side `on_job_cancelled` handles cleanup.
- Agent handoff is a different primitive: `activate_worker(target, deactivate_self=True)` transfers the active conversational role.
- [Proxy agents](https://docs.pipecat.ai/pipecat/learn/proxy-agents) connect agents on different buses or networks over a point-to-point WebSocket, with an explicit allowlist of forwarded message types.

Pipecat's topology distinction matters:

| Pattern | Topology | Good for | Parker default |
| --- | --- | --- | --- |
| Local worker bus | One process, one bus | current one-family orchestration | yes, conceptually; Parker's current `asyncio`/threadpool design is sufficient |
| Distributed agents | Many processes/machines on one shared bus/channel | scaling or hardware isolation | later, only after a measured need |
| Proxy agents | Point-to-point connection between different buses/networks | family OpenClaw/Hermes or third-party agent boundary | likely future shape |
| Audible agent handoff | one LLM deactivates and another becomes active | explicitly different specialists/personas | not the normal Parker experience |
| Job delegation | front agent remains active while workers return results | search, context, projects, deeper reasoning | Parker's primary pattern |

Parker is a **job-delegation system**, not a collection of audible agent handoffs. The person should not have to know which worker answered.

## External reference architecture 3: PhoneLLM

Kwindla Hultman Kramer's [PhoneLLM announcement](https://x.com/kwindla/status/2093014818647339026), [Daily's release article](https://www.daily.co/blog/announcing-pipecat-phonellm-alpha-1/), and the [Hugging Face model card](https://huggingface.co/pipecat-ai/phonellm-alpha-1) describe a candidate **text-mode front LLM**, not a speech-to-speech model:

- full-parameter fine-tune of NVIDIA Nemotron 3 Nano 30B-A3B;
- hybrid Mamba-Transformer mixture-of-experts;
- 30B total parameters, 3.5B active;
- 262,144-token context;
- trained with NVIDIA NeMo for low-latency, multi-turn voice-agent workloads;
- optimized for instruction following, tool calls, and say/do consistency with thinking disabled;
- recommended settings: temperature `0`, thinking disabled;
- served with vLLM or SGLang; one-click Modal endpoint is available;
- English is the model card's declared language, despite informal multilingual expectations in the X discussion;
- BSD 2-Clause terms apply to Pipecat's work, with the underlying NVIDIA Nemotron license and attribution obligations still applying.

The Pipecat team reports PhoneBench performance on par with GPT 5.6 Terra, 94% lower modeled cost, and 1,300 ms faster P95 time-to-first-token. It also reports a sub-600 ms P95 time-to-first-answer-token serving target and high B200 concurrency after Modal optimization. These are **external, workload-specific claims**, not Parker evidence.

PhoneBench evaluates more relevant constructs than generic LLM benchmarks: telephone speaking style, tool-call accuracy, say/do consistency, factual grounding, conversation coherence, authentication/escalation discipline, and caller outcome. It uses LLM judges calibrated against human labels and keeps evaluation scenarios, prompts, and tools separate from training data.

PhoneLLM is interesting for Parker because a purpose-built fast model may be a better conversational coordinator than a large reasoning model. It is not an automatic choice because:

- Parker's questions are broader than telephone customer support;
- effortful-speech ASR and repair remain separate upstream problems;
- a cascaded stack must supply and tune STT, turn detection, TTS, and interruption handling;
- current hardware cannot be assumed to run a 30B model at the published latency;
- a hosted Modal endpoint changes cost, data path, cold-start, and regional-latency behavior;
- the published benchmark is not Parker's user, tool set, conversation style, or safety boundary.

PhoneLLM belongs in a measured model/runtime bake-off, not in Parker's dependency graph today.

## Parker target architecture

```text
┌──────────────────────────── person-facing realtime plane ────────────────────────────┐
│                                                                                       │
│  microphone / transport                                                               │
│        -> echo cancellation / noise handling                                           │
│        -> VAD + turn detection + interruption                                          │
│        -> FRONT VOICE AGENT (one Parker identity)                                      │
│             - hears or receives the final transcript                                   │
│             - owns presence, pacing, repair, and short responses                       │
│             - owns the live conversation context                                       │
│             - acknowledges slow work immediately                                       │
│             - may propose, but never directly execute, an action                        │
│        -> speech output / configured Parker voice                                      │
│                                                                                       │
└───────────────────────────────────┬───────────────────────────────────────────────────┘
                                    │ typed job request
                                    v
┌──────────────────────────── delegated work plane ─────────────────────────────────────┐
│  context worker     search worker     deeper-reasoning/project worker     action broker│
│  local + ambient    current facts     planning/synthesis/delegation       stage only    │
│                                                                                       │
│  workers return typed results; they do not speak, mutate live history, or execute      │
└───────────────────────────────────┬───────────────────────────────────────────────────┘
                                    │ screened result envelope
                                    v
                         relevance / policy / stale-result gate
                                    │
                                    v
                      front agent chooses whether/how to speak it
                                    │
                                    v
┌──────────────────────────── evidence and continuity plane ────────────────────────────┐
│ session journal -> feedback -> outcomes -> selected memory/corrections -> next context │
└───────────────────────────────────────────────────────────────────────────────────────┘
```

### Plane 1: one front voice agent

The front agent owns everything the person experiences as Parker:

- voice and persona;
- turn starts, turn ends, pauses, backchannels, and barge-in;
- short acknowledgements and progress language;
- repair under uncertain transcription or intent;
- deciding whether a worker result is still relevant;
- rendering a result in the current conversational context;
- truth about whether an action is proposed, staged, confirmed, complete, failed, or still running;
- goodbye and session close.

The front agent must stay available while background work runs. "Never blocks" means the conversation loop remains responsive; it does not mean Parker should fill silence compulsively or narrate internal machinery.

### Visual embodiment contract

Parker's visual presence follows the same single-front-agent rule. A renderer-independent semantic expression state translates real voice/runtime signals—listening, user speech energy, front-response work, background-job work, played output audio, interruption, staged confirmation, guard redirect, close/error—into one consistent Parker embodiment. The approved primary renderer direction is a stylized 3D Reachy Mini; reduced-motion, no-WebGL, and static/2D fallbacks must preserve the complete textual and control experience. The detailed build brief is [`docs/plans/2026-08-31-reachy-mini-converse-ui.md`](plans/2026-08-31-reachy-mini-converse-ui.md).

The renderer is downstream of the voice architecture: it may visualize only state that Parker can truthfully derive or explicitly emit, and it may not invent work, listening, speech, or action completion. The semantic state should later be able to drive a physical Reachy Mini, but physical control is not part of the current UI slice.

### Plane 2: invisible workers

Initial worker classes should stay small:

1. **Context worker** — recent interactions, approved memory, current household context, due local state. Starts at session open and injects silently.
2. **Search worker** — current information and source metadata. Model-invoked; Parker acknowledges and keeps talking.
3. **Reasoning/project worker** — a later bounded worker for deeper synthesis, planning, or delegating project work. It returns a result or artifact reference; it does not become the live persona.
4. **Action broker** — validates and stages a proposal through Parker's existing capture → resolve → stage → confirm → execute pipeline. It is not a generic worker with side-effect authority.

Do not create one worker per subject. Weather, sports, news, and general curiosity stay one search/research capability unless evidence shows a real contract difference.

### Plane 3: evidence and continuity

The current session-review journal is part of the architecture, not optional debugging. Every meaningful event needs one correlated trail:

- user speech start/end and final transcript;
- assistant speech actually played, including interrupted partial speech;
- front-agent acknowledgement;
- job dispatch, progress, result, error, timeout, cancellation, and injection;
- source metadata shown on screen;
- action proposal/stage/confirm/execute outcome;
- guard trip or refusal;
- client latency marks;
- user feedback and final interaction outcome;
- memory/correction selected for the next session.

Generated-but-never-played speech must not be recorded as if the person heard it. Pipecat's interruption behavior—commit only words synchronized with played audio—is the standard Parker should preserve across runtimes.

## The job contract

Parker's current `WorkerResult` is the v1 seed. A runtime-neutral job should eventually carry:

```json
{
  "job_id": "stable unique id",
  "kind": "context | search | reasoning | project | action_proposal",
  "session_id": "owning voice session",
  "turn_id": "originating turn when applicable",
  "generation": 7,
  "request": "bounded user-visible intent or question",
  "created_at": "server time",
  "deadline_at": "optional deadline",
  "cancel_on_interruption": false,
  "payload": {},
  "policy_context": {}
}
```

A result should carry:

```json
{
  "job_id": "same id",
  "status": "completed | failed | timed_out | cancelled | stale",
  "result": {},
  "sources": [],
  "created_at": "server time",
  "completed_at": "server time",
  "guard": {"screened": true, "tripped": false},
  "action_proposals": [],
  "error": null
}
```

These are protocol requirements, not a command to add new tables or a message broker now.

### Lifecycle invariants

- A job ID is idempotent: identical retry returns the same logical job; conflicting reuse fails.
- A worker result is data, never an instruction to the front model.
- Worker content is untrusted and fenced before injection.
- Source labels/URLs stay display metadata, not spoken prompt content.
- Every result carries the originating request and age so the front agent can judge relevance.
- Server logic drops results after session close, terminal Stop, generation mismatch, or expired deadline.
- User interruption cancels speech immediately. Whether it cancels a background job is explicit per job.
- Search may continue through a conversational interruption; an action confirmation tied to an abandoned turn may not.
- Timeouts and worker failures produce an honest short state; they do not silently retry external side effects.
- Only the action broker may create a staged action, and execution still requires Parker's ordinary confirmation path.
- The session journal links dispatch, acknowledgement, result, injection, and user-visible outcome by job ID.

## Interruption and turn-taking contract

Pipecat's interruption model is a useful reference:

1. interruption propagates urgently through the pipeline;
2. LLM generation and interruptible function calls cancel;
3. TTS buffers clear;
4. unplayed transport audio flushes;
5. only words actually played enter assistant history;
6. the pipeline becomes ready for the new turn.

Parker needs the equivalent behavior whether the front runtime is OpenAI Realtime or a cascaded Pipecat pipeline.

For effortful speech, generic fast end-pointing is not automatically correct. Turn detection must be evaluated on:

- long pauses inside one thought;
- trailing/restarted phrases;
- quiet voice and changing volume;
- backchannels such as "yes" or "uh-huh" that may or may not be an interruption;
- explicit Stop;
- the user's ability to continue after Parker acknowledges a worker.

Candidate turn strategies include provider semantic VAD, Silero plus a learned turn model such as Pipecat Smart Turn, and manual Start/Done as the accessible fallback. Thresholds remain family/user-configurable only after measurement; do not copy the external diagram's values.

## Front-runtime portfolio

Parker should keep one experience contract while testing more than one implementation:

| Runtime | Strengths | Trade-offs | Current decision |
| --- | --- | --- | --- |
| OpenAI Realtime speech-to-speech | most natural current voice; semantic turn-taking; native barge-in; already shipped | provider controls more of the audio loop; post-hoc transcript screening; cost/vendor dependency | flagship runtime now; make it the default when configured and finish human testing |
| Cascaded STT → fast text LLM → streaming TTS, potentially Pipecat + PhoneLLM | component control; pre-TTS text screening; swappable/open models; reliable typed tools; clearer played-text accounting | more models and network hops; voice/turn quality must be assembled; operational complexity | bounded spike after current live-default and human-feedback slice |
| Manual Start/Done + local ASR + browser/macOS TTS | explicit pacing; useful fallback and diagnostic control | robotic voice; tap burden; not the flagship experience | keep as labeled fallback, not the first thing the user meets |

Parker's voice, state machine, result envelopes, action broker, review trail, and outcome semantics must not depend on which front runtime wins.

## Latency and quality budgets

The external Pipecat references argue for client-measured P95 voice-to-voice latency at or below roughly 1,500 ms, with an LLM first-answer-token budget around 600–650 ms in a cascaded system. Those are useful starting targets, not Parker results.

Parker should measure:

- user speech end → final transcript;
- user speech end → first acknowledgement audio;
- user speech end → first substantive audio;
- TTS request → first played audio;
- tap/spoken Stop → silence;
- worker dispatch → acknowledgement;
- worker dispatch → result;
- result → injection;
- injection → first relevant spoken mention;
- P50, P95, and worst observed values;
- false turn ends, missed barge-ins, and unnecessary interruptions;
- useful outcome and user desire to continue, not latency alone.

The measurement point should be the person's microphone/speaker path when possible. Server TTFT alone excludes endpointing, network, encoding, buffering, TTS, playback, and device behavior.

## Evaluation plan

A front-runtime or model candidate graduates only through the same Parker scenario set and human protocol.

### Deterministic/runtime tests

- one front persona despite multiple workers;
- context worker arrives before/after greeting;
- search result arrives during speech, during user speech, after topic change, after Stop, and after close;
- duplicate/rephrased requests, idempotent retry, timeout, cancellation, worker crash, and reconnect;
- guard screens the worker output before injection;
- worker proposals cannot bypass staging/confirmation;
- spoken history contains only played text after interruption;
- action status language is exactly true;
- job/source/event correlation survives restart where the runtime claims durability.

### ParkerBench-style model bake-off

Use held-out, Dad-shaped, multi-turn scenarios rather than generic chat benchmarks:

- variable/incorrect ASR transcripts and bounded repair;
- "yes one", ordinals, trailing words, changed mind, and partial confirmation;
- current questions plus follow-ups;
- tool choice, arguments, and say/do consistency;
- broad curiosity outside customer-support scripts;
- action proposal versus read-only answer;
- prohibited or human-gated edges;
- long context and memory selection;
- concise, natural spoken style;
- no false claim that delegated work or an action is complete.

Track accuracy together with end-to-end latency and cost. Keep the model from seeing evaluation scenarios during any future tuning.

### Human comparison

Run the same short tasks through:

1. current OpenAI Realtime front;
2. a Pipecat/PhoneLLM cascaded spike if it reaches the gate;
3. stock assistant when useful as a preference baseline.

The winner is the experience the user wants to keep using, subject to zero false-action claims and the action boundary—not the architecture with the most open components.

## Adoption sequence

### Now: finish the current fast-voice experience

- make Live the primary entry when configured;
- set and evaluate the Parker voice explicitly;
- fix natural spoken selection such as "yes one";
- feed browser/client latency into the session review;
- define realtime interaction outcomes;
- run another human session and review the trail.

### Next: harden the runtime-neutral seams

- keep the front/worker/result/action boundaries explicit;
- make job/event correlation visible in the review surface;
- pin stale/cancel/interruption behavior with the scenario gauntlet;
- add a reasoning/project worker only when one real task needs it.

Do this with the current in-process implementation. Do not add Redis, PGMQ, or Pipecat merely to restate contracts Parker already has.

### Then: run a contained Pipecat + PhoneLLM spike

A spike is justified after the current flagship has a measured baseline. It should:

- run on an isolated branch or throwaway harness;
- reuse Parker's existing scenario inputs, policy broker, and review metrics;
- use a hosted, geographically close endpoint before any hardware purchase;
- compare OpenAI Realtime with a cascaded Pipecat runtime using an appropriate STT, PhoneLLM at temperature 0/no-thinking, and a natural streaming TTS;
- verify interruption, played-text history, sources, tool calls, broad curiosity, and variable-speech behavior;
- end in adopt, keep as optional runtime, or discard.

### Later: distribute only for a measured reason

Move workers to a shared Redis/PGMQ bus only if process isolation, remote accelerators, independent scaling, or failure containment creates observed value. Use one session-scoped channel. Place latency-sensitive front components and their bus geographically close.

Treat the family OpenClaw/Hermes boundary as a proxy/typed-gateway boundary rather than joining an external general agent directly to the live voice bus. Forward only explicit message/result types.

Fine-tuning or a Parker-specific model comes after voluntary use and a governed, sufficiently large correction/eval corpus. Prompt/context changes and inference/runtime optimization come first.

## Explicit non-decisions

This architecture does **not** currently approve:

- replacing Parker's realtime implementation with Pipecat;
- adding Pipecat, Redis, PGMQ, Modal, vLLM, SGLang, Nemotron ASR, MagpieTTS, or PhoneLLM as production dependencies;
- buying a DGX Spark or provisioning a persistent GPU;
- routing Parker through FaceTime;
- creating 16 Hermes workers or a generic worker marketplace;
- making background agents audible;
- giving a worker or model direct execution authority;
- training or fine-tuning on household voice/history;
- claiming PhoneBench results as Parker performance.

## Sources

Primary external sources, retrieved 2026-08-31:

- [Bootoshi: one consistent voice agent and architecture diagram](https://x.com/KingBootoshi/status/2094498865617453232)
- [Pipecat: Distributed Agents](https://docs.pipecat.ai/pipecat/learn/distributed-agents)
- [Pipecat: Proxy Agents](https://docs.pipecat.ai/pipecat/learn/proxy-agents)
- [Pipecat: Agent Handoff](https://docs.pipecat.ai/pipecat/learn/agent-handoff)
- [Pipecat: Job Coordination](https://docs.pipecat.ai/pipecat/learn/job-coordination)
- [Pipecat: Interruptions](https://docs.pipecat.ai/pipecat/fundamentals/interruptions)
- [Kwindla: PhoneLLM announcement](https://x.com/kwindla/status/2093014818647339026)
- [Kwindla: weights and Modal deployment follow-up](https://x.com/kwindla/status/2094491302188741045)
- [Daily: Announcing Pipecat PhoneLLM Alpha 1](https://www.daily.co/blog/announcing-pipecat-phonellm-alpha-1/)
- [Hugging Face: `pipecat-ai/phonellm-alpha-1` model card](https://huggingface.co/pipecat-ai/phonellm-alpha-1)
- [Voice AI & Voice Agents: An Illustrated Primer](https://voiceaiandvoiceagents.com/)
- [Modal: One-second voice-to-voice latency with Modal, Pipecat, and open models](https://modal.com/blog/low-latency-voice-bot)

Parker-local sources:

- [`docs/plans/2026-08-30-voice-orchestrator-handoff.md`](plans/2026-08-30-voice-orchestrator-handoff.md)
- [`docs/brain-adapters.md`](brain-adapters.md)
- `backend/app/parker/realtime.py`
- `backend/app/parker/realtime_workers.py`
- `backend/app/parker/session_review.py`
- `backend/tests/test_realtime.py`
- `backend/tests/test_realtime_workers.py`
- `backend/tests/test_scenarios_review.py`
