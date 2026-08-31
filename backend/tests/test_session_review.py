"""The review-the-session surface: feed, detail, feedback, page.

Route-level pins for the human-testing flywheel (app/parker/session_review.py
+ /parker/sessions routes + the single-file page). The live end-to-end
scenarios live in tests/test_scenarios_review.py; here the journal rows are
built directly so each contract is pinned in isolation.
"""

from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.db.models import CallLog
from app.main import app
from app.parker import session_review
from app.parker.session_review import (
    MAX_FEEDBACK_NOTE_CHARS,
    RealtimeSessionEvent,
    build_session_detail,
    record_event_sync,
)
from app.parker.sessions_ui import SESSIONS_PAGE_HTML

client = TestClient(app)


def _factory(db):
    maker = sessionmaker(bind=db.get_bind())
    return lambda: maker()


def _seed_session(db, call_sid="REALTIME-abc123"):
    make_db = _factory(db)
    record_event_sync(
        make_db, call_sid, 1, "turn", "when does Alcaraz play next",
        "Friday night.", {"t_ms": 1500, "guard_tripped": False},
    )
    record_event_sync(
        make_db, call_sid, 2, "injection", "", "He plays Friday, around seven.",
        {"worker": "search", "question": "when does Alcaraz play next",
         "worker_ms": 240, "age_s": 1, "since_ask_ms": 900, "t_ms": 2400},
    )
    db.expire_all()
    return call_sid


def test_feed_is_empty_when_no_live_sessions_exist(db):
    assert client.get("/parker/sessions").json() == {"sessions": []}


def test_feed_lists_sessions_newest_first_with_counts(db):
    _seed_session(db, "REALTIME-older")
    _seed_session(db, "REALTIME-newer")
    sessions = client.get("/parker/sessions").json()["sessions"]
    assert [s["call_sid"] for s in sessions] == ["REALTIME-newer", "REALTIME-older"]
    assert sessions[0]["turn_count"] == 1
    assert sessions[0]["feedback_count"] == 0
    assert sessions[0]["live"] is True  # no finalize ran; shown honestly


def test_detail_serves_the_journal_in_sequence_order(db):
    sid = _seed_session(db)
    detail = client.get(f"/parker/sessions/{sid}").json()
    assert [e["seq"] for e in detail["events"]] == [1, 2]
    turn, injection = detail["events"]
    assert turn["heard"] == "when does Alcaraz play next"
    assert turn["detail"]["t_ms"] == 1500
    assert injection["detail"]["since_ask_ms"] == 900
    assert detail["next_card"] == {"lines": [], "error": ""}  # empty world


def test_detail_404_for_unknown_or_non_realtime_sessions(db):
    assert client.get("/parker/sessions/REALTIME-missing").status_code == 404
    db.add(CallLog(call_sid="CONVERSE-x", call_type="converse"))
    db.commit()
    assert client.get("/parker/sessions/CONVERSE-x").status_code == 404


def test_feedback_files_against_a_turn_and_survives_reload(db):
    sid = _seed_session(db)
    event_id = client.get(f"/parker/sessions/{sid}").json()["events"][0]["id"]
    filed = client.post(
        f"/parker/sessions/{sid}/feedback",
        json={"event_id": event_id, "note": "  that felt wrong because it guessed  "},
    )
    assert filed.status_code == 200
    assert filed.json()["note"] == "that felt wrong because it guessed"
    detail = client.get(f"/parker/sessions/{sid}").json()
    assert detail["events"][0]["feedback"][0]["note"] == (
        "that felt wrong because it guessed"
    )
    assert client.get("/parker/sessions").json()["sessions"][0]["feedback_count"] == 1


def test_feedback_against_another_sessions_event_is_refused(db):
    sid_a = _seed_session(db, "REALTIME-aaa")
    sid_b = _seed_session(db, "REALTIME-bbb")
    event_a = client.get(f"/parker/sessions/{sid_a}").json()["events"][0]["id"]
    response = client.post(
        f"/parker/sessions/{sid_b}/feedback", json={"event_id": event_a}
    )
    assert response.status_code == 404  # never filed against the wrong conversation
    assert client.get("/parker/sessions").json()["sessions"][0]["feedback_count"] == 0


def test_feedback_note_is_capped(db):
    sid = _seed_session(db)
    event_id = client.get(f"/parker/sessions/{sid}").json()["events"][0]["id"]
    filed = client.post(
        f"/parker/sessions/{sid}/feedback",
        json={"event_id": event_id, "note": "x" * (MAX_FEEDBACK_NOTE_CHARS + 500)},
    )
    assert len(filed.json()["note"]) == MAX_FEEDBACK_NOTE_CHARS


def test_next_card_preview_reflects_current_memories(db):
    from app.memory.store import save_memory

    save_memory(db, "Sarah visits on Thursdays.", "fact")
    sid = _seed_session(db)
    detail = client.get(f"/parker/sessions/{sid}").json()
    assert any("Sarah visits on Thursdays." in line for line in detail["next_card"]["lines"])


def test_record_event_sync_creates_the_call_log_when_missing(db):
    record_event_sync(_factory(db), "REALTIME-fresh", 1, "turn", "hello there parker")
    db.expire_all()
    call = db.query(CallLog).filter(CallLog.call_sid == "REALTIME-fresh").one()
    assert call.call_type == "realtime"
    event = db.query(RealtimeSessionEvent).one()
    assert event.call_log_id == call.id


def test_detail_builder_returns_none_for_unknown(db):
    assert build_session_detail(db, "REALTIME-ghost") is None


def test_sessions_page_serves_with_conventions(db):
    response = client.get("/parker/sessions/ui")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    page = response.text
    assert "Parker — session review" in page
    assert "/parker/sessions" in page  # the page talks to its own local API
    # transcript and feedback text render through textContent, never
    # interpolated into markup
    assert "textContent" in page
    for dangerous in (
        "${ev.heard}",
        "${ev.said}",
        "${s.summary}",
        "${f.note}",
        "${d.question}",
        "${d.label}",
        "${d.note}",
        "${d.error}",
        "${d.action_type}",  # model-controlled — must never be interpolated
        "${a.action_type}",
        "${s.minted_memory}",
    ):
        assert dangerous not in page
    # the one-tap judgment control exists
    assert "Felt wrong" in page


def test_page_module_docstring_states_the_contract():
    from app.parker import sessions_ui

    assert "textContent" in (sessions_ui.__doc__ or "")
    assert SESSIONS_PAGE_HTML.strip().startswith("<!doctype html>")
