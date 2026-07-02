"""Clipped-start fragment reconstruction as repair choices.

ASR loses the first word(s) of effortful speech constantly. Fragments
with one high-precision reading ("me to water the plants..." = a clipped
reminder; "a speech exercise for..." = a clipped start-exercise) become
repair choices — offered, never auto-captured, safety-screened.
"""

from app.conversation.textloop import TextSession, fragment_candidates
from app.db.models import CallLog, CapturedIntent


def _session(db):
    call = CallLog(call_sid="CA_FRAGMENT", call_type="text_loop")
    db.add(call)
    db.commit()
    db.refresh(call)
    return TextSession(db, call.id)


def test_clipped_reminder_fragment_reconstructs():
    results = fragment_candidates("me to water the tomato plants this evening")
    assert len(results) == 1
    assert results[0]["action_type"] == "reminder"
    assert results[0]["subject"] == "water the tomato plants this evening"

    bare = fragment_candidates("to water the tomato plants this evening")
    assert bare and bare[0]["action_type"] == "reminder"


def test_clipped_exercise_fragment_reconstructs():
    results = fragment_candidates("a speech exercise for the morning cards")
    assert len(results) == 1
    assert results[0]["action_type"] == "exercise_start"
    assert results[0]["subject"] == "speech exercise: the morning cards"


def test_non_fragments_and_safety_trips_return_nothing():
    assert fragment_candidates("water the plants") == []
    assert fragment_candidates("to be honest") == []  # single-word remainder
    assert fragment_candidates("me to take half my pills") == []
    assert fragment_candidates("What's the weather this weekend?") == []


def test_clipped_reminder_is_offered_then_captured(db):
    session = _session(db)

    response = session.handle("me to water the tomato plants this evening")

    assert response["kind"] == "choices"
    first = response["choices"][0]
    assert first["action_type"] == "reminder"
    assert db.query(CapturedIntent).count() == 0  # offered, not auto-captured

    session.handle(str(first["position"]))
    captured = db.query(CapturedIntent).one()
    assert captured.requested_action == "reminder"
    assert captured.subject == "water the tomato plants this evening"
