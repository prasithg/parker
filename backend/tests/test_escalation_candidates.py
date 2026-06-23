"""Non-response → escalation candidate tests."""

from datetime import datetime

from fastapi.testclient import TestClient

from app.db.models import CallLog, CapturedIntent, ResolutionResult, StagedAction
from app.escalation.candidates import flag_non_response_candidates
from app.escalation.models import Escalation
from app.main import app

NOW = datetime(2026, 6, 9, 12, 0, 0)


def _staged_action(
    db,
    *,
    resurface_count=3,
    last_resurfaced_at=datetime(2026, 6, 9, 10, 0, 0),
    status="staged",
    with_call=True,
    subject="drink water",
):
    call_id = None
    if with_call:
        call = CallLog(call_sid=f"CA_CAND_{resurface_count}_{subject}", call_type="check_in")
        db.add(call)
        db.commit()
        db.refresh(call)
        call_id = call.id
    captured = CapturedIntent(
        call_log_id=call_id,
        intent_text=f"Remind me to {subject}.",
        requested_action="remind",
        subject=subject,
        status="resolved",
    )
    db.add(captured)
    db.commit()
    resolution = ResolutionResult(
        captured_intent_id=captured.id,
        status="staged",
        action_type="reminder",
        reversible=True,
        summary=subject,
    )
    db.add(resolution)
    db.commit()
    action = StagedAction(
        resolution_result_id=resolution.id,
        status=status,
        action_type="reminder",
        action_payload=f'{{"subject": "{subject}"}}',
        resurface_count=resurface_count,
        last_resurfaced_at=last_resurfaced_at,
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    return action


def test_stale_unconfirmed_action_becomes_candidate_exactly_once(db):
    action = _staged_action(db)

    first = flag_non_response_candidates(db, now=NOW, resurface_threshold=3, quiet_minutes=30)
    second = flag_non_response_candidates(db, now=NOW, resurface_threshold=3, quiet_minutes=30)

    assert len(first) == 1
    assert second == []
    assert db.query(Escalation).count() == 1
    db.refresh(action)
    assert action.escalation_id == first[0].id


def test_candidate_is_info_severity_with_no_notifications(db):
    _staged_action(db)

    created = flag_non_response_candidates(db, now=NOW, resurface_threshold=3, quiet_minutes=30)

    candidate = created[0]
    assert candidate.severity == "info"
    assert candidate.status == "open"
    assert candidate.notified_contacts == "[]"
    assert "Non-response candidate" in candidate.reason
    assert "drink water" in candidate.reason
    assert "3x" in candidate.reason


def test_below_resurface_threshold_is_not_flagged(db):
    _staged_action(db, resurface_count=2)

    created = flag_non_response_candidates(db, now=NOW, resurface_threshold=3, quiet_minutes=30)

    assert created == []
    assert db.query(Escalation).count() == 0


def test_recent_resurface_within_quiet_window_is_not_flagged(db):
    _staged_action(db, last_resurfaced_at=datetime(2026, 6, 9, 11, 45, 0))

    created = flag_non_response_candidates(db, now=NOW, resurface_threshold=3, quiet_minutes=30)

    assert created == []


def test_confirmed_or_executed_actions_are_not_flagged(db):
    _staged_action(db, status="confirmed", subject="stretch")
    _staged_action(db, status="executed", subject="walk")

    created = flag_non_response_candidates(db, now=NOW, resurface_threshold=3, quiet_minutes=30)

    assert created == []


def test_never_resurfaced_action_is_not_flagged(db):
    _staged_action(db, resurface_count=0, last_resurfaced_at=None)

    created = flag_non_response_candidates(db, now=NOW, resurface_threshold=3, quiet_minutes=30)

    assert created == []


def test_intent_without_call_log_is_skipped_without_error(db):
    action = _staged_action(db, with_call=False)

    created = flag_non_response_candidates(db, now=NOW, resurface_threshold=3, quiet_minutes=30)

    assert created == []
    db.refresh(action)
    assert action.escalation_id is None  # stays eligible, never silently dropped


def test_tick_endpoint_reports_candidates(db):
    _staged_action(db)
    client = TestClient(app)

    tick = client.post("/parker/tick", json={"now": NOW.isoformat()})

    assert tick.status_code == 200
    assert tick.json()["escalation_candidates"] == 1
    # Candidate is visible through the normal escalation review flow.
    assert db.query(Escalation).filter(Escalation.status == "open").count() == 1
