# Parker Strategy — the capability model

> **Provenance:** Pras, voice ideation session while driving, 2026-08-17. Stored verbatim (formatting only — no wording changes). This is the strategy baseline; capability briefs, experiment specs, and the roadmap in this directory derive from it. Where this reframes README/CLAUDE.md (e.g. the North Star hierarchy), reconciliation happens in those derived docs — this file stays the source ideation.

## 1. North Star

Build a daily companion that helps a person age well by becoming more useful to them every day.

Parker is not primarily a voice assistant, healthcare application, or collection of features.

The core idea is a long-lived assistant that develops an increasingly useful model of one person and improves through continued interaction with them.

The initial proving ground is helping my dad.

The immediate test is simple:

> Does my dad prefer using Parker to Google Home, and does Parker become measurably more useful to him over time?

Longer term, Parker should move from:

responding → understanding → remembering → anticipating → helping → learning

The important property is not how capable Parker is on day one. It is the slope of improvement.

## 2. Product Thesis

Most assistants are effectively stateless tools. They may have access to powerful models, but they don't meaningfully develop alongside a person.

Parker should behave more like a great human assistant, caregiver, coach, therapist, or nurse:

- it gets to know you;
- it understands how you communicate;
- it remembers what matters;
- it notices patterns;
- it understands your routines;
- it learns from mistakes;
- it adjusts how it interacts with you;
- it becomes more useful because of accumulated experience.

This creates the fundamental Parker loop:

**Observe → Interpret → Act → Feedback → Update**

Every important Parker capability should eventually participate in this loop.

## 3. Strategy Framework

Parker development should operate through five layers:

**Vision** — What are we ultimately trying to create? This changes rarely.

↓

**Capabilities** — What durable abilities must Parker develop? Capabilities are not features or projects. They are things Parker can become progressively better at.

↓

**Experiments** — What hypothesis are we currently testing about improving a capability? Experiments should generally run for days or weeks rather than months.

↓

**Work** — What needs to actually get built? Issues, code, prompts, evaluations, datasets, integrations, infrastructure, etc. Linear/Kanban/GitHub can manage this layer.

↓

**Evidence** — What did we learn? Every experiment should produce evidence that changes or strengthens our understanding of Parker.

Then the cycle repeats:

Vision → Capabilities → Experiments → Work → Evidence → Better Experiments

## 4. Capabilities, Not Features

Parker should be modeled as an entity developing capabilities rather than software accumulating features.

A useful mental model is an RPG character sheet. Parker has core attributes that can continuously level up. Features and skills are things Parker can do. Capabilities describe how good Parker is at being Parker.

Initial capability model:

### 1. Perception — *What is happening?*

Ability to perceive and understand signals from the person and environment.

Examples:

- speech recognition;
- abnormal or impaired speech recognition;
- speaker identification;
- visual perception;
- activity recognition;
- environmental context;
- device context;
- temporal context;
- confidence estimation.

### 2. Communication — *Can we understand each other?*

Ability to communicate naturally and successfully with the person.

Examples:

- understanding intent;
- conversational clarification;
- speech correction;
- adapting to individual speech patterns;
- appropriate response length;
- vocabulary adaptation;
- tone;
- repetition when necessary;
- multimodal communication;
- detecting misunderstanding.

Communication success matters more than transcription accuracy.

### 3. Memory — *What do I know about this person?*

Ability to maintain an accurate, evolving model of the person and their life.

Examples:

- identity;
- preferences;
- relationships;
- routines;
- important events;
- goals;
- habits;
- episodic memories;
- conversation history;
- corrections;
- learned communication patterns;
- relevant health context;
- behavioral history.

Memory should not merely mean storing information. The goal is useful recall at the right moment.

### 4. Reasoning — *Given what I know, what does this mean and what should I do?*

Ability to combine current observations, historical context, goals, and constraints.

Examples:

- contextual reasoning;
- prioritization;
- planning;
- detecting inconsistencies;
- interpreting behavior over time;
- deciding whether to interrupt;
- determining when clarification is needed;
- deciding which capability or skill should handle something.

### 5. Coaching — *How can I help this person do better?*

Ability to positively influence behavior without becoming annoying or controlling.

Examples:

- reminders;
- encouragement;
- recall exercises;
- speech exercises;
- routines;
- activity prompts;
- behavioral reinforcement;
- personalized motivation;
- adjusting interventions based on response;
- knowing when not to intervene.

The goal is not maximum intervention. It is useful intervention at the right time.

### 6. Learning — *How do I become better for this specific person?*

Ability to turn interactions and outcomes into persistent improvement.

Examples:

- learning from corrections;
- learning speech patterns;
- learning preferences;
- updating confidence;
- detecting changing routines;
- evaluating past interventions;
- reinforcement from explicit feedback;
- reinforcement from implicit behavior;
- self-evaluation;
- proposing improvements to itself.

This is potentially Parker's most important long-term capability.

### 7. Agency — *What can I actually get done?*

Ability to take useful actions in the world.

Examples:

- calendar;
- reminders;
- messaging;
- email;
- media playback;
- YouTube;
- browser use;
- smart-home control;
- scheduling;
- calling other agents;
- triggering workflows;
- interacting with external systems.

Individual integrations are skills. Agency is the underlying capability.

### 8. Trust — *Should Parker do this, and should the person trust Parker doing it?*

Ability to operate safely, predictably, transparently, and with appropriate autonomy.

Examples:

- confidence thresholds;
- confirmation;
- permissions;
- privacy;
- explaining actions;
- distinguishing observation from inference;
- escalating uncertainty;
- caregiver boundaries;
- avoiding unnecessary intervention;
- preserving user control.

Trust is not merely a safety layer. It determines how much autonomy Parker can earn.

## 5. Capability Structure

Every capability should eventually have its own living specification. Use the same structure for each one:

- **Mission** — Why does this capability exist?
- **Sub-capabilities** — What component abilities make it possible?
- **Current State** — What can Parker reliably do today?
- **Target State** — What should Parker be able to do next?
- **Metrics** — How do we know the capability is improving?
- **Experiments** — What hypotheses are currently being tested?
- **Evidence** — What have previous experiments taught us?
- **Open Questions** — What don't we understand yet?

## 6. Capability Maturity

Capabilities should level up rather than simply become "done." A generic maturity model:

- **Level 0 — Absent.** Parker cannot reliably perform the capability.
- **Level 1 — Functional.** Parker can perform it in simple situations.
- **Level 2 — Reliable.** Parker performs it consistently under expected conditions.
- **Level 3 — Personalized.** Performance improves specifically for this person.
- **Level 4 — Adaptive.** Parker detects changes and modifies its behavior.
- **Level 5 — Self-improving.** Parker can identify weaknesses, learn from outcomes, and improve the capability with decreasing human intervention.

Not every capability needs to reach Level 5.

## 7. Experiments Are the Unit of Progress

Projects tend to measure output. Parker should measure learning and capability improvement.

Each active experiment should contain:

- **Hypothesis** — What do we believe?
- **Capability** — Which Parker capability are we improving?
- **Intervention** — What are we changing or building?
- **Evaluation** — How will we measure whether it worked?
- **Evidence** — What happened?
- **Decision** — Based on the evidence: keep; modify; abandon; investigate further.
- **Learning** — What should Parker development do differently because of this result?

## 8. Example Experiment

**Capability:** Communication / Perception

**Hypothesis:** Parker can understand Dad's Parkinson's-affected speech more reliably than Google Home by learning from clarification and correction.

**Intervention:** Capture misunderstood utterances, ask a clarification question, store the corrected interpretation, and use previous corrections during future interpretation.

**Evaluation:** Track:

- first-attempt success rate;
- clarification rate;
- successful clarification rate;
- repeated-error rate;
- number of learned corrections reused successfully;
- comparison against Google Home where practical.

**Evidence:** Collect real interactions rather than relying entirely on synthetic evaluation.

**Success:** The important result is not merely higher transcription accuracy. Success means: Dad gets what he wanted with less frustration.

## 9. Relationship Formation

"Onboarding" is useful engineering terminology, but it describes the wrong conceptual model.

A human assistant, nurse, therapist, or caregiver develops a relationship model. Parker should do the same. Call this:

**Relationship Formation**

The goal is to initialize and continuously improve Parker's model of the person. It should not necessarily be a one-time questionnaire. Instead:

Initial interview + observation + progressive micro-interviews + implicit learning

For example, Parker might initially learn:

- who you are;
- important people;
- normal routines;
- interests;
- communication preferences;
- goals;
- medications/reminders where appropriate;
- things you frequently need help with.

Later Parker can naturally fill gaps:

> "You mention Ravi pretty often. Who is Ravi?"

or:

> "You usually watch YouTube around this time. Is that something you normally do after lunch?"

Relationship formation therefore cuts across:

- memory;
- communication;
- perception;
- learning;
- trust.

It is a relationship phase and ongoing process, not another core capability.

## 10. Skills Sit Above Capabilities

Skills are concrete things Parker can do. Examples:

- play YouTube;
- medication reminder;
- speech exercise;
- recall game;
- calendar assistant;
- exercise coach;
- morning briefing;
- call a family member;
- answer a question;
- control a device.

A skill should reuse Parker's capabilities. For example:

**Medication Reminder** uses:

- Perception
- Memory
- Reasoning
- Communication
- Coaching
- Learning
- Agency
- Trust

This distinction matters. We should avoid building eight separate versions of "memory" inside eight different skills. Improve the underlying capability and every skill should benefit.

## 11. Initial Prioritization

Do not attempt to build all eight capabilities simultaneously. For the first Parker phase, focus on three:

**Priority 1 — Communication / Perception.** Can Dad successfully communicate with Parker? This is the wedge. If Parker understands him significantly better than existing assistants, there is immediate value.

**Priority 2 — Memory.** Can Parker actually get to know Dad? This creates accumulated value and enables personalization.

**Priority 3 — Learning.** Does Parker measurably improve because Dad uses it? This tests the deepest Parker thesis.

For the MVP, reasoning, agency, coaching, and trust can initially rely heavily on existing models, OpenClaw, tools, and simple policies. They become dedicated capability programs later.

## 12. Initial Product Loop

The first Parker loop can therefore be extremely small:

```text
Dad speaks
  ↓
Parker interprets
  ↓
If uncertain, Parker clarifies
  ↓
Dad corrects Parker
  ↓
Parker stores the correction
  ↓
Similar situation occurs later
  ↓
Parker uses what it learned
  ↓
Measure whether it succeeded
```

That single loop exercises:

- perception;
- communication;
- memory;
- learning.

If this works reliably, Parker already demonstrates its core thesis.

## 13. Metrics

Avoid vanity metrics such as:

- number of integrations;
- number of prompts;
- number of agents;
- number of features;
- lines of code.

The primary metrics should describe the relationship.

Candidate North Star metric:

> **Successful interactions without human assistance**

Supporting metrics:

- task success rate;
- first-attempt understanding;
- clarification frequency;
- clarification success;
- repeated-error rate;
- useful memory retrieval;
- successful application of learned corrections;
- interaction abandonment;
- intervention acceptance;
- user-initiated interactions;
- repeat usage.

Eventually an especially important metric may be:

> **Learning Velocity** — How quickly does Parker improve from experience?

For example: How many repeated interactions are required before Parker reliably handles something it previously failed at?

A Parker system with slightly lower initial capability but substantially higher learning velocity may ultimately be more valuable than a more capable static assistant.

## 14. Weekly Operating System

Every week should answer five questions:

1. **Capability** — What Parker capability are we trying to improve?
2. **Hypothesis** — What do we believe will improve it?
3. **Experiment** — What are we doing to test that belief?
4. **Evidence** — What happened in reality?
5. **Learning** — What changed about our understanding of Parker?

Then choose the next experiment.

This should matter more than whether every planned ticket was completed.

## 15. Agent Organization

The capability model may eventually become the organizational model. Each major capability could have an AI agent responsible for continuously improving it. For example:

- Perception Agent
- Communication Agent
- Memory Agent
- Learning Agent
- Coaching Agent
- Agency Agent
- Trust Agent

Each agent could own:

- capability specification;
- evaluations;
- experiment backlog;
- failure analysis;
- research;
- proposed improvements;
- evidence history.

Initially these are conceptual roles. Later they could become actual persistent agents. Eventually, if Parker grows into a larger project or organization, human capability owners could replace or supervise those agents.

The architecture therefore scales naturally:

- Pras → Vision / Direction / Delivery
- Capability Owners → Continuous capability improvement
- Agents / Engineers → Experiments and implementation

This is intentionally closer to organizing an intelligence than organizing a SaaS product.

## 16. Relationship to the Broader Technical Work

Parker should be a proving ground for reusable work around:

- agent harnesses;
- persistent memory;
- reinforcement;
- self-learning systems;
- recursive language models;
- agent evaluation;
- agent self-improvement;
- context management;
- OpenClaw;
- personal AI infrastructure.

Where possible, Parker-specific product work should sit on top of reusable primitives.

A useful test when designing something:

> Is this fundamentally a Parker feature, or is it a general capability that any long-lived agent should possess?

If general, prefer building or extracting it as reusable infrastructure.

Parker then becomes both:

1. a useful product for a real person; and
2. a demanding real-world test environment for continuously learning agents.

The real person must come first. Architecture should serve the experience, rather than turning Parker into an excuse to build infrastructure.

## 17. Immediate Planning Goal

Do not create a six-month feature roadmap yet. The next planning exercise should instead produce:

**Three capability briefs**

1. Communication / Perception
2. Memory
3. Learning

For each:

- mission;
- baseline;
- maturity levels;
- metrics;
- current weaknesses;
- experiment backlog.

Then define:

**Experiment 001** — Can Parker understand Dad better than Google Home and learn from its mistakes?

Everything required to run that experiment becomes the first implementation plan.

## 18. Planning Principle

The fundamental question is not:

> What should we build next?

It is:

> What does Parker need to become better at next, and what is the smallest experiment that will tell us whether we're making it better?

That should drive the roadmap.
