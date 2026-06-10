"""Deterministic demo seed and transcript replay tests."""

from datetime import datetime

from fastapi.testclient import TestClient

from app.db.models import CapturedIntent, OutboxMessage
from app.demo.replay import DEMO_SCRIPT, replay_transcript
from app.demo.seed import seed_demo_data
from app.main import app

NOW = datetime(2026, 6, 10, 15, 0, 0)


def test_seed_produces_the_documented_review_state(db):
    summary = seed_demo_data(db, now=NOW)

    assert summary["skipped"] is False
    client = TestClient(app)
    review = client.get("/parker/review").json()

    assert len(review["pending_actions"]) == summary["pending_confirmation"] == 3
    subjects = {item["subject"] for item in review["pending_actions"]}
    assert "afternoon stretches" in subjects
    assert "water the tomato plants" in subjects
    drafts = [item for item in review["pending_actions"] if item["action_type"] == "family_message"]
    assert len(drafts) == 1
    assert drafts[0]["recipient"] == "Rohan"
    assert drafts[0]["message_text"]  # confirmation restates the message

    assert len(review["outbox_queued"]) == 1
    assert review["outbox_queued"][0]["recipient"] == "Sarah"
    assert len(review["escalation_candidates"]) == 1
    assert "afternoon stretches" in review["escalation_candidates"][0]["reason"]
    assert review["open_escalations"] == []


def test_seed_is_deterministic_for_a_fixed_now(db):
    first = seed_demo_data(db, now=NOW)

    assert first == {
        "skipped": False,
        "call_log_id": first["call_log_id"],
        "pending_confirmation": 3,
        "outbox_queued": 1,
        "escalation_candidates": 1,
        "executed_history": 2,
    }


def test_seed_refuses_to_run_twice(db):
    seed_demo_data(db, now=NOW)

    second = seed_demo_data(db, now=NOW)

    assert second["skipped"] is True
    assert "reset-db" in second["reason"]
    assert db.query(OutboxMessage).count() == 1  # nothing duplicated


def test_replay_routes_the_full_script_safely(db):
    exchanges = replay_transcript(db)

    assert len(exchanges) == len(DEMO_SCRIPT)
    kinds = [exchange["kind"] for exchange in exchanges]
    assert kinds == [
        "captured",   # tomato plants reminder
        "captured",   # message to Sarah
        "choices",    # disfluent garden utterance → repair choices
        "captured",   # selection 1 → reminder
        "refused",    # medication change
        "needs_human_approval",  # purchase
        "answer",     # weather question
    ]
    # Exactly three intents captured; the unsafe lines captured nothing.
    intents = db.query(CapturedIntent).all()
    assert len(intents) == 3
    by_action = sorted(intent.requested_action for intent in intents)
    assert by_action == ["message", "remind", "reminder"]
    message = next(i for i in intents if i.requested_action == "message")
    assert message.recipient == "Sarah"


def test_replay_is_repeatable_in_one_session_without_errors(db):
    replay_transcript(db)
    exchanges = replay_transcript(db)

    assert len(exchanges) == len(DEMO_SCRIPT)
    # Second run captures three more pending intents (replay is additive by design).
    assert db.query(CapturedIntent).count() == 6


def test_seed_and_replay_compose_for_the_full_demo(db):
    seed_demo_data(db, now=NOW)
    replay_transcript(db)
    client = TestClient(app)

    tick = client.post("/parker/tick", json={"now": NOW.isoformat()})

    assert tick.status_code == 200
    review = client.get("/parker/review").json()
    # 3 seeded pending + 3 replay captures staged by the tick.
    assert len(review["pending_actions"]) == 6
