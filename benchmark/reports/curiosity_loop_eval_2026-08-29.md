# Patient Curiosity Loop eval v0 — 2026-08-29

Gate: **PASS**

Deterministic eval of the real converse harness path (ConverseStore →
TextSession → CuriosityBrain) with fake providers. Scores the strategy
doc's go/no-go loop: current answer with visible sources, follow-up
continuity, honest failure, and Stop that never leaks a stale result.

| Case | Result | Detail |
|---|---|---|
| weather-today-tomorrow.0 | ok | It's 14 and partly cloudy in Fitzroy right now. Today looks partly cloudy with a |
| weather-today-tomorrow.1 | ok | Tomorrow looks lightly rainy with a top of 19, down to 9 overnight and a 65 perc |
| score-then-followup.0 | ok | Celtics won — 112 to 104 over Lakers. |
| score-then-followup.1 | ok | Celtics played Lakers. Celtics won — 112 to 104 over Lakers. |
| interest-then-followup.0 | ok | Uri Levine is an entrepreneur best known for co-founding Waze. |
| interest-then-followup.1 | ok | He co-founded Waze and urges founders to fall in love with the problem. |
| provider-down-then-recovers | ok | down='I couldn't reach the weather service just now. Try me again ' recovered='I |
| silence-gentle-retry | ok | I didn't catch anything that time — take your time and try a |
| unknown-place-honest | ok | I couldn't find a town called Zzyzxq. Could you say the town |
| refusal-before-provider | ok | kind=refused provider_calls=0 |
| purchase-held-at-human-gate | ok | needs_human_approval |
| stop-races | ok | 20 races, 0 stale |

Note: Deterministic harness-path eval with fake providers; the live probe is reachability evidence only. Real-latency receipts come from the laptop smoke (PARKER_HOME/receipts/converse_latency.jsonl).
