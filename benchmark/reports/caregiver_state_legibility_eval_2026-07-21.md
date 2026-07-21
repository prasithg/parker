# Parker caregiver-state legibility eval v0

- Date: 2026-07-21
- Purpose: score whether the local review surface makes state buckets and safe next actions legible versus a raw chat-only baseline.
- Provenance: synthetic/local review-state tasks plus sanitized public-audio metadata; no raw audio, private data, or model/API dependency.

## Gate

| Metric | Value |
| --- | ---: |
| Total tasks | 10 |
| Parker review UI correct | 10 |
| Raw chat-only correct | 0 |
| Delta vs raw chat | 1.0 |
| Unsafe misses | 0 |
| Audio-grounded lifecycle tasks | 4 |
| Gate passed | True |

## State buckets checked

- `escalation_candidates`
- `outbox_approved`
- `outbox_queued`
- `pending_actions`
- `recent_cancelled`
- `research_handoff_cancelled`
- `research_handoff_completed`
- `research_handoff_ready`
- `research_handoff_redacted`
- `safety_contract`

## Task results

- **PASS** `csl-001-pending-confirmation` `parker_review_ui` — ok
- **FAIL** `csl-001-pending-confirmation` `raw_chat_only` — bucket expected 'pending_actions', got 'unknown'; status expected 'staged', got 'unknown'; missing allowed actions: ['cancel', 'confirm']; local_only expected True, got False; missing provenance fields: ['action_type', 'status', 'subject']
- **PASS** `csl-002-queued-local-outbox` `parker_review_ui` — ok
- **FAIL** `csl-002-queued-local-outbox` `raw_chat_only` — bucket expected 'outbox_queued', got 'unknown'; status expected 'queued_local', got 'unknown'; missing allowed actions: ['approve_local', 'cancel']; local_only expected True, got False; missing provenance fields: ['body', 'recipient', 'status']
- **PASS** `csl-003-approved-still-local-outbox` `parker_review_ui` — ok
- **FAIL** `csl-003-approved-still-local-outbox` `raw_chat_only` — bucket expected 'outbox_approved', got 'unknown'; status expected 'approved_local', got 'unknown'; missing allowed actions: ['cancel']; local_only expected True, got False; missing provenance fields: ['approved_by', 'recipient', 'status']
- **PASS** `csl-004-cancelled-audit-row` `parker_review_ui` — ok
- **FAIL** `csl-004-cancelled-audit-row` `raw_chat_only` — bucket expected 'recent_cancelled', got 'unknown'; status expected 'cancelled', got 'unknown'; local_only expected True, got False; missing provenance fields: ['cancelled_at', 'cancelled_by', 'execution_result']
- **PASS** `csl-005-non-response-candidate` `parker_review_ui` — ok
- **FAIL** `csl-005-non-response-candidate` `raw_chat_only` — bucket expected 'escalation_candidates', got 'unknown'; status expected 'open', got 'unknown'; missing allowed actions: ['review']; local_only expected True, got False; review_only expected True, got False; missing provenance fields: ['notified_contacts', 'reason', 'severity']
- **PASS** `csl-006-visible-safety-contract` `parker_review_ui` — ok
- **FAIL** `csl-006-visible-safety-contract` `raw_chat_only` — bucket expected 'safety_contract', got 'unknown'; status expected 'visible', got 'unknown'; local_only expected True, got False; missing provenance fields: ['no_outbound_sends', 'privacy_boundary', 'two_human_gate']
- **PASS** `csl-007-research-handoff-ready` `parker_review_ui` — ok
- **FAIL** `csl-007-research-handoff-ready` `raw_chat_only` — bucket expected 'research_handoff_ready', got 'unknown'; status expected 'ready', got 'unknown'; missing allowed actions: ['cancel', 'complete']; local_only expected True, got False; missing provenance fields: ['provenance_status', 'query', 'risk_label', 'selected_interpretation', 'source_kind', 'status']
- **PASS** `csl-008-research-handoff-completed` `parker_review_ui` — ok
- **FAIL** `csl-008-research-handoff-completed` `raw_chat_only` — bucket expected 'research_handoff_completed', got 'unknown'; status expected 'completed', got 'unknown'; local_only expected True, got False; missing provenance fields: ['completed_at', 'completed_by', 'provenance_status', 'query', 'status']
- **PASS** `csl-009-research-handoff-cancelled` `parker_review_ui` — ok
- **FAIL** `csl-009-research-handoff-cancelled` `raw_chat_only` — bucket expected 'research_handoff_cancelled', got 'unknown'; status expected 'cancelled', got 'unknown'; local_only expected True, got False; missing provenance fields: ['cancelled_at', 'cancelled_by', 'provenance_status', 'query', 'status']
- **PASS** `csl-010-research-handoff-redacted` `parker_review_ui` — ok
- **FAIL** `csl-010-research-handoff-redacted` `raw_chat_only` — bucket expected 'research_handoff_redacted', got 'unknown'; status expected 'completed', got 'unknown'; local_only expected True, got False; missing provenance fields: ['completed_at', 'completed_by', 'provenance_status', 'redacted_at', 'redacted_by', 'redaction_reason', 'risk_label', 'status']

## Release posture

- Safe claim: A synthetic caregiver-state proxy now checks whether Parker's review surface makes pending, queued, approved, cancelled, non-response-candidate, and no-send safety-contract state identifiable versus a raw chat-only baseline, including ready/completed/cancelled local research cards and redacted-query audit grounded in one reviewed public-audio metadata episode.
- Required caveat: Synthetic local review-state proxy with sanitized public-audio metadata only; not a caregiver usability study, not human-graded or ASR-performance evidence, no raw audio, and no private family or medical data.
- Human usability claim allowed: false
