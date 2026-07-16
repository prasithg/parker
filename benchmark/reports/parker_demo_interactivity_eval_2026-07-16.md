# Parker interactivity eval v0

- Date: 2026-07-16
- Predictions: benchmark/reports/parker_demo_interactivity_predictions_latest.json
- Scenarios: 8

## Metrics

| Metric | Value |
| --- | --- |
| overall_pass_rate | 100.00% |
| unsafe_miss_count | 0 |

## Dimension scores

| Dimension | Score |
| --- | --- |
| caregiver_ui_clarity | 100.00% |
| confirmation_before_action | 100.00% |
| confirmation_restatement_binding | 100.00% |
| interruption_changed_mind_handling | 100.00% |
| latency_turn_count | 100.00% |
| local_outbox_reversibility | 100.00% |
| repair_under_uncertain_speech | 100.00% |
| unsafe_action_suppression | 100.00% |

## Thinking Machines criteria alignment

- **relevance:** Targets real-time assistive interactions where speech may be effortful, users may interrupt, and family/caregiver state must remain visible.
- **feasibility:** Runs as a local deterministic pytest/CLI harness with JSON fixtures; no live APIs, no private audio, and no model dependency.
- **construct_validity:** Scores observable interaction behaviors tied to the claimed capabilities: repair choice structure, changed-mind cancellation, confirmation ordering and readback/action binding, caregiver UI fields, latency/turn budgets, local outbox reversibility, and unsafe-action suppression.
- **simplicity_and_generality:** Plain JSON traces can be produced by Parker, another voice agent, or a public benchmark runner; metrics are independent of Parker internals.

## Safety-critical misses (0)

None.

## Other failures (0)

None.

## Current product trace note

- TextSession handles changed-mind draft revisions and cancel-only steering, cancels queued local outbox messages, and binds spoken confirmation to the exact action type, recipient, subject, and intent text that Parker read back.
