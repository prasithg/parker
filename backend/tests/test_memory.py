"""Tests for conversation memory and context building."""

from fastapi.testclient import TestClient

from app.conversation.prompts import build_system_prompt_with_context
from app.db.models import CallLog, DoseLog, Medication
from app.main import app
from app.memory.store import (
    get_call_context,
    get_context_for_next_call,
    get_recent_memories,
    save_call_context,
    save_memory,
    search_memories,
)


def _call(db, sid="CA_MEM"):
    call = CallLog(call_sid=sid, call_type="check_in", patient_mood="good")
    db.add(call)
    db.commit()
    return call


def test_memory_crud_and_search(db):
    memory = save_memory(db, "Dad likes jazz music", "preference", source="manual")

    assert memory.id is not None
    assert get_recent_memories(db)[0].content == "Dad likes jazz music"
    assert search_memories(db, "jazz")[0].id == memory.id


def test_call_context_save_retrieve_and_next_context(db):
    call = _call(db)
    save_memory(db, "He enjoys the porch", "fact", call.id)
    rows = save_call_context(db, call.id, {"concerns_raised": "felt dizzy", "topics_discussed": ["garden"]})
    assert len(rows) == 2

    context = get_call_context(db, call.id)
    assert context["concerns_raised"] == "felt dizzy"
    assert "garden" in context["topics_discussed"]

    next_context = get_context_for_next_call(db)
    assert "He enjoys the porch" in next_context
    assert "Last recorded mood: good" in next_context
    assert "felt dizzy" in next_context


def test_next_call_context_includes_adherence_streak(db):
    call = _call(db, "CA_STREAK")
    med = Medication(name="Sinemet", dosage="25/100", schedule_times='["09:00"]')
    db.add(med)
    db.commit()
    db.add(DoseLog(call_log_id=call.id, medication_id=med.id, scheduled_time="09:00", confirmed=True))
    db.commit()

    assert "Medication adherence streak: 1" in get_context_for_next_call(db)


def test_memory_router_endpoints(db):
    client = TestClient(app)
    response = client.post("/memory/", json={"content": "likes tea", "memory_type": "preference"})
    assert response.status_code == 200
    assert response.json()["source"] == "manual"

    response = client.get("/memory/")
    assert response.status_code == 200
    assert response.json()[0]["content"] == "likes tea"

    response = client.get("/memory/next-call-context")
    assert response.status_code == 200
    assert "context" in response.json()


def test_build_system_prompt_with_context(db, monkeypatch):
    call = _call(db, "CA_PROMPT")
    save_memory(db, "likes morning walks", "preference", call.id)

    prompt = build_system_prompt_with_context(db, "check_in", patient_name="Dad")

    assert "Dad" in prompt
    assert "likes morning walks" in prompt
    assert "Medication adherence" in prompt
