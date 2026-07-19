# Parker repair-quality rubric eval v0

- Date: 2026-07-19
- Purpose: proxy-check repair-choice specificity/safety and prevent generic fallback choices from being cited as quality evidence.
- Provenance: public synthetic/local rubric cases only; no private data; no model/API dependency; not human-graded evidence.

## Gate

| Metric | Value |
| --- | ---: |
| Total cases | 5 |
| Reference passing | 5 |
| Generic fallback passing | 0 |
| Rubric detects generic fallback | True |
| Quality proof claim allowed | False |
| Gate passed | True |

## Grant posture

- Safe claim: A deterministic synthetic rubric now checks repair-choice specificity and safety; the generic no-key fallback is explicitly flagged as non-citable quality evidence.
- Required caveat: Synthetic proxy rubric only; not human-graded repair quality, not real patient/audio evidence, and not a claim that Parker's fallback choices are semantically good.
- Next research step: Use this rubric as a seed for human/caregiver repair-choice grading and realtime audio slices.

## Case results

| Case | Reference | Generic fallback | Required terms |
| --- | ---: | ---: | --- |
| rq-001-garden-neighbor | True | False | garden |
| rq-002-doctor-thing | True | False | doctor, appointment |
| rq-003-physio-morning | True | False | physio, morning |
| rq-004-daughter-visit | True | False | daughter, visit |
| rq-005-tuesday-appointment | True | False | tuesday, appointment |
