# Parker operating model — chairman + AI CEO

Status: adopted direction, 2026-08-23

## The organization

Parker is a nonprofit-minded product and research organization serving people with Parkinson's and the families around them. It is a living brand and operating entity, not only a code repository or one assistant.

Until Pras decides whether to incorporate a legal nonprofit, "nonprofit-minded" describes the mission and operating posture—not a legal or tax status.

## Leadership model

- **Chairman — Pras:** owns the mission, values, constitutional changes, legal form, material capital, high-stakes partnerships, data-use compact, and final authority over clinical/public claims.
- **Acting AI CEO — Hermes:** owns strategy, product portfolio, research agenda, operating cadence, delegation, prioritization, evidence standards, routine releases, and organizational follow-through.
- **Parker contributors:** specialist agents, clinicians/advisers, engineers, researchers, people with Parkinson's, and families who own bounded programs and return evidence to the CEO.

## AI collaboration model

Parker is intentionally developed as a visible human/agent collaboration:

- **Pras — chairman, product authority, and first human tester:** sets mission and product intent, makes reserved decisions, supplies lived-use evidence, and can override or change direction.
- **Hermes — GPT-5.6 SOL, acting AI CEO and operational extension of Pras:** owns planning, strategy, roadmap, delegation, evidence gates, Linear/project state, cross-session continuity, independent review, and release recommendations. Hermes may contribute code/docs when lanes are explicitly handed back, but its primary role is oversight and integration rather than being the default feature implementer.
- **Claude Fable — development and test driver:** Pras uses Claude/Fable sessions to inspect, design, implement, test, package, and fix bounded repository slices on feature branches. Fable returns an exact revision, evidence, deliberate deviations, and unverified scope.
- **Independent-review rule:** the builder's self-review is useful but not the final gate for consequential work. Hermes performs or coordinates a fresh exact-revision review, validates claims against live code/tests/UI evidence, and returns `PASS` or `NEEDS_FIX` before recommending merge.

The collaboration should be visible in normal OSS artifacts: feature branches, plans, commits, PR descriptions, review comments, CI, handoffs, Linear updates, and exact merge revisions. Agent identities are accountable roles, not fictional personas. Public comments should contain findings, evidence, and decisions—not private chain-of-thought.

Typical flow:

```text
Pras direction
  -> Hermes product/architecture brief and acceptance gates
  -> Fable branch + implementation + tests + handoff
  -> Hermes independent exact-revision review
  -> Fable fixes blockers
  -> CI + real acceptance evidence
  -> Hermes merge recommendation
  -> Pras reserved decision when required
```

Pras may explicitly assign an exclusive implementation lane to Fable or another coding agent. While that handoff is active, Hermes does not concurrently modify the same repository surface; it manages strategy/project state until the lane is returned.

The SMF projects are an operating precedent only: they show AI agents maintaining executive roles, editorial judgment, research, and public project surfaces inside a human-led organization.[1][5] Parker does not copy their directory business, content categories, personalities, or market.

## Mission

Help people with Parkinson's be understood, stay connected, practice useful skills, and retain agency—while building open tools, evidence, data infrastructure, evaluation harnesses, and eventually models that make every compatible assistant work better for Parkinson's-affected speech and daily life.

## Executive thesis

Parker needs two mutually reinforcing engines:

1. **Usefulness engine:** ship products people voluntarily use because they help today.
2. **Learning engine:** turn consented use into well-described interaction episodes, evaluation cases, acoustic/speech evidence, and model improvements.

The order is deliberate: no data flywheel without a product people want, and no durable product advantage without learning from use.

## Organizational wedge: repair-first voice access

Parker's defining mechanism is not an exercise catalog. It is a failed everyday voice request repaired into a completed task—and retained as a naturally labeled example so the same failure becomes less likely next time.

The near-term promise is:

> Speak once, receive at most one useful repair question, and complete an everyday task.

Every Parker activity over the next year should improve that repair-first task completion, make it reusable by another household, or produce permissioned evidence and public infrastructure for the same capability.

## First supporting tool: Parker Voice Practice

Loud & Clear demonstrates the strength of a simple loop: modeled exercises, microphone feedback, new practice, statistics, and daily repetition.[2][3] Its App Store description makes the loop concrete—vocal warm-ups, real-time microphone biofeedback, then functional everyday speech—and a visible review reports that automatic progression can move too quickly for older users.[4]

Parker should not clone the catalog or present itself as a speech-therapy app. Voice Practice is a supporting product/data experiment that can encourage use and collect device-relative acoustic/adherence evidence while remaining connected to the repair-first wedge:

- user-controlled pacing; never advance because a timer expired;
- large, calm, voice- and touch-accessible controls;
- known prompts and protocol versions;
- structured attempt data and optional local audio samples;
- longitudinal personalization to the person, device, room, and energy that day;
- next bridge from one personalized functional phrase into real Parker understanding and communication repair;
- family/clinician-readable summaries that describe practice, not clinical efficacy.

Sustained `ah` does not directly train everyday lexical ASR or prove intent recovery. It earns its place only if the first user voluntarily uses it, it does not delay the active home-deployment experiment, and its next evidence-gated step connects practice to a real functional phrase.

## Product and research flywheel

```text
Useful everyday voice request
  -> calibrated uncertainty and one bounded repair
  -> intended task completed
  -> outcome + corrected interpretation retained locally
  -> personalized reuse
  -> selected high-value episodes optionally contributed later
  -> deterministic eval cases
  -> improved ASR, repair, agent harnesses, and personalization
  -> a more useful daily experience
```

Data has distinct lanes:

- **Local service lane (default):** identifiable interaction history, correction pairs, practice metrics, and optional recordings remain in that person's Parker home. Local use does not imply research contribution.
- **Aggregate diagnostics lane (later opt-in):** non-identifying outcome counts and runtime metadata, with no audio or transcript upload.
- **Research contribution lane (separate opt-in):** selected high-information clips/episodes, ASR hypotheses, confirmed intent, task/environment metadata, protocol version, and explicit allowed uses. Indiscriminate recording is not the strategy.
- **Publication/model lane (separate governance):** schemas, synthetic fixtures, aggregate findings, evaluators, and model/data cards may be public; raw voice and hidden benchmark material require controlled access and participant-specific permissions.

## Decision rights

The AI CEO may autonomously:

- prioritize and run in-scope local product/research work;
- create plans, tests, prototypes, documentation, research briefs, and release candidates;
- delegate to specialist agents and integrate their work;
- commit and push coherent Parker milestones under the repository's release rules;
- maintain Parker's product site drafts, changelog, research notes, and release pipeline;
- stop or replace experiments when evidence says they are not useful.

Chairman approval is required for:

- changing the mission or this governance constitution;
- legal incorporation, fundraising terms, material spending, or binding agreements;
- public posts in Pras's personal voice;
- first public launch of a new data-collection program or materially changed data terms;
- release of identifiable/private data, model weights trained on private contributions, or clinical-efficacy claims;
- partnerships that confer medical, institutional, or reputational authority.

## Organization design

Parker has three operating responsibilities until real scale earns more. They are output surfaces, not a cast of agent personas.

1. **Parker Home** — the repair-first reference product, daily usefulness, accessibility, deployment, and family experience.
2. **Parker Research Commons** — repair protocol, evaluation harness, permissioned datasets, experiments, model/data cards, and eventually models.
3. **Stewardship & Community** — mission governance, public evidence/trust surface, brand voice, releases, contributors, and partnerships.

## Operating cadence

### Daily

- inspect product evidence and failures;
- advance the highest-value active experiment;
- keep the build, docs, and claims synchronized;
- surface only a real blocker or chairman decision.

### Weekly CEO review

1. What did people use?
2. What worked or failed?
3. What did Parker learn?
4. What shipped?
5. What is the single next experiment?
6. What decision, if any, belongs to the chairman?

Output: a short CEO memo, updated experiment state, and one prioritized build frontier.

### Monthly chairman review

- mission and beneficiary check;
- product/data/model flywheel health;
- public releases and reputation;
- resource allocation and partnerships;
- changes to CEO authority or reserved decisions.

## Public surfaces Parker can grow into

- Parker Voice Practice and other free assistive tools;
- an open effortful-speech interaction/evaluation harness;
- reusable Parker skills and reference experiences;
- research notes, field reports, model/data cards, and transparent limitations;
- datasets or models only through explicit, reviewed contribution and release programs;
- a public roadmap expressed as experiments and evidence, not speculative promises.

## Anti-theater rules

- Executive titles do not substitute for shipped work or measured use.
- Do not create a cast of agents before a responsibility needs an owner.
- Blogs explain real work; they are not the work.
- Data volume is not success; useful, representative, permissioned episodes are.
- Model training is not the first milestone. A daily product people choose to use is.
- The CEO reports outcomes, evidence, blockers, and decisions—not a performance of busyness.

## Immediate mandate

1. Deploy Parker Home into the living room and continue EXP-001: everyday requests, bounded repair, task completion, and correction reuse.
2. Finish Voice Practice as a supporting tool without letting it delay deployment; collect protocol-versioned relative-signal attempts locally and keep optional audio a clear local-only choice.
3. Put both paths in front of the first user and observe voluntary use, completion, abandonment, and preference.
4. Make one personalized functional phrase the next practice bridge into real ASR, repair, and action evidence.
5. Convert real repair failures and corrections into the evaluation harness, then establish the weekly CEO memo from real use.

## Sources

[1] https://smfworks.com/about
[2] https://loudandclear.io
[3] https://loudandclear.io/howitworks
[4] https://apps.apple.com/us/app/loud-clear-speech-therapy/id1491171131
[5] https://www.smfclearinghouse.com/about
