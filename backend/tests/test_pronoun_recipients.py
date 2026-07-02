"""Pronoun "recipients" are not recipients.

Found by the first LIVE brain-lane eval run: "Tell me about the trains in
India" matched the tell-X message pattern and captured a family message to
recipient "me", hijacking an informational question before it reached the
brain. First-person pronouns fall through to the answer lane; third-person
pronouns clarify who the message is for.
"""

from app.config import settings
from app.conversation.textloop import TextSession, probe_direct_intent
from app.db.models import CallLog, CapturedIntent


def _session(db):
    call = CallLog(call_sid="CA_PRONOUN", call_type="text_loop")
    db.add(call)
    db.commit()
    db.refresh(call)
    return TextSession(db, call.id)


def test_tell_me_about_is_informational_not_a_message(db):
    session = _session(db)

    response = session.handle("Tell me about the trains in India.")

    assert response["kind"] != "captured"
    assert db.query(CapturedIntent).count() == 0


def test_tell_us_and_send_me_fall_through(db):
    session = _session(db)

    for utterance in ("Tell us the news from this morning", "Send me the weather for Saturday"):
        response = session.handle(utterance)
        assert response["kind"] != "captured"
    assert db.query(CapturedIntent).count() == 0


def test_third_person_pronoun_clarifies_who(db):
    session = _session(db)

    response = session.handle("Tell her the physio visit went well")

    assert response["kind"] == "clarify"
    assert "who" in response["speech"].lower()
    assert db.query(CapturedIntent).count() == 0


def test_named_recipient_still_captures(db, monkeypatch):
    monkeypatch.setattr(settings, "personal_lexicon", "Sarah")
    session = _session(db)

    response = session.handle("Tell Sarah the physio visit went well today")

    assert response["kind"] == "captured"
    assert db.query(CapturedIntent).one().recipient == "Sarah"


def test_probe_never_proposes_pronoun_recipients():
    assert probe_direct_intent("Tell me about the trains in India") is None
    assert probe_direct_intent("Tell her the visit went well") is None
