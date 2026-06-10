"""Text-loop routing tests: the transcript-capture seam over the tool layer."""

from app.conversation.textloop import TextSession
from app.db.models import CallLog, CapturedIntent


def _session(db):
    call = CallLog(call_sid="CA_TEXTLOOP", call_type="text_loop")
    db.add(call)
    db.commit()
    db.refresh(call)
    return TextSession(db, call.id)


def test_medication_change_is_refused_with_no_side_effects(db):
    session = _session(db)

    response = session.handle("My pills make me dizzy. Should I take half tomorrow?")

    assert response["kind"] == "refused"
    assert "doctor" in response["speech"]
    assert response["flag_for_family"] is True
    assert db.query(CapturedIntent).count() == 0


def test_purchase_requests_route_to_human_approval(db):
    session = _session(db)

    response = session.handle("Order that walker with the card on file")

    assert response["kind"] == "needs_human_approval"
    assert db.query(CapturedIntent).count() == 0


def test_reminder_utterance_captures_pending_intent(db):
    session = _session(db)

    response = session.handle("Remind me to water the plants.")

    assert response["kind"] == "captured"
    saved = db.get(CapturedIntent, response["captured_intent_id"])
    assert saved.requested_action == "remind"
    assert saved.subject == "water the plants"
    assert saved.status == "pending"


def test_message_utterance_captures_recipient(db):
    session = _session(db)

    response = session.handle("Tell Sarah dinner on Sunday would be lovely")

    assert response["kind"] == "captured"
    saved = db.get(CapturedIntent, response["captured_intent_id"])
    assert saved.requested_action == "message"
    assert saved.recipient == "Sarah"
    assert saved.intent_text == "dinner on Sunday would be lovely"


def test_ambiguous_utterance_offers_choices_then_selection_captures(db):
    session = _session(db)

    offered = session.handle("Call... the... you know... the one with the garden...")

    assert offered["kind"] == "choices"
    assert "1)" in offered["speech"]
    assert db.query(CapturedIntent).count() == 0

    selected = session.handle("1")

    assert selected["kind"] == "captured"
    saved = db.get(CapturedIntent, selected["captured_intent_id"])
    assert saved.requested_action == "reminder"
    assert "garden" in saved.intent_text
    assert db.query(CapturedIntent).count() == 1


def test_none_of_these_selection_captures_nothing(db):
    session = _session(db)
    session.handle("The thing... with the... you know...")

    response = session.handle("3")

    assert response["kind"] == "retry"
    assert db.query(CapturedIntent).count() == 0
    # Session is reset: the next utterance routes normally.
    follow_up = session.handle("Remind me to stretch")
    assert follow_up["kind"] == "captured"


def test_invalid_selection_reprompts_without_capturing(db):
    session = _session(db)
    session.handle("The thing... with the... you know...")

    response = session.handle("9")

    assert response["kind"] == "choices"
    assert db.query(CapturedIntent).count() == 0


def test_questions_get_answer_stub_without_capture(db):
    session = _session(db)

    response = session.handle("What's the weather this weekend?")

    assert response["kind"] == "answer"
    assert db.query(CapturedIntent).count() == 0
