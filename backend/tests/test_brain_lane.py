"""The brain-wired informational lane in TextSession.

A fake BrainAdapter (scripted replies, recorded calls) stands in for
Claude — no key, no network. These tests pin the policy-gate invariants:
the brain is the fallthrough, never the front door; refused utterances
never reach it; proposals become confirmation choices, never captures.
"""

from __future__ import annotations

from app.brain.adapter import BrainContext, BrainReply, ProposedAction
from app.brain.guard import MEDICAL_BOUNDARY_REDIRECT
from app.conversation.textloop import ANSWER_STUB_SPEECH, TextSession
from app.db.models import CallLog, CapturedIntent


class FakeBrain:
    """Scripted BrainAdapter that records every respond() call."""

    def __init__(self, replies=None, raises: Exception | None = None):
        self._replies = list(replies or [])
        self._raises = raises
        self.calls: list[dict] = []

    def respond(self, history, utterance, context):
        self.calls.append(
            {"history": list(history), "utterance": utterance, "context": context}
        )
        if self._raises is not None:
            raise self._raises
        if self._replies:
            return self._replies.pop(0)
        return BrainReply(speech="I'm not sure, but I'm here.")


CONTEXT = BrainContext(patient_name="Dad", lexicon_names=("Sarah", "Priya"))


def _session(db, brain=None):
    call = CallLog(call_sid="CA_BRAIN_LANE", call_type="text_loop")
    db.add(call)
    db.commit()
    db.refresh(call)
    return TextSession(db, call.id, brain=brain, brain_context=CONTEXT)


# ---------------------------------------------------------------------------
# Zero-config fallback and the answer lane
# ---------------------------------------------------------------------------


def test_without_brain_questions_keep_the_deterministic_stub(db):
    session = _session(db, brain=None)

    response = session.handle("What's the weather like today?")

    assert response["kind"] == "answer"
    assert response["speech"] == ANSWER_STUB_SPEECH


def test_question_routes_to_brain_and_returns_answer(db):
    brain = FakeBrain([BrainReply(speech="It's Tuesday the ninth, all day.")])
    session = _session(db, brain=brain)

    response = session.handle("What day is it today?")

    assert response["kind"] == "answer"
    assert response["speech"] == "It's Tuesday the ninth, all day."
    assert brain.calls[0]["utterance"] == "What day is it today?"
    assert brain.calls[0]["context"] is CONTEXT
    assert db.query(CapturedIntent).count() == 0


def test_unmatched_statement_falls_through_to_brain(db):
    brain = FakeBrain([BrainReply(speech="That sounds like a lovely afternoon.")])
    session = _session(db, brain=brain)

    response = session.handle("The garden was nice this afternoon")

    assert response["kind"] == "answer"
    assert brain.calls[0]["utterance"] == "The garden was nice this afternoon"


def test_unmatched_statement_without_brain_still_offers_repair_choices(db):
    session = _session(db, brain=None)

    response = session.handle("The garden was nice this afternoon")

    assert response["kind"] == "choices"


def test_brain_error_degrades_to_spoken_apology_not_crash(db):
    brain = FakeBrain(raises=RuntimeError("api down"))
    session = _session(db, brain=brain)

    response = session.handle("What day is it?")

    assert response["kind"] == "answer"
    assert "try me again" in response["speech"]
    assert db.query(CapturedIntent).count() == 0


# ---------------------------------------------------------------------------
# History carryover (follow-ups) and bounds
# ---------------------------------------------------------------------------


def test_follow_up_carries_prior_exchange_in_history(db):
    brain = FakeBrain(
        [
            BrainReply(speech="Saturday looks free so far."),
            BrainReply(speech="Sunday too."),
        ]
    )
    session = _session(db, brain=brain)

    session.handle("What's on this Saturday?")
    session.handle("And what about Sunday?")

    first, second = brain.calls
    assert first["history"] == []
    assert [m.role for m in second["history"]] == ["user", "assistant"]
    assert second["history"][0].content == "What's on this Saturday?"
    assert second["history"][1].content == "Saturday looks free so far."


def test_history_is_bounded(db):
    brain = FakeBrain([BrainReply(speech=f"answer {i}") for i in range(20)])
    session = _session(db, brain=brain)

    for i in range(20):
        session.handle(f"What is question number {i}?")

    # 12 turns × 2 messages max
    assert len(brain.calls[-1]["history"]) <= 24


def test_refused_utterance_never_reaches_brain_or_its_history(db):
    brain = FakeBrain([BrainReply(speech="Hello!")])
    session = _session(db, brain=brain)

    refused = session.handle("My pills make me dizzy. Should I take half tomorrow?")
    assert refused["kind"] == "refused"
    assert brain.calls == []

    session.handle("What day is it?")
    assert len(brain.calls) == 1
    assert brain.calls[0]["history"] == []  # the refused turn left no trace


def test_deterministic_capture_stays_primary_over_brain(db):
    brain = FakeBrain()
    session = _session(db, brain=brain)

    response = session.handle("Remind me to water the plants tomorrow")

    assert response["kind"] == "captured"
    assert brain.calls == []


def test_vague_utterance_keeps_deterministic_repair_choices(db):
    brain = FakeBrain()
    session = _session(db, brain=brain)

    response = session.handle("Call... the... you know... the one with the garden...")

    assert response["kind"] == "choices"
    assert brain.calls == []


# ---------------------------------------------------------------------------
# Action proposals: confirmation-gated, never a direct capture
# ---------------------------------------------------------------------------


def _reminder_proposal():
    return ProposedAction(
        action_type="reminder",
        label="a reminder to do the morning stretches",
        subject="do the morning stretches",
        intent_text="remind me to do the morning stretches",
    )


def test_proposed_action_becomes_choice_never_direct_capture(db):
    brain = FakeBrain(
        [BrainReply(speech="Happy to.", proposed_actions=(_reminder_proposal(),))]
    )
    session = _session(db, brain=brain)

    response = session.handle("Could you help me remember my stretches somehow?")

    assert response["kind"] == "choices"
    assert "Happy to." in response["speech"]
    assert "Should I set that up?" in response["speech"]
    labels = [c["label"] for c in response["choices"]]
    assert labels == ["a reminder to do the morning stretches", "none of these"]
    # proposal alone captures nothing
    assert db.query(CapturedIntent).count() == 0


def test_selecting_proposed_choice_captures_through_pipeline(db):
    brain = FakeBrain(
        [BrainReply(speech="Happy to.", proposed_actions=(_reminder_proposal(),))]
    )
    session = _session(db, brain=brain)

    session.handle("Could you help me remember my stretches somehow?")
    response = session.handle("1")

    assert response["kind"] == "captured"
    captured = db.query(CapturedIntent).one()
    assert captured.requested_action == "reminder"
    assert captured.subject == "do the morning stretches"


def test_declining_proposed_choice_captures_nothing(db):
    brain = FakeBrain(
        [BrainReply(speech="Happy to.", proposed_actions=(_reminder_proposal(),))]
    )
    session = _session(db, brain=brain)

    session.handle("Could you help me remember my stretches somehow?")
    response = session.handle("2")  # none of these

    assert response["kind"] == "retry"
    assert db.query(CapturedIntent).count() == 0


def test_message_proposal_to_unknown_recipient_is_dropped(db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "personal_lexicon", "Sarah, Priya")
    brain = FakeBrain(
        [
            BrainReply(
                speech="I can let her know.",
                proposed_actions=(
                    ProposedAction(
                        action_type="family_message",
                        label="send Gertrude a message about dinner",
                        subject="message Gertrude",
                        intent_text="dinner is at six",
                        recipient="Gertrude",
                    ),
                ),
            )
        ]
    )
    session = _session(db, brain=brain)

    response = session.handle("Someone should know dinner is at six, don't you think?")

    # the unaddressable proposal vanishes; the speech remains an answer
    assert response["kind"] == "answer"
    assert response["speech"] == "I can let her know."
    assert db.query(CapturedIntent).count() == 0


def test_message_proposal_to_lexicon_name_offers_choice_with_recipient(db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "personal_lexicon", "Sarah, Priya")
    brain = FakeBrain(
        [
            BrainReply(
                speech="",
                proposed_actions=(
                    ProposedAction(
                        action_type="family_message",
                        label="send Sarah a message that dinner is at six",
                        subject="message Sarah",
                        intent_text="dinner is at six",
                        recipient="sarah",  # ASR-ish casing resolves to lexicon spelling
                    ),
                ),
            )
        ]
    )
    session = _session(db, brain=brain)

    response = session.handle("Someone should know dinner is at six, don't you think?")
    assert response["kind"] == "choices"

    captured_response = session.handle("1")
    assert captured_response["kind"] == "captured"
    captured = db.query(CapturedIntent).one()
    assert captured.requested_action == "family_message"
    assert captured.recipient == "Sarah"
    assert captured.intent_text == "dinner is at six"


def test_medical_drift_in_brain_reply_is_refused_and_flagged(db):
    brain = FakeBrain(
        [
            BrainReply(
                speech="You should take an extra 50 mg when you feel stiff.",
                proposed_actions=(_reminder_proposal(),),
            )
        ]
    )
    session = _session(db, brain=brain)

    response = session.handle("What helps with stiffness in the mornings?")

    assert response["kind"] == "refused"
    assert response["speech"] == MEDICAL_BOUNDARY_REDIRECT
    assert response["flag_for_family"] is True
    assert db.query(CapturedIntent).count() == 0
