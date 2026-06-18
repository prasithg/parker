# Parker degraded-input replay eval v0

- Date: 2026-06-18
- Provenance: synthetic held-out transcript-level smoke check; no private family/patient data; no model/API dependency.
- Purpose: convert Claw's Night4 correction into one quantitative interaction metric with a non-interactive baseline.

## Pre-registered primary metric

| Metric | Value |
| --- | ---: |
| Parker repair protocol intent recovery | 100.00% |
| Non-interactive no-repair intent recovery | 0.00% |
| Delta | 100.00% |
| Success threshold delta | 34.00% |
| Safety-critical misses | 0 |
| Threshold met | True |

## Baseline details

| Baseline | Intent recovery | Repair initiated | Median turns to resolution | Safety-critical misses |
| --- | ---: | ---: | ---: | ---: |
| non_interactive_no_repair | 0.00% | 0.00% | n/a | 0 |
| parker_repair_protocol | 100.00% | 100.00% | 2 | 0 |

## Case breakdown

### non_interactive_no_repair

- **FAIL** `deg-001-reminder-tomato-evening`: action=None, turns=None — no repair loop; asks the user to repeat and recovers no confirmed intent
- **FAIL** `deg-002-family-message-physio`: action=None, turns=None — no repair loop; asks the user to repeat and recovers no confirmed intent
- **FAIL** `deg-003-reminder-garden-call`: action=None, turns=None — no repair loop; asks the user to repeat and recovers no confirmed intent

### parker_repair_protocol

- **PASS** `deg-001-reminder-tomato-evening`: action=reminder, turns=2
- **PASS** `deg-002-family-message-physio`: action=family_message, turns=2
- **PASS** `deg-003-reminder-garden-call`: action=reminder, turns=2

## Grant-readiness caveat

This number is useful because it prevents pure proposal polish from masquerading as an interactivity result. It is not enough to claim real-world Parkinson's speech performance. The grant-funded version still needs real audio or consented participant data, richer degraded-input slices, a stronger non-interactive baseline, realtime latency instrumentation, and human/model grading of repair-choice quality.
