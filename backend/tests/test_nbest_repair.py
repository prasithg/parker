"""N-best repair seam: alternate ASR hypotheses enrich repair choices.

Alternates are never routed directly — they can only surface as repair
choices (confirmation-gated), and only when they parse to a concrete safe
intent. A selected alternate-derived choice captures its parsed
recipient/subject, fixing the "Tell Sarah" -> "There a" ASR-erasure case
where the degraded primary transcript loses the recipient.
"""

from app.conversation.textloop import TextSession, probe_direct_intent
from app.db.models import CallLog, CapturedIntent


def _session(db):
    call = CallLog(call_sid="CA_NBEST", call_type="text_loop")
    db.add(call)
    db.commit()
    db.refresh(call)
    return TextSession(db, call.id)


# ------------------------------------------------------------------ probe

def test_probe_parses_message_with_recipient_and_body() -> None:
    intent = probe_direct_intent("Tell Sarah physio went well today")
    assert intent is not None
    assert intent["action_type"] == "family_message"
    assert intent["recipient"] == "Sarah"
    assert intent["intent_text"] == "physio went well today"
    assert "Sarah" in intent["label"]


def test_probe_parses_reminder_and_exercise() -> None:
    reminder = probe_direct_intent("Remind me to water the plants this evening")
    assert reminder is not None and reminder["action_type"] == "reminder"
    assert reminder["subject"] == "water the plants this evening"
    exercise = probe_direct_intent("Start a speech exercise for the morning cards")
    assert exercise is not None and exercise["action_type"] == "exercise_start"
    assert exercise["subject"] == "speech exercise: the morning cards"


def test_probe_parses_music_media_without_broad_hear_about_matches() -> None:
    playlist = probe_direct_intent("Play my rock playlist")
    assert playlist is not None
    assert playlist["action_type"] == "media_playlist"
    assert playlist["subject"] == "my rock playlist"

    track = probe_direct_intent("I want to hear Snow by Red Hot Chili Peppers")
    assert track is not None
    assert track["action_type"] == "media_playlist"
    assert track["subject"] == "Snow by Red Hot Chili Peppers"

    assert probe_direct_intent("I want to hear about tomorrow's appointment") is None


def test_probe_refuses_safety_tripping_hypotheses() -> None:
    # Alternates must not become a side door around the refusal guards.
    assert probe_direct_intent("Remind me to take half my pills") is None
    assert probe_direct_intent("Tell Sarah to order that walker") is None
    assert probe_direct_intent("Remind me about my bank account password") is None


def test_probe_returns_none_for_non_commands() -> None:
    assert probe_direct_intent("There a physio went well today") is None
    assert probe_direct_intent("") is None
    assert probe_direct_intent("the weather is nice") is None


# ------------------------------------------------------- session behavior

DEGRADED = "There a... the physio thing... you know..."
ALTERNATE = "Tell Sarah physio went well today"


def test_alternate_hypothesis_becomes_first_choice_and_captures_recipient(db):
    session = _session(db)

    response = session.handle(DEGRADED, alternates=[ALTERNATE])

    assert response["kind"] == "choices"
    first = response["choices"][0]
    assert first["action_type"] == "family_message"
    assert "Sarah" in first["label"]

    selected = session.handle(str(first["position"]))
    assert selected["kind"] == "captured"
    captured = db.query(CapturedIntent).one()
    assert captured.requested_action == "family_message"
    assert captured.recipient == "Sarah"
    assert captured.intent_text == "physio went well today"


def test_media_alternate_becomes_first_choice_and_captures_clean_subject(db):
    session = _session(db)

    response = session.handle(
        "I want to hear us now by Red Hot Chili Peppers.",
        alternates=["I want to hear Snow by Red Hot Chili Peppers."],
    )

    assert response["kind"] == "choices"
    first = response["choices"][0]
    assert first["action_type"] == "media_playlist"
    assert first["subject"] == "Snow by Red Hot Chili Peppers"
    assert "Snow by Red Hot Chili Peppers" in first["label"]
    assert db.query(CapturedIntent).count() == 0

    selected = session.handle(str(first["position"]))
    assert selected["kind"] == "captured"
    captured = db.query(CapturedIntent).one()
    assert captured.requested_action == "media_playlist"
    assert captured.subject == "Snow by Red Hot Chili Peppers"


def test_alternates_are_never_routed_directly(db):
    session = _session(db)

    # A clean, capturable alternate must still only appear as a choice —
    # the degraded primary utterance stays the routed input.
    response = session.handle(DEGRADED, alternates=["Remind me to water the plants"])

    assert response["kind"] == "choices"
    assert db.query(CapturedIntent).count() == 0


def test_safety_tripping_alternate_never_appears_in_choices(db):
    session = _session(db)

    response = session.handle(DEGRADED, alternates=["Remind me to take half my pills tomorrow"])

    assert response["kind"] == "choices"
    assert all("pills" not in c["label"] for c in response["choices"])


def test_alternate_identical_to_primary_is_ignored(db):
    session = _session(db)

    response = session.handle(DEGRADED, alternates=[DEGRADED])

    assert response["kind"] == "choices"
    # No enriched fields anywhere — nothing was probed.
    assert all("recipient" not in c for c in response["choices"])


def test_handle_without_alternates_is_unchanged(db):
    session = _session(db)

    response = session.handle("Remind me to water the tomato plants this evening")

    assert response["kind"] == "captured"
    assert db.query(CapturedIntent).one().requested_action == "remind"
