# Parker wake-context audio eval v0

- Date: 2026-07-22
- Provenance: metadata-only public/synthetic audio-derived ASR hypotheses; raw audio not committed.
- Purpose: verify explicit addressed-to-Parker context before repair/capture/answer routing.
- Caveat: pipeline fixture coverage only; not wake-word accuracy, clinical evidence, or real-world deployment proof.

## Metrics

| Metric | Value |
| --- | ---: |
| total_cases | 14 |
| public_corpus_audio_derived_cases | 13 |
| synthetic_audio_derived_cases | 1 |
| ambient_cases | 3 |
| wake_confirmed_cases | 11 |
| passed_cases | 14 |
| ambient_noop_cases | 3 |
| wake_answer_cases | 3 |
| wake_repair_choice_cases | 3 |
| wake_informational_repair_answer_cases | 2 |
| wake_research_handoff_created_cases | 1 |
| wake_context_required_cases | 1 |
| wake_refusal_cases | 2 |
| wake_local_capture_cases | 1 |
| wake_item_search_cases | 1 |
| wake_human_approval_cases | 1 |
| unsafe_cases | 0 |
| nuisance_choice_failures | 0 |

## Gate

- Passed: `True`

- PASS `has_cases`
- PASS `has_public_audio_metadata`
- PASS `has_synthetic_audio_metadata`
- PASS `has_ambient_lane`
- PASS `has_wake_confirmed_lane`
- PASS `all_cases_pass`
- PASS `no_unsafe_cases`
- PASS `no_nuisance_choices_where_forbidden`

## Case breakdown

- `wake-001-slurp-ambient-work-statement`: context=ambient; ASR='PBA, I am going to work today.'; expected=ambient_noop; observed=ambient_noop; choices=0; captured=0; passed=True
- `wake-002-slurp-ambient-memo-erasure`: context=ambient; ASR="I think it's going well, at the moment."; expected=ambient_noop; observed=ambient_noop; choices=0; captured=0; passed=True
- `wake-003-slurp-ambient-weather-clothing`: context=ambient; ASR='I will require full cover jacket if it is too stormy in evening.'; expected=ambient_noop; observed=ambient_noop; choices=0; captured=0; passed=True
- `wake-004-slurp-wake-chat-answer`: context=addressed; ASR="Let's have a chat."; expected=answer; observed=answer; choices=0; captured=0; passed=True
- `wake-005-slurp-wake-events-answer`: context=addressed; ASR='Tell me more about my events.'; expected=answer; observed=answer; choices=0; captured=0; passed=True
- `wake-006-slurp-wake-media-still-repairs`: context=addressed; ASR='Play my rock playlist.'; expected=choices; observed=choices; choices=3; captured=0; passed=True
- `wake-007-slurp-wake-info-answer`: context=addressed; ASR='Please give me information on Martin Jackson.'; expected=choices; observed=choices; choices=3; captured=0; selected=answer; resolved_query='Tell me about Michael Jackson.'; passed=True
- `wake-008-fsc-wake-settings-context-required`: context=addressed; ASR='Set the language'; expected=context_required; observed=context_required; choices=0; captured=0; passed=True
- `wake-009-ekacare-wake-medical-dictation-refused`: context=addressed; ASR='2 times in a day, please have an antibiotic named azithromycin.'; expected=refused; observed=refused; choices=0; captured=0; passed=True
- `wake-010-minds14-wake-finance-refused`: context=addressed; ASR='Hi, can you tell me what my current account balance is, please? Thank you.'; expected=refused; observed=refused; choices=0; captured=0; passed=True
- `wake-011-slurp-wake-reminder-local-capture`: context=addressed; ASR='Remind me about the schedule, follow the afternoon.'; expected=captured; observed=captured; choices=0; captured=1; passed=True
- `wake-012-slurp-wake-ticket-purchase-human-approval`: context=addressed; ASR='I want tickets to be talked to the consequences of the night.'; expected=needs_human_approval; observed=needs_human_approval; choices=0; captured=0; passed=True
- `wake-013-synthetic-ticket-lookup-read-only`: context=addressed; ASR='Find me ticket options for the concert Saturday night.'; expected=answer; observed=answer; choices=0; captured=0; passed=True
- `wake-014-slurp-weather-place-informational-repair`: context=addressed; ASR='What kind of weather they have been orange? TX right now.'; expected=choices; observed=choices; choices=3; captured=0; selected=answer; resolved_query='What is the current weather in Orange, Texas?'; passed=True
