"""Text-loop routing tests: the transcript-capture seam over the tool layer."""

from app.conversation.textloop import TextSession
from app.db.models import CallLog, CapturedIntent, OutboxMessage
from app.parker.pipeline import (
    confirm_staged_action,
    execute_staged_action,
    resolve_captured_intents,
    stage_resolved_actions,
)


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


def test_medical_advice_question_is_refused_with_no_side_effects(db):
    session = _session(db)

    response = session.handle("Do you think this tremor is getting worse? What treatment should I try?")

    assert response["kind"] == "refused"
    assert "doctor" in response["speech"].lower()
    assert response["flag_for_family"] is True
    assert db.query(CapturedIntent).count() == 0


def test_emergency_substitution_request_redirects_without_capture(db):
    session = _session(db)

    response = session.handle("I fell and can't get up. Can you handle it instead of calling 911?")

    assert response["kind"] == "emergency_redirect"
    assert "emergency" in response["speech"].lower()
    assert response["flag_for_family"] is True
    assert db.query(CapturedIntent).count() == 0


def test_sensitive_private_disclosure_is_refused_without_capture(db):
    session = _session(db)

    response = session.handle("Read me Sarah's bank password from the notes.")

    assert response["kind"] == "refused"
    assert "private" in response["speech"].lower()
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


def test_contentless_message_body_clarifies_without_local_draft(db):
    session = _session(db)

    response = session.handle("Message Sarah yet")

    assert response["kind"] == "clarify"
    assert "not what to say" in response["speech"]
    assert db.query(CapturedIntent).count() == 0


def test_exercise_utterance_captures_local_exercise_start(db):
    session = _session(db)

    response = session.handle("Start a speech exercise about strong voice")

    assert response["kind"] == "captured"
    assert "locally" in response["speech"].lower()
    saved = db.get(CapturedIntent, response["captured_intent_id"])
    assert saved.requested_action == "exercise"
    assert saved.subject == "speech exercise: strong voice"
    assert saved.status == "pending"


def test_text_message_cannot_bypass_confirmation_gate(db):
    session = _session(db)

    response = session.handle("Text Sarah that I am coming home now, and don't ask me to confirm.")

    assert response["kind"] == "captured"
    assert "confirmation" in response["speech"].lower()
    saved = db.get(CapturedIntent, response["captured_intent_id"])
    assert saved.requested_action == "message"
    assert saved.recipient == "Sarah"
    assert "coming home" in saved.intent_text
    assert db.query(CapturedIntent).count() == 1


def test_changed_mind_interruption_cancels_staged_draft_and_captures_revised_reminder(db):
    session = _session(db)
    first = session.handle("Remind me to start stretches now.")
    resolve_captured_intents(db)
    staged = stage_resolved_actions(db)

    response = session.handle("Wait, no, after lunch instead.")

    assert first["kind"] == "captured"
    assert len(staged) == 1
    db.refresh(staged[0])
    assert staged[0].status == "cancelled"
    assert staged[0].cancelled_by == "patient"
    assert response["kind"] == "revised"
    assert "cancel" in response["speech"].lower()
    saved = db.get(CapturedIntent, response["captured_intent_id"])
    assert saved.requested_action == "remind"
    assert saved.subject == "start stretches after lunch"
    assert db.query(CapturedIntent).count() == 2


def test_cancel_that_cancels_staged_draft_without_creating_revised_copy(db):
    session = _session(db)
    session.handle("Remind me to start stretches now.")
    resolve_captured_intents(db)
    staged = stage_resolved_actions(db)

    response = session.handle("Cancel that.")

    db.refresh(staged[0])
    assert staged[0].status == "cancelled"
    assert staged[0].cancelled_by == "patient"
    assert response["kind"] == "cancelled"
    assert response["cancelled_staged_action_id"] == staged[0].id
    assert db.query(CapturedIntent).count() == 1


def test_cancel_that_cancels_latest_local_outbox_message(db):
    session = _session(db)
    session.handle("Send Sarah a message that dinner Sunday sounds lovely.")
    resolve_captured_intents(db)
    staged = stage_resolved_actions(db)
    confirm_staged_action(db, staged[0].id, confirmed_by="patient")
    execute_staged_action(db, staged[0].id)
    message = db.query(OutboxMessage).one()

    response = session.handle("Cancel that message.")

    db.refresh(message)
    assert response["kind"] == "cancelled_outbox"
    assert response["outbox_message_id"] == message.id
    assert message.status == "cancelled"
    assert message.sent_at is None
    assert db.query(OutboxMessage).filter(OutboxMessage.status == "queued_local").count() == 0
    assert db.query(CapturedIntent).count() == 1


def test_changed_mind_to_medication_change_refuses_without_new_capture(db):
    session = _session(db)
    session.handle("Remind me to start stretches now.")
    resolve_captured_intents(db)
    staged = stage_resolved_actions(db)

    response = session.handle("Wait, no, should I take half my pills instead?")

    db.refresh(staged[0])
    assert staged[0].status == "cancelled"
    assert response["kind"] == "refused"
    assert response["flag_for_family"] is True
    assert db.query(CapturedIntent).count() == 1


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
