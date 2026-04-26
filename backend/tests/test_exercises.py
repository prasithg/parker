"""Tests for cognitive exercise library and session tracking."""

import pytest

from app.conversation.tools import execute_tool
from app.db.models import CallLog
from app.exercises.library import all_exercises, select_exercise
from app.exercises.session import (
    complete_exercise,
    get_exercise_history,
    get_recommended_exercise,
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
