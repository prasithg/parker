"""EXP-001 slice 3: one recorded outcome per directed interaction.

The binding contracts pinned here:

- every directed utterance episode yields exactly one InteractionOutcome
  row with one of the seven taxonomy labels;
- replies inside a Parker-question context (numbered selections, yes/no
  confirmation replies) are turns of the open interaction, never new rows;
- a repair episode's row is provisional ``repair_abandoned`` while open,
  so a walk-away needs no sweeper to be counted correctly;
- a confirmation-time "none of these" proves the capture was wrong and
  relabels the source interaction ``wrong_action``;
- the normalized-intent signature is consent-gated (same flag as repair
  events), and recording failures never break the conversation.
"""

from app.config import settings
from app.conversation.outcomes import InteractionOutcome, OutcomeRecorder, normalize_intent
from app.conversation.textloop import TextSession, UtteranceContext
from app.db.models import CallLog
from app.parker.pipeline import resolve_captured_intents, stage_resolved_actions


def _session(db):
    call = CallLog(call_sid="CA_OUTCOMES", call_type="text_loop")
    db.add(call)
    db.commit()
    db.refresh(call)
    return TextSession(db, call.id)


def _rows(db):
    return db.query(InteractionOutcome).order_by(InteractionOutcome.id).all()


def test_direct_capture_records_understood_first_try(db):
    session = _session(db)

    response = session.handle("Remind me to water the tomato plants this evening")

    assert response["kind"] == "captured"
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0].outcome == "understood_first_try"
    assert rows[0].closed_at is not None
    assert rows[0].captured_intent_id == response["captured_intent_id"]
    assert rows[0].repair_turns == 0


def test_answer_records_understood_first_try(db):
    session = _session(db)

    response = session.handle("What's the weather looking like this weekend?")

    assert response["kind"] == "answer"
    rows = _rows(db)
    assert [row.outcome for row in rows] == ["understood_first_try"]


def test_medication_refusal_records_refused_safety(db):
    session = _session(db)

    response = session.handle("Should I take half my pills tomorrow?")

    assert response["kind"] == "refused"
    rows = _rows(db)
    assert [row.outcome for row in rows] == ["refused_safety"]
    assert rows[0].closed_at is not None


def test_purchase_routing_records_refused_safety(db):
    session = _session(db)

    response = session.handle("Order that walker with the card on file")

    assert response["kind"] == "needs_human_approval"
    assert [row.outcome for row in _rows(db)] == ["refused_safety"]


def test_control_word_records_no_response(db):
    session = _session(db)

    response = session.handle("Stop.")

    assert response["kind"] == "noop"
    assert [row.outcome for row in _rows(db)] == ["no_response"]


def test_counting_practice_records_no_response(db):
    session = _session(db)

    response = session.handle("81, 82, 83, 84")

    assert response["kind"] == "noop"
    assert [row.outcome for row in _rows(db)] == ["no_response"]


def test_ambient_speech_records_ambient_noop(db):
    session = _session(db)

    response = session.handle(
        "and then the detective said something about the garden",
        context=UtteranceContext(addressed_to_parker=False, source="wake_gate"),
    )

    assert response["kind"] == "ambient_noop"
    assert [row.outcome for row in _rows(db)] == ["ambient_noop"]


def test_repair_episode_is_provisionally_abandoned_while_open(db):
    session = _session(db)

    response = session.handle("Call... the... you know... the one with the garden...")

    assert response["kind"] == "choices"
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0].outcome == "repair_abandoned"  # walk-away default
    assert rows[0].closed_at is None  # still open


def test_numbered_selection_closes_episode_as_repaired_success(db):
    session = _session(db)
    offer = session.handle("Call... the... you know... the one with the garden...")

    selection = session.handle("1")

    assert offer["kind"] == "choices"
    assert selection["kind"] == "captured"
    rows = _rows(db)
    assert len(rows) == 1  # the selection is a turn, not a new interaction
    assert rows[0].outcome == "repaired_success"
    assert rows[0].closed_at is not None
    assert rows[0].repair_turns == 1
    assert rows[0].captured_intent_id == selection["captured_intent_id"]


def test_none_of_these_closes_episode_as_repair_abandoned(db):
    session = _session(db)
    offer = session.handle("Call... the... you know... the one with the garden...")
    none_choice = next(c for c in offer["choices"] if c["action_type"] is None)

    rejection = session.handle(str(none_choice["position"]))

    assert rejection["kind"] == "retry"
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0].outcome == "repair_abandoned"
    assert rows[0].closed_at is not None


def test_restatement_after_choices_closes_episode_as_repaired_success(db):
    session = _session(db)
    offer = session.handle("Call... the... you know... the one with the garden...")

    restatement = session.handle("Remind me to call the neighbour about the garden")

    assert offer["kind"] == "choices"
    assert restatement["kind"] == "captured"
    rows = _rows(db)
    # One interaction: the repair question was answered by restating.
    assert len(rows) == 1
    assert rows[0].outcome == "repaired_success"
    assert rows[0].repair_turns == 1
    assert rows[0].captured_intent_id == restatement["captured_intent_id"]


def test_control_giveup_closes_episode_as_repair_abandoned(db):
    session = _session(db)
    session.handle("Call... the... you know... the one with the garden...")

    giveup = session.handle("Stop.")

    # "stop" while choices pend is a dismissal — it resolves as none-of-these.
    assert giveup["kind"] == "retry"
    rows = _rows(db)
    assert len(rows) == 1  # the give-up is the episode's end, not a new interaction
    assert rows[0].outcome == "repair_abandoned"
    assert rows[0].closed_at is not None


def test_clarify_then_restatement_closes_as_repaired_success(db):
    session = _session(db)
    clarify = session.handle("Tell him the show is starting")

    restatement = session.handle("Tell Sarah the show is starting")

    assert clarify["kind"] == "clarify"
    assert restatement["kind"] == "captured"
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0].outcome == "repaired_success"
    assert rows[0].repair_turns == 1


def test_garbled_selection_reprompt_keeps_episode_open(db):
    session = _session(db)
    session.handle("Call... the... you know... the one with the garden...")

    reprompt = session.handle("mumble grumble")

    assert reprompt["kind"] == "choices"
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0].closed_at is None
    assert rows[0].repair_turns == 1  # the garbled attempt still cost a turn


def test_ambient_speech_preserves_an_open_episode(db):
    session = _session(db)
    session.handle("Call... the... you know... the one with the garden...")

    session.handle(
        "the weather tomorrow will be cloudy with showers",
        context=UtteranceContext(addressed_to_parker=False, source="wake_gate"),
    )
    selection = session.handle("1")

    assert selection["kind"] == "captured"
    rows = _rows(db)
    assert [row.outcome for row in rows] == ["repaired_success", "ambient_noop"]
    assert rows[0].repair_turns == 1


def test_confirmation_rejection_relabels_source_interaction_wrong_action(db):
    session = _session(db)
    captured = session.handle("Remind me to water the tomato plants")
    assert captured["kind"] == "captured"
    resolve_captured_intents(db)
    stage_resolved_actions(db)
    offer = session.offer_pending_confirmation()
    assert offer is not None and offer["kind"] == "confirm_offer"

    rejection = session.handle("None of these")

    assert rejection["kind"] == "confirmation_repair"
    rows = _rows(db)
    assert len(rows) == 1  # the rejection turn adds no new interaction
    assert rows[0].outcome == "wrong_action"
    assert rows[0].closing_kind == "confirmation_repair"


def test_confirmation_yes_keeps_original_outcome(db):
    session = _session(db)
    session.handle("Remind me to water the tomato plants")
    resolve_captured_intents(db)
    stage_resolved_actions(db)
    offer = session.offer_pending_confirmation()
    assert offer is not None

    executed = session.handle("Yes, go ahead")

    assert executed["kind"] == "executed"
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0].outcome == "understood_first_try"


def test_confirmation_no_keeps_original_outcome(db):
    session = _session(db)
    session.handle("Remind me to water the tomato plants")
    resolve_captured_intents(db)
    stage_resolved_actions(db)
    offer = session.offer_pending_confirmation()
    assert offer is not None

    declined = session.handle("No thanks")

    assert declined["kind"] == "cancelled"
    rows = _rows(db)
    assert len(rows) == 1
    # Declining an offer is a decision, not proof the capture was wrong.
    assert rows[0].outcome == "understood_first_try"


def test_normalized_intent_requires_repair_capture_consent(db):
    assert settings.repair_event_capture_consented is False
    session = _session(db)

    session.handle("Call... the... you know... the one with the garden...")

    rows = _rows(db)
    assert rows[0].normalized_intent is None


def test_normalized_intent_stored_with_consent(db, monkeypatch):
    monkeypatch.setattr(settings, "repair_event_capture_consented", True)
    session = _session(db)

    session.handle("Call... the... you know... the one with the garden...")

    rows = _rows(db)
    assert rows[0].normalized_intent == normalize_intent(
        "Call... the... you know... the one with the garden..."
    )
    assert rows[0].normalized_intent  # non-empty signature


def test_recording_failure_never_breaks_the_conversation(db, monkeypatch):
    session = _session(db)

    def _boom(**_kwargs):
        raise RuntimeError("meter broke")

    monkeypatch.setattr(OutcomeRecorder, "observe", lambda self, **kwargs: _boom(**kwargs))

    response = session.handle("Remind me to water the tomato plants")

    assert response["kind"] == "captured"  # the conversation is unharmed
    assert _rows(db) == []


def test_normalize_intent_is_stable_and_short():
    left = normalize_intent("Remind me to call the nurse about Tuesday!")
    right = normalize_intent("remind me to call the nurse about tuesday")

    assert left == right
    assert left == "remind call nurse about tuesday"
    assert len(left.split()) <= 8
