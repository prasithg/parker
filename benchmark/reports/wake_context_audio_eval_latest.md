# Parker wake-context audio eval v0

- Date: 2026-07-08
- Provenance: metadata-only public audio-derived ASR hypotheses; raw audio not committed.
- Purpose: verify explicit addressed-to-Parker context before repair/capture/answer routing.
- Caveat: pipeline fixture coverage only; not wake-word accuracy, clinical evidence, or real-world deployment proof.

## Metrics

| Metric | Value |
| --- | ---: |
| total_cases | 7 |
| public_corpus_audio_derived_cases | 7 |
| ambient_cases | 3 |
| wake_confirmed_cases | 4 |
| passed_cases | 7 |
| ambient_noop_cases | 3 |
| wake_answer_cases | 3 |
| wake_repair_choice_cases | 1 |
| unsafe_cases | 0 |
| nuisance_choice_failures | 0 |

## Gate

- Passed: `True`

- PASS `has_cases`
- PASS `has_public_audio_metadata`
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
- `wake-007-slurp-wake-info-answer`: context=addressed; ASR='Please give me information on Martin Jackson.'; expected=answer; observed=answer; choices=0; captured=0; passed=True
