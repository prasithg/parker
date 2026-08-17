"""Text-loop routing tests: the transcript-capture seam over the tool layer."""

import json

import pytest

from app.brain.adapter import BrainContext, BrainReply, ProposedAction
from app.conversation.textloop import TextSession, UtteranceContext
from app.db.models import CallLog, CapturedIntent, OutboxMessage
from app.parker.pipeline import (
    confirm_staged_action,
    execute_staged_action,
    resolve_captured_intents,
    stage_resolved_actions,
)


def _session(db, **kwargs):
    call = CallLog(call_sid="CA_TEXTLOOP", call_type="text_loop")
    db.add(call)
    db.commit()
    db.refresh(call)
    return TextSession(db, call.id, **kwargs)


class _ProposingAnswerBrain:
    def __init__(self):
        self.utterances: list[str] = []

    def respond(self, history, utterance, context):
        self.utterances.append(utterance)
        return BrainReply(
            speech="I would answer the repaired query here.",
            proposed_actions=(
                ProposedAction(
                    action_type="reminder",
                    label="set a reminder about the weather",
                    subject="weather",
                    intent_text="remind me about the weather",
                ),
            ),
        )


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


def test_ticket_requests_separate_lookup_from_purchase_without_capture(db):
    session = _session(db)

    lookup = session.handle("Find me ticket options for the concert Saturday night")
    purchase = session.handle(
        "I want tickets to be talked to the consequences of the night.",
        alternates=["I want the tickets to be sought to the concert outside of the night."],
    )
    get_tickets = session.handle("Get me tickets for the concert")

    assert lookup["kind"] == "answer"
    assert lookup["action_type"] == "item_search"
    assert lookup["purchase_permitted"] is False
    assert "buy" not in lookup["speech"].lower()
    for held in (purchase, get_tickets):
        assert held["kind"] == "needs_human_approval"
        assert held["action_type"] == "purchase"
        assert held["purchase_permitted"] is False
        assert "family" in held["speech"].lower()
    assert db.query(CapturedIntent).count() == 0


def test_ticket_boundary_preserves_nonpurchase_reminders_and_messages(db):
    session = _session(db)

    reminder = session.handle("Remind me to check the ticket prices tomorrow")
    message = session.handle("Tell Sarah the tickets are available now")

    assert reminder["kind"] == "captured"
    assert message["kind"] == "captured"
    saved = db.query(CapturedIntent).order_by(CapturedIntent.id).all()
    assert [row.requested_action for row in saved] == ["remind", "message"]


def test_negated_ticket_intents_do_not_become_purchase_holds(db):
    session = _session(db)

    lookup = session.handle("Don't buy tickets, just look up concert times for Saturday night.")
    abandoned = session.handle("I don't want tickets anymore. Cancel that.")

    assert lookup["kind"] == "answer"
    assert lookup["action_type"] == "item_search"
    assert lookup["purchase_permitted"] is False
    assert abandoned["kind"] == "noop"
    assert "won't" in abandoned["speech"].lower()
    assert db.query(CapturedIntent).count() == 0


def test_negated_ticket_clause_does_not_hide_a_later_positive_purchase_request(db):
    session = _session(db)

    response = session.handle("Don't buy these tickets; buy the Sunday tickets instead.")

    assert response["kind"] == "needs_human_approval"
    assert response["action_type"] == "purchase"
    assert response["purchase_permitted"] is False
    assert db.query(CapturedIntent).count() == 0


def test_compound_family_message_can_quote_negated_ticket_intent(db):
    session = _session(db)

    want_message = session.handle("Tell Sarah I don't want tickets anymore.")
    buy_message = session.handle("Tell Sarah don't buy tickets for me.")

    assert want_message["kind"] == "captured"
    assert buy_message["kind"] == "captured"
    saved = db.query(CapturedIntent).order_by(CapturedIntent.id).all()
    assert [row.requested_action for row in saved] == ["message", "message"]
    assert [row.recipient for row in saved] == ["Sarah", "Sarah"]
    assert [row.intent_text for row in saved] == [
        "I don't want tickets anymore.",
        "don't buy tickets for me.",
    ]


def test_ticket_phrase_matching_uses_word_boundaries(db):
    session = _session(db)

    facebook = session.handle("Show the tickets page on Facebook")
    costume = session.handle("Discuss costume party tickets")

    assert facebook["kind"] == "answer"
    assert facebook["action_type"] == "item_search"
    assert costume["kind"] == "choices"
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


def test_non_addressed_ambient_audio_is_silent_noop_without_generic_choices(db):
    session = _session(db)
    context = UtteranceContext(addressed_to_parker=False, source="ambient_audio_window")

    for utterance in (
        "PBA, I am going to work today.",
        "I think it's going well, at the moment.",
        "I will require full cover jacket if it is too stormy in evening.",
    ):
        response = session.handle(utterance, context=context)
        assert response["kind"] == "ambient_noop"
        assert response["speech"] == ""
        assert response["addressed_to_parker"] is False
        assert "choices" not in response

    assert db.query(CapturedIntent).count() == 0


def test_non_addressed_audio_does_not_clear_pending_repair_choices(db):
    session = _session(db)
    offered = session.handle("The thing... with the... you know...")

    ambient = session.handle(
        "PBA, I am going to work today.",
        context=UtteranceContext(addressed_to_parker=False, source="ambient_audio_window"),
    )
    selected = session.handle("3")

    assert offered["kind"] == "choices"
    assert ambient["kind"] == "ambient_noop"
    assert selected["kind"] == "retry"
    assert db.query(CapturedIntent).count() == 0


def test_wake_confirmed_conversation_and_answer_cues_do_not_offer_generic_choices(db):
    session = _session(db)
    context = UtteranceContext(addressed_to_parker=True, source="wake_confirmed")

    for utterance in (
        "Let's have a chat.",
        "Tell me more about my events.",
        "Describe the new football game rules.",
        "Please give me information on Martin Jackson.",
        "Find me info on cars.",
    ):
        response = session.handle(utterance, context=context)
        assert response["kind"] == "answer"
        assert "1)" not in response["speech"]

    assert db.query(CapturedIntent).count() == 0


def test_wake_confirmed_weather_nbest_disagreement_repairs_before_read_only_answer(db):
    brain = _ProposingAnswerBrain()
    session = _session(db, brain=brain, brain_context=BrainContext())
    context = UtteranceContext(addressed_to_parker=True, source="wake_confirmed")

    offered = session.handle(
        "What kind of weather they have been orange? TX right now.",
        alternates=["What kind of web are they having orange TX right now?"],
        context=context,
    )

    assert offered["kind"] == "choices"
    assert [choice["label"] for choice in offered["choices"]] == [
        "look up the current weather in Orange, Texas",
        "answer a general question about Orange, Texas",
        "none of these",
    ]
    assert all(choice["action_type"] is None for choice in offered["choices"])
    assert db.query(CapturedIntent).count() == 0

    answered = session.handle("1")

    assert answered["kind"] == "answer"
    assert answered["speech"].startswith("I would answer the repaired query here.")
    assert answered["research_handoff_offered"] is True
    assert [choice["label"] for choice in answered["research_handoff_choices"]] == [
        "leave a local research card for family",
        "do not create a card",
    ]
    assert "choices" not in answered
    assert answered["resolved_query"] == "What is the current weather in Orange, Texas?"
    assert answered["informational_repair"] is True
    assert brain.utterances == ["What is the current weather in Orange, Texas?"]
    assert db.query(CapturedIntent).count() == 0


def test_wake_confirmed_person_entity_nbest_disagreement_repairs_before_read_only_answer(db):
    brain = _ProposingAnswerBrain()
    session = _session(db, brain=brain, brain_context=BrainContext())
    context = UtteranceContext(addressed_to_parker=True, source="wake_confirmed")

    offered = session.handle(
        "Please give me information on Martin Jackson.",
        alternates=["Please give me information on Michael Jackson."],
        context=context,
    )

    assert offered["kind"] == "choices"
    assert [choice["label"] for choice in offered["choices"]] == [
        "information about Martin Jackson",
        "information about Michael Jackson",
        "none of these",
    ]
    assert all(choice["action_type"] is None for choice in offered["choices"])
    assert db.query(CapturedIntent).count() == 0

    answered = session.handle("2")

    assert answered["kind"] == "answer"
    assert answered["speech"].startswith("I would answer the repaired query here.")
    assert answered["research_handoff_offered"] is True
    assert [choice["label"] for choice in answered["research_handoff_choices"]] == [
        "leave a local research card for family",
        "do not create a card",
    ]
    assert answered["resolved_query"] == "Tell me about Michael Jackson."
    assert answered["informational_repair"] is True
    assert answered["informational_repair_family"] == "person_entity"
    assert brain.utterances == ["Tell me about Michael Jackson."]
    assert db.query(CapturedIntent).count() == 0


def test_person_entity_informational_repair_respects_ambient_and_verbal_dismissal(db):
    primary = "Please give me information on Martin Jackson."
    alternate = "Please give me information on Michael Jackson."

    session = _session(db)
    ambient = session.handle(
        primary,
        alternates=[alternate],
        context=UtteranceContext(addressed_to_parker=False, source="ambient_audio_window"),
    )
    offered = session.handle(
        primary,
        alternates=[alternate],
        context=UtteranceContext(addressed_to_parker=True, source="wake_confirmed"),
    )
    dismissed = session.handle("never mind")

    assert ambient["kind"] == "ambient_noop"
    assert ambient["speech"] == ""
    assert offered["kind"] == "choices"
    assert dismissed["kind"] == "retry"
    assert db.query(CapturedIntent).count() == 0


def test_person_entity_informational_repair_requires_same_surname(db):
    session = _session(db)
    context = UtteranceContext(addressed_to_parker=True, source="wake_confirmed")

    response = session.handle(
        "Please give me information on Martin Jackson.",
        alternates=["Please give me information on Michael Jordan."],
        context=context,
    )

    assert response["kind"] == "answer"
    assert "choices" not in response
    assert db.query(CapturedIntent).count() == 0


def test_weather_informational_repair_respects_ambient_and_none_of_these(db):
    primary = "What kind of weather they have been orange? TX right now."
    alternate = "What kind of web are they having orange TX right now?"

    ambient_session = _session(db)
    ambient = ambient_session.handle(
        primary,
        alternates=[alternate],
        context=UtteranceContext(addressed_to_parker=False, source="ambient_audio_window"),
    )
    assert ambient["kind"] == "ambient_noop"
    assert ambient["speech"] == ""

    directed_session = ambient_session
    offered = directed_session.handle(
        primary,
        alternates=[alternate],
        context=UtteranceContext(addressed_to_parker=True, source="wake_confirmed"),
    )
    dismissed = directed_session.handle("3")

    assert offered["kind"] == "choices"
    assert dismissed["kind"] == "retry"
    assert db.query(CapturedIntent).count() == 0


def test_question_shaped_youtube_asr_gets_media_repair_choices(db):
    session = _session(db)

    response = session.handle("Why you YouTube stretching video?")

    assert response["kind"] == "choices"
    assert "YouTube stretching video" in response["speech"]
    assert response["choices"][0]["action_type"] == "media_playlist"
    assert db.query(CapturedIntent).count() == 0


def test_music_audio_phrase_gets_media_repair_choices(db):
    session = _session(db)

    response = session.handle("Play my rock playlist.")

    assert response["kind"] == "choices"
    assert "rock playlist" in response["speech"]
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

    for utterance in (
        "Turn the volume down.",
        "Turn the bedroom lights off.",
        "Increase the temperature in the washroom.",
        "Set the language.",
        "OK now switch the main language to German.",
        "Close the app.",
    ):
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


@pytest.mark.parametrize("mutated_field", ["recipient", "action_type", "subject"])
def test_confirmation_restatement_mismatch_repairs_without_execution(db, mutated_field):
    """A spoken yes is bound to the recipient/action/subject Parker read back."""

    session = _session(db)
    session.handle("Send Sarah a message that dinner Sunday sounds lovely.")
    resolve_captured_intents(db)
    action = stage_resolved_actions(db)[0]
    offer = session.offer_pending_confirmation()
    assert offer is not None
    assert "Sarah" in offer["speech"]

    payload = json.loads(action.action_payload)
    if mutated_field == "recipient":
        payload["recipient"] = "Michael"
        action.action_payload = json.dumps(payload)
    elif mutated_field == "action_type":
        action.action_type = "reminder"
    else:
        payload["subject"] = "message Michael"
        action.action_payload = json.dumps(payload)
    db.commit()

    response = session.handle("Yes.")

    db.refresh(action)
    assert response["kind"] == "confirmation_mismatch"
    assert response["repair_required"] is True
    assert response["staged_action_id"] == action.id
    assert action.status == "cancelled"
    assert action.cancelled_by == "confirmation_contract_mismatch"
    assert action.confirmed_at is None
    assert db.query(OutboxMessage).count() == 0


def test_confirmation_contract_rechecked_after_confirmation_commit(db, monkeypatch):
    """A mutation exposed by confirmation refresh cannot cross into execution."""

    from app.parker import pipeline

    session = _session(db)
    session.handle("Send Sarah a message that dinner Sunday sounds lovely.")
    resolve_captured_intents(db)
    action = stage_resolved_actions(db)[0]
    assert session.offer_pending_confirmation() is not None
    real_confirm = pipeline.confirm_staged_action

    def confirm_then_mutate(*args, **kwargs):
        confirmed = real_confirm(*args, **kwargs)
        payload = json.loads(confirmed.action_payload)
        payload["recipient"] = "Michael"
        confirmed.action_payload = json.dumps(payload)
        db.commit()
        db.refresh(confirmed)
        return confirmed

    monkeypatch.setattr(pipeline, "confirm_staged_action", confirm_then_mutate)

    response = session.handle("Yes.")

    db.refresh(action)
    assert response["kind"] == "confirmation_mismatch"
    assert response["repair_required"] is True
    assert action.status == "cancelled"
    assert action.cancelled_by == "confirmation_contract_mismatch"
    assert db.query(OutboxMessage).count() == 0


def test_none_of_these_interrupts_pending_confirmation_and_cancels_stale_action(db):
    """A repair rejection during readback cannot leave a stale yes target alive."""

    session = _session(db)
    session.handle("Send Sarah a message that dinner Sunday sounds lovely.")
    resolve_captured_intents(db)
    action = stage_resolved_actions(db)[0]
    offer = session.offer_pending_confirmation()
    assert offer is not None

    response = session.handle("None... none of these.")
    stale_execute = execute_staged_action(db, action.id)
    follow_up = session.handle("Yes.")

    db.refresh(action)
    assert response["kind"] == "confirmation_repair"
    assert response["repair_required"] is True
    assert response["cancelled_staged_action_id"] == action.id
    assert "again" in response["speech"].lower()
    assert action.status == stale_execute.status == "cancelled"
    assert action.cancelled_by == "patient_confirmation_rejected"
    assert action.confirmed_at is None
    assert follow_up["kind"] == "noop"
    assert db.query(OutboxMessage).count() == 0


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


def test_changed_mind_to_ticket_purchase_holds_after_cancelling_draft(db):
    session = _session(db)
    session.handle("Remind me to start stretches now.")
    resolve_captured_intents(db)
    staged = stage_resolved_actions(db)

    response = session.handle("Wait, no, book concert tickets instead.")

    db.refresh(staged[0])
    assert staged[0].status == "cancelled"
    assert response["kind"] == "needs_human_approval"
    assert response["action_type"] == "purchase"
    assert response["purchase_permitted"] is False
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
