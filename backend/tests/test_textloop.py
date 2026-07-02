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


def test_financial_account_requests_are_refused_without_capture(db):
    session = _session(db)

    for utterance in (
        "Can you tell me my current account balance please?",
        "I need help setting up a joint account.",
        "Please, I need help setting up a joining town.",
        "How do I turn it join the count?",
    ):
        response = session.handle(utterance)
        assert response["kind"] == "refused"
        assert "bank" in response["speech"].lower()

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


def test_no_context_cancel_message_audio_phrase_noops_without_generic_choices(db):
    session = _session(db)

    for utterance in ("Cancel that message.", "that message."):
        response = session.handle(utterance)
        assert response["kind"] == "noop"
        assert "message" in response["speech"].lower()
        assert "1)" not in response["speech"]

    assert db.query(CapturedIntent).count() == 0


def test_standalone_control_words_do_not_offer_generic_actions_without_context(db):
    session = _session(db)

    for utterance in ("No.", "Go.", "Stop.", "Down.", "On.", "Off.", "of", "Zero.", "Oh no."):
        response = session.handle(utterance)
        assert response["kind"] == "noop"
        assert "1)" not in response["speech"]

    assert db.query(CapturedIntent).count() == 0


def test_control_negation_audio_phrase_noops_without_context(db):
    session = _session(db)

    for utterance in ("No, don't go yet.", "Don't go yet."):
        response = session.handle(utterance)
        assert response["kind"] == "noop"
        assert "not to go" in response["speech"].lower()
        assert "1)" not in response["speech"]

    assert db.query(CapturedIntent).count() == 0


def test_question_shaped_youtube_asr_gets_media_repair_choices(db):
    session = _session(db)

    response = session.handle("Why you YouTube stretching video?")

    assert response["kind"] == "choices"
    assert "YouTube stretching video" in response["speech"]
    assert response["choices"][0]["action_type"] == "media_playlist"
    assert db.query(CapturedIntent).count() == 0


def test_repetitive_no_transcript_asr_hallucination_noops(db):
    session = _session(db)
    utterance = "I'll be happy, " * 12

    response = session.handle(utterance)

    assert response["kind"] == "noop"
    assert "repeated audio" in response["speech"]
    assert "1)" not in response["speech"]
    assert db.query(CapturedIntent).count() == 0


def test_medical_asr_dictation_refuses_without_generic_repair_or_local_action(db):
    session = _session(db)

    examples = (
        "2 times in a day, please have an antibiotic named azithromycin.",
        "Hello, the patient has fever and I am suspecting Dengue and the patient should take Dolo 650.",
        "For the medicine, take thyroxene, also take doulo 650, avoid eating outside food.",
    )
    for utterance in examples:
        response = session.handle(utterance)
        assert response["kind"] == "refused"
        assert "medical" in response["speech"].lower()
        assert "1)" not in response["speech"]

    assert db.query(CapturedIntent).count() == 0


def test_standalone_stop_or_cancel_cancels_active_local_draft(db):
    for idx, utterance in enumerate(("Stop.", "Cancel."), start=1):
        call = CallLog(call_sid=f"CA_TEXTLOOP_CANCEL_{idx}", call_type="text_loop")
        db.add(call)
        db.commit()
        db.refresh(call)
        session = TextSession(db, call.id)
        session.handle("Remind me to start stretches now.")
        resolve_captured_intents(db)
        staged = stage_resolved_actions(db)

        response = session.handle(utterance)

        db.refresh(staged[0])
        assert response["kind"] == "cancelled"
        assert staged[0].status == "cancelled"
        assert staged[0].cancelled_by == "patient"


def test_device_controls_require_context_instead_of_generic_repair(db):
    session = _session(db)

    for utterance in ("Turn the volume down.", "Turn the bedroom lights off.", "Increase the temperature in the washroom."):
        response = session.handle(utterance)
        assert response["kind"] == "context_required"
        assert "approved" in response["speech"]
        assert "1)" not in response["speech"]

    assert db.query(CapturedIntent).count() == 0


def test_pending_repair_selection_still_wins_over_no_context_control_guard(db):
    session = _session(db)
    offered = session.handle("The thing... with the... you know...")

    response = session.handle("3")

    assert offered["kind"] == "choices"
    assert response["kind"] == "retry"
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


def test_stop_revision_to_medication_change_refuses_after_cancelling_draft(db):
    session = _session(db)
    session.handle("Remind me to start stretches now.")
    resolve_captured_intents(db)
    staged = stage_resolved_actions(db)

    response = session.handle("Stop taking my pills instead.")

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


def test_counting_sequences_noop_without_capture_or_choices(db):
    # Speech-therapy exercises are counting, and ASR renders spoken numbers
    # as digits (web-private validation lane). Counting is ambient exercise
    # audio — never a command, never worth generic repair choices.
    session = _session(db)

    for utterance in ("One, two, three, four.", "81, 82, 83, 84, 85, 86, 87, 88, 89, 90."):
        response = session.handle(utterance)
        assert response["kind"] == "noop"
        assert "counting" in response["speech"].lower()

    assert db.query(CapturedIntent).count() == 0


def test_counting_sequence_dismisses_pending_choices_so_digits_cannot_select(db):
    # The near-miss from the validation lane: ambient speech draws generic
    # choices, then exercise counting continues. A counting line must set
    # the choices aside so a later digit-rendered fragment cannot select
    # an intent nobody asked for.
    session = _session(db)
    offered = session.handle("The thing... with the... you know...")
    assert offered["kind"] == "choices"

    counting = session.handle("1, 2, 3, 4.")
    assert counting["kind"] == "noop"

    follow_up = session.handle("2")
    assert follow_up["kind"] != "captured"
    assert db.query(CapturedIntent).count() == 0


def test_new_command_escapes_pending_choices_instead_of_being_swallowed(db):
    # Before this seam, one nuisance choice offer put the session into a
    # "Just say the number" loop that ate the user's next real command —
    # the worst failure shape for a speaker whose retries are effortful.
    session = _session(db)
    offered = session.handle("The thing... with the... you know...")
    assert offered["kind"] == "choices"

    response = session.handle("Remind me to take my walk this afternoon")

    assert response["kind"] == "captured"
    saved = db.get(CapturedIntent, response["captured_intent_id"])
    assert saved.requested_action == "remind"
    assert "walk" in saved.subject


def test_question_escapes_pending_choices(db):
    session = _session(db)
    session.handle("The thing... with the... you know...")

    response = session.handle("When is my next appointment")

    assert response["kind"] == "answer"
    assert db.query(CapturedIntent).count() == 0


def test_dismissal_words_reject_pending_choices_without_capture(db):
    session = _session(db)
    session.handle("The thing... with the... you know...")

    response = session.handle("never mind")

    assert response["kind"] == "retry"
    assert db.query(CapturedIntent).count() == 0
    # Session is reset: the next utterance routes normally.
    follow_up = session.handle("Remind me to stretch")
    assert follow_up["kind"] == "captured"


def test_garbled_text_while_choices_pending_still_reprompts(db):
    # Escape is only for clearly-new commands/questions; unclear text is
    # still most likely a garbled selection attempt.
    session = _session(db)
    session.handle("The thing... with the... you know...")

    response = session.handle("the blue one maybe")

    assert response["kind"] == "choices"
    assert "number" in response["speech"].lower()
    assert db.query(CapturedIntent).count() == 0


def test_questions_get_answer_stub_without_capture(db):
    session = _session(db)

    response = session.handle("What's the weather this weekend?")

    assert response["kind"] == "answer"
    assert db.query(CapturedIntent).count() == 0
