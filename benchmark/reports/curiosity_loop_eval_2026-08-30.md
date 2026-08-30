# Patient Curiosity Loop eval v0 — 2026-08-30

Gate: **PASS**

Deterministic eval of the real converse harness path (ConverseStore →
TextSession → ClaudeBrainAdapter) with a scripted fake search client.
Every subject flows through the one general brain lane — web-search
citations surface as on-screen sources; there are no per-subject
provider lanes to maintain.

| Case | Result | Detail |
|---|---|---|
| weather-today-tomorrow.0 | ok | It's 14 and partly cloudy in Fitzroy right now, with a top of 16 expected. |
| weather-today-tomorrow.1 | ok | Tomorrow in Fitzroy looks rainy with a top of 19. |
| score-then-followup.0 | ok | No — Collingwood lost to the Bulldogs on Friday night, 96 to 93. |
| score-then-followup.1 | ok | Very close — it came down to the final minute, a three-point margin. |
| interest-then-followup.0 | ok | Uri Levine is an entrepreneur best known for co-founding Waze. |
| interest-then-followup.1 | ok | He champions falling in love with the problem, not the solution. |
| brain-down-then-recovers | ok | down='I couldn't reach my answers just now — try me again in a mom' |
| silence-gentle-retry | ok | I didn't catch anything that time — take your time and try a |
| refusal-before-brain | ok | kind=refused brain_calls=0 |
| purchase-held-at-human-gate | ok | needs_human_approval |
| vague-question-reasks | ok | kind=retry |
| stop-races | ok | 20 races, 0 stale |

Note: Deterministic harness-path eval with a scripted fake search client; every subject flows through the one general brain lane (no per-subject providers). The live lane is latency/reachability evidence only.
