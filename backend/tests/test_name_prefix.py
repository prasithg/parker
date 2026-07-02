"""Lexicon-gated name-prefix message parsing.

ASR erases leading verbs on effortful speech: "Tell Sarah physio went
well" arrives as "Sarah physio went well". When the name is in the
family's personal lexicon, Parker offers (never auto-captures) the
message interpretation, and the capture uses the lexicon's canonical
spelling. Without the lexicon, behavior is unchanged.
"""

from app.config import settings
from app.conversation.textloop import TextSession, name_prefix_candidate
from app.db.models import CallLog, CapturedIntent


def _session(db):
    call = CallLog(call_sid="CA_NAMEPREFIX", call_type="text_loop")
    db.add(call)
    db.commit()
    db.refresh(call)
    return TextSession(db, call.id)


def test_no_lexicon_means_no_candidate(monkeypatch):
    monkeypatch.setattr(settings, "personal_lexicon", "")
    assert name_prefix_candidate("Sarah physio went well today") is None


def test_candidate_uses_canonical_lexicon_spelling(monkeypatch):
    monkeypatch.setattr(settings, "personal_lexicon", "Sarah, physio")
    # ASR spelled it "Sara"? Prefix match is case-insensitive on the
    # lexicon name; the canonical spelling wins in the capture.
    intent = name_prefix_candidate("sarah physio went well today")
    assert intent is not None
    assert intent["recipient"] == "Sarah"
    assert intent["action_type"] == "family_message"
    assert intent["intent_text"] == "physio went well today"


def test_lowercase_lexicon_words_are_not_names(monkeypatch):
    monkeypatch.setattr(settings, "personal_lexicon", "physio, Sarah")
    assert name_prefix_candidate("physio went well today") is None


def test_short_remainder_is_not_a_message(monkeypatch):
    monkeypatch.setattr(settings, "personal_lexicon", "Sarah")
    assert name_prefix_candidate("Sarah okay") is None


def test_safety_screen_applies(monkeypatch):
    monkeypatch.setattr(settings, "personal_lexicon", "Sarah")
    assert name_prefix_candidate("Sarah should I take half my pills") is None


def test_verb_erased_message_offers_choice_and_captures_canonically(db, monkeypatch):
    monkeypatch.setattr(settings, "personal_lexicon", "Sarah")
    session = _session(db)

    response = session.handle("Sarah physio went well today")

    assert response["kind"] == "choices"
    first = response["choices"][0]
    assert first["action_type"] == "family_message"
    assert "Sarah" in first["label"]
    # It is offered, never auto-captured.
    assert db.query(CapturedIntent).count() == 0

    session.handle(str(first["position"]))
    captured = db.query(CapturedIntent).one()
    assert captured.recipient == "Sarah"
    assert captured.requested_action == "family_message"
    assert captured.intent_text == "physio went well today"


def test_close_recipient_mangle_snaps_to_lexicon_spelling(db, monkeypatch):
    # ASR: "Send Anna a message..." -> "Send an a message..." — a close
    # mangle snaps to the canonical lexicon name.
    monkeypatch.setattr(settings, "personal_lexicon", "Priya, Anna, Sarah")
    session = _session(db)

    response = session.handle("Send an a message that I'm feeling much better today")

    assert response["kind"] == "captured"
    captured = db.query(CapturedIntent).one()
    assert captured.recipient == "Anna"


def test_badly_mangled_recipient_clarifies_never_captures(db, monkeypatch):
    # "Message Priya that..." -> "Message pre of that..." — "pre" is too
    # far from any known name to guess. Asking beats misdirecting; a
    # message must never be captured toward a nonexistent person.
    monkeypatch.setattr(settings, "personal_lexicon", "Priya, Anna, Sarah")
    session = _session(db)

    response = session.handle("Message pre of that the new chair arrived")

    assert response["kind"] == "clarify"
    assert "pre" in response["speech"]
    assert db.query(CapturedIntent).count() == 0


def test_unknown_recipient_clarifies_instead_of_capturing(db, monkeypatch):
    monkeypatch.setattr(settings, "personal_lexicon", "Priya, Anna")
    session = _session(db)

    response = session.handle("Tell Zorblax the meeting moved")

    assert response["kind"] == "clarify"
    assert db.query(CapturedIntent).count() == 0


def test_no_lexicon_keeps_v0_recipient_behavior(db):
    session = _session(db)

    response = session.handle("Tell Zorblax the meeting moved to Tuesday")

    assert response["kind"] == "captured"
    assert db.query(CapturedIntent).one().recipient == "Zorblax"


def test_talking_about_a_person_can_be_declined(db, monkeypatch):
    monkeypatch.setattr(settings, "personal_lexicon", "Sarah")
    session = _session(db)

    response = session.handle("Sarah came by this afternoon")
    assert response["kind"] == "choices"
    none_choice = next(c for c in response["choices"] if c["action_type"] is None)
    retry = session.handle(str(none_choice["position"]))
    assert retry["kind"] == "retry"
    assert db.query(CapturedIntent).count() == 0
