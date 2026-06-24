"""Parker recliner/TV evening-loop tests."""

from fastapi.testclient import TestClient

from app.evening.session import (
    EVENING_PROMPT_CLINICAL_DENYLIST,
    LocalEveningSession,
    cancel_local_evening_session,
    complete_local_evening_session,
    list_recent_local_evening_sessions,
    note_evening_silence,
    record_evening_response,
    start_local_evening_session,
)
from app.main import app


class StubNonResponseLadder:
    def __init__(self):
        self.calls = []

    def note_silence(self, session_id: int) -> None:
        self.calls.append(session_id)


def test_evening_loop_start_is_idempotent_per_calendar_evening(db):
    first = start_local_evening_session(db, now="2026-06-23T19:00:00")
    second = start_local_evening_session(db, now="2026-06-23T21:30:00")

    assert second.id == first.id
    assert second.status == "offered"
    assert db.query(LocalEveningSession).count() == 1

    tomorrow = start_local_evening_session(db, now="2026-06-24T18:45:00")

    assert tomorrow.id != first.id
    assert db.query(LocalEveningSession).count() == 2


def test_evening_loop_decline_is_optional_and_not_reoffered_that_evening(db):
    session = start_local_evening_session(db, now="2026-06-23T19:00:00")

    declined = record_evening_response(db, session.id, "not now", now="2026-06-23T19:01:00")

    assert declined.status == "declined"
    assert declined.declined_at.isoformat() == "2026-06-23T19:01:00"
    assert "not now" in declined.last_response

    later = record_evening_response(db, session.id, "yes please", now="2026-06-23T20:00:00")
    same_evening = start_local_evening_session(db, now="2026-06-23T20:05:00")

    assert later.status == "declined"
    assert same_evening.id == session.id
    assert same_evening.status == "declined"
    assert "later response after decline" in (same_evening.caregiver_note or "")


def test_evening_loop_unclear_short_response_offers_numbered_repair_choice(db):
    session = start_local_evening_session(db, now="2026-06-23T19:00:00")

    repaired = record_evening_response(db, session.id, "mm", now="2026-06-23T19:01:00")

    assert repaired.status == "offered"
    assert "1)" in repaired.prompt_card
    assert "2)" in repaired.prompt_card
    assert "recliner" in repaired.prompt_card.lower()
    assert "tv" in repaired.prompt_card.lower()
    for forbidden in EVENING_PROMPT_CLINICAL_DENYLIST:
        assert forbidden not in repaired.prompt_card.lower()


def test_evening_loop_done_from_offer_completes_repair_option(db):
    session = start_local_evening_session(db, now="2026-06-23T19:00:00")

    completed = record_evening_response(db, session.id, "done", now="2026-06-23T19:02:00")

    assert completed.status == "completed"
    assert completed.completed_at is not None
    assert completed.completed_at.isoformat() == "2026-06-23T19:02:00"
    assert "quiet night" in completed.prompt_card.lower()


def test_evening_loop_engagement_path_completes_without_medical_language(db):
    session = start_local_evening_session(db, now="2026-06-23T19:00:00")

    engaged = record_evening_response(db, session.id, "yes", now="2026-06-23T19:02:00")

    assert engaged.status == "engaged"
    assert "recliner" in engaged.prompt_card.lower()
    assert "tv" in engaged.prompt_card.lower()

    completed = record_evening_response(db, session.id, "goodnight", now="2026-06-23T19:15:00")

    assert completed.status == "completed"
    assert completed.completed_at.isoformat() == "2026-06-23T19:15:00"
    all_copy = f"{engaged.prompt_card}\n{completed.prompt_card}".lower()
    for forbidden in EVENING_PROMPT_CLINICAL_DENYLIST:
        assert forbidden not in all_copy


def test_evening_loop_silence_times_out_and_calls_nonresponse_ladder_once(db):
    session = start_local_evening_session(db, now="2026-06-23T19:00:00")
    ladder = StubNonResponseLadder()

    timed_out = note_evening_silence(
        db,
        session.id,
        non_response_ladder=ladder,
        now="2026-06-23T19:05:00",
    )
    repeated = note_evening_silence(
        db,
        session.id,
        non_response_ladder=ladder,
        now="2026-06-23T19:06:00",
    )

    assert timed_out.status == "timed_out"
    assert repeated.status == "timed_out"
    assert timed_out.timed_out_at.isoformat() == "2026-06-23T19:05:00"
    assert ladder.calls == [session.id]


def test_evening_loop_silence_after_terminal_session_is_noop(db):
    declined = start_local_evening_session(db, now="2026-06-23T19:00:00")
    declined = record_evening_response(db, declined.id, "not now", now="2026-06-23T19:01:00")
    cancelled = start_local_evening_session(db, now="2026-06-24T19:00:00")
    cancelled = cancel_local_evening_session(db, cancelled.id, now="2026-06-24T19:02:00")
    assert cancelled is not None
    completed = start_local_evening_session(db, now="2026-06-25T19:00:00")
    completed = record_evening_response(db, completed.id, "done", now="2026-06-25T19:01:00")
    ladder = StubNonResponseLadder()

    for session in (declined, cancelled, completed):
        before_status = session.status
        result = note_evening_silence(
            db,
            session.id,
            non_response_ladder=ladder,
            now="2026-06-26T19:05:00",
        )
        assert result.status == before_status
        assert result.silence_noted_at is None

    assert ladder.calls == []


def test_caregiver_complete_declined_evening_session_is_noop(db):
    session = start_local_evening_session(db, now="2026-06-23T19:00:00")
    declined = record_evening_response(db, session.id, "not now", now="2026-06-23T19:01:00")

    result = complete_local_evening_session(
        db,
        declined.id,
        caregiver_note="caregiver tried to mark done after decline",
        now="2026-06-23T19:20:00",
    )

    assert result is not None
    assert result.status == "declined"
    assert result.completed_at is None
    assert result.caregiver_note is None


def test_caregiver_review_surfaces_evening_sessions_and_review_controls(db):
    session = start_local_evening_session(db, now="2026-06-23T19:00:00")
    record_evening_response(db, session.id, "yes", now="2026-06-23T19:01:00")
    client = TestClient(app)

    review = client.get("/parker/review").json()

    assert "recent_evening_sessions" in review
    assert review["recent_evening_sessions"][0]["id"] == session.id
    assert review["recent_evening_sessions"][0]["status"] == "engaged"
    assert "recliner" in review["recent_evening_sessions"][0]["prompt_card"].lower()

    completed = client.post(
        f"/parker/evening/{session.id}/complete",
        json={"caregiver_note": "Evening loop completed locally.", "now": "2026-06-23T19:20:00"},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["caregiver_note"] == "Evening loop completed locally."

    page = client.get("/parker/review/ui").text
    assert "Evening loop" in page
    assert "recent_evening_sessions" in page
    assert "/parker/evening/${s.id}/complete" in page


def test_evening_session_caregiver_can_cancel_and_history_is_newest_first(db):
    first = start_local_evening_session(db, now="2026-06-23T19:00:00")
    second = start_local_evening_session(db, now="2026-06-24T19:00:00")

    cancelled = cancel_local_evening_session(
        db,
        first.id,
        caregiver_note="Dad changed his mind.",
        now="2026-06-23T19:03:00",
    )
    completed = complete_local_evening_session(
        db,
        second.id,
        caregiver_note="TV and recliner routine completed.",
        now="2026-06-24T19:20:00",
    )
    history = list_recent_local_evening_sessions(db)

    assert cancelled.status == "cancelled"
    assert completed.status == "completed"
    assert [item.id for item in history] == [second.id, first.id]


def test_evening_loop_module_has_no_outbound_network_surface():
    import inspect
    import app.evening.session as evening_session

    source = inspect.getsource(evening_session)

    assert "requests" not in source
    assert "httpx" not in source
    assert "send_message" not in source
    assert "dispatch" not in source


def test_create_tables_imports_evening_session_model_directly():
    import inspect
    import app.db.database as database

    source = inspect.getsource(database.create_tables)

    assert "app.evening.session" in source
