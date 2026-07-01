"""Flywheel v0: repair-event capture is consent-gated and audio-free.

The binding contract pinned here: with consent OFF (the default), a full
repair exchange writes NOTHING. With consent on, the stored event holds
the degraded utterance, the alternate hypotheses, the offered choices,
and the user's selection — a complete labeled example, transcript-level
only.
"""

import json

from app.config import settings
from app.conversation.textloop import TextSession
from app.conversation.repair_events import RepairEvent
from app.db.models import CallLog
from app.voice.transcribe import lexicon_initial_prompt


def _session(db):
    call = CallLog(call_sid="CA_FLYWHEEL", call_type="text_loop")
    db.add(call)
    db.commit()
    db.refresh(call)
    return TextSession(db, call.id)


DEGRADED = "There a... the physio thing... you know..."
ALTERNATE = "Tell Sarah physio went well today"


def _run_repair_exchange(db, *, pick_none: bool = False) -> None:
    session = _session(db)
    response = session.handle(DEGRADED, alternates=[ALTERNATE])
    assert response["kind"] == "choices"
    if pick_none:
        none_choice = next(c for c in response["choices"] if c["action_type"] is None)
        session.handle(str(none_choice["position"]))
    else:
        session.handle(str(response["choices"][0]["position"]))


def test_consent_defaults_off_and_nothing_is_written(db):
    assert settings.repair_event_capture_consented is False
    _run_repair_exchange(db)
    assert db.query(RepairEvent).count() == 0


def test_consented_selection_stores_the_full_labeled_example(db, monkeypatch):
    monkeypatch.setattr(settings, "repair_event_capture_consented", True)
    _run_repair_exchange(db)

    event = db.query(RepairEvent).one()
    assert event.utterance == DEGRADED
    assert json.loads(event.alternates_json) == [ALTERNATE]
    offered = json.loads(event.offered_choices_json)
    assert any(c["action_type"] == "family_message" for c in offered)
    assert event.selected_action_type == "family_message"
    assert event.selected_position == 1
    assert event.captured_intent_id is not None


def test_consented_none_of_these_rejection_is_recorded(db, monkeypatch):
    monkeypatch.setattr(settings, "repair_event_capture_consented", True)
    _run_repair_exchange(db, pick_none=True)

    event = db.query(RepairEvent).one()
    assert event.selected_action_type is None
    assert event.captured_intent_id is None


def test_lexicon_prompt_builder(monkeypatch):
    monkeypatch.setattr(settings, "personal_lexicon", "")
    assert lexicon_initial_prompt() is None
    monkeypatch.setattr(settings, "personal_lexicon", "Sarah, physio, Leander")
    prompt = lexicon_initial_prompt()
    assert prompt is not None
    assert "Sarah" in prompt and "Leander" in prompt
