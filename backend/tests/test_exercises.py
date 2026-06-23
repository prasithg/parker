"""Tests for cognitive exercise library and session tracking."""

import pytest

from app.conversation.tools import execute_tool
from app.db.models import CallLog
from app.exercises.library import all_exercises, select_exercise
from app.exercises.session import (
    LocalExerciseSession,
    cancel_local_exercise_session,
    complete_exercise,
    complete_local_exercise_session,
    get_exercise_history,
    get_recommended_exercise,
    list_recent_local_exercise_sessions,
    start_local_exercise_session,
    start_exercise,
)


def _call(db):
    call = CallLog(call_sid="CA_EX", call_type="evening_chat")
    db.add(call)
    db.commit()
    return call


def test_exercise_library_loads_categories():
    exercises = all_exercises()
    names = {exercise.name for exercise in exercises}
    assert {"word_association", "three_word_recall", "general_trivia", "tell_me_about"} <= names
    assert all(exercise.follow_up_prompt for exercise in exercises)
    assert all(exercise.scoring_hint for exercise in exercises)


def test_select_exercise_by_alias_and_unknown():
    exercise = select_exercise("word_game")
    assert exercise.category == "word_games"
    with pytest.raises(ValueError):
        select_exercise("nope")


def test_start_complete_history_and_recommendation(db):
    call = _call(db)
    result, follow_up = start_exercise(db, call.id, "trivia")

    assert result.id is not None
    assert result.completed is False
    assert result.prompt_given
    assert follow_up

    completed = complete_exercise(db, result.id, "summer", 5)
    assert completed.completed is True
    assert completed.score == 5

    history = get_exercise_history(db)
    assert history[0].id == result.id
    assert get_recommended_exercise(db)


def test_complete_exercise_score_validation(db):
    call = _call(db)
    result, _ = start_exercise(db, call.id, "three_word_recall")
    with pytest.raises(ValueError):
        complete_exercise(db, result.id, "answer", 6)


def test_conversation_tool_start_and_complete(db):
    call = _call(db)
    started = execute_tool(db, call.id, "cognitive_exercise", {"exercise_type": "memory_recall"})
    assert started["status"] == "started"
    assert started["exercise_result_id"]
    assert started["follow_up_prompt"]

    completed = execute_tool(
        db,
        call.id,
        "complete_exercise_result",
        {"exercise_result_id": started["exercise_result_id"], "patient_response": "garden penny radio", "score": 4},
    )
    assert completed["status"] == "completed"
    assert completed["score"] == 4


def test_local_exercise_session_start_complete_cancel_history_and_safe_prompt_cards(db):
    call = _call(db)
    started = start_local_exercise_session(
        db,
        call_log_id=call.id,
        subject="speech exercise: strong voice",
        now="2026-06-22T19:30:00",
    )

    assert started.id is not None
    assert started.call_log_id == call.id
    assert started.status == "started"
    assert started.category == "speech"
    assert started.difficulty == "gentle"
    assert started.started_at.isoformat() == "2026-06-22T19:30:00"
    assert started.completed_at is None
    assert started.cancelled_at is None
    assert "strong voice" in started.prompt_card.lower()

    completed = complete_local_exercise_session(
        db,
        started.id,
        caregiver_note="Dad completed one short round comfortably.",
        now="2026-06-22T19:37:00",
    )
    assert completed is not None
    assert completed.status == "completed"
    assert completed.completed_at.isoformat() == "2026-06-22T19:37:00"
    assert completed.caregiver_note == "Dad completed one short round comfortably."

    to_cancel = start_local_exercise_session(
        db,
        call_log_id=call.id,
        subject="movement exercise: gentle stretch",
        now="2026-06-22T20:00:00",
    )
    cancelled = cancel_local_exercise_session(
        db,
        to_cancel.id,
        caregiver_note="Dad declined after the first prompt.",
        now="2026-06-22T20:01:00",
    )
    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert cancelled.cancelled_at.isoformat() == "2026-06-22T20:01:00"
    assert cancelled.caregiver_note == "Dad declined after the first prompt."

    history = list_recent_local_exercise_sessions(db)
    assert [item.id for item in history] == [to_cancel.id, started.id]
    assert db.query(LocalExerciseSession).count() == 2
    for item in history:
        prompt = item.prompt_card.lower()
        assert "diagnose" not in prompt
        assert "treatment" not in prompt
        assert "therapy" not in prompt
        assert "medication" not in prompt
