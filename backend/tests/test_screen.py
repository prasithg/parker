"""The live patient screen: state store, TextSession wiring, and the page.

What these pin down: the screen mirrors exactly one exchange (a single
overwritten row — never a transcript log); numbered choice cards match
the spoken "1) ... 2) ..." options verbatim; capture internals (parsed
recipients, intent text) never reach the store; Parker-initiated
confirmation offers show up with nothing heard; and `make demo` leaves
the screen populated because replay routes through the same session.
"""

from fastapi.testclient import TestClient

from app.config import settings
from app.conversation.textloop import TextSession
from app.db.models import CallLog
from app.demo.replay import replay_transcript
from app.main import app
from app.parker.pipeline import resolve_captured_intents, stage_resolved_actions
from app.parker.screen import (
    AWAITING_CHOICE,
    AWAITING_NOTHING,
    AWAITING_YES_NO,
    ScreenState,
    get_screen_state,
    publish_screen_state,
    serialize_screen_state,
)

client = TestClient(app)


def _session(db, call_sid="SCREEN-TEST"):
    call = CallLog(call_sid=call_sid, call_type="text_loop")
    db.add(call)
    db.commit()
    db.refresh(call)
    return TextSession(db, call.id)


# ---------------------------------------------------------------------------
# The store: one row, current exchange only, no capture internals
# ---------------------------------------------------------------------------


def test_publish_overwrites_the_single_row_and_keeps_no_history(db):
    publish_screen_state(db, heard="first thing", speech="reply one", kind="captured")
    publish_screen_state(db, heard="second thing", speech="reply two", kind="answer")

    assert db.query(ScreenState).count() == 1
    state = get_screen_state(db)
    assert state.heard == "second thing"
    assert state.speech == "reply two"
    assert state.kind == "answer"
    # Nothing from the first exchange survives anywhere in the table.
    assert "first thing" not in state.heard + state.speech + state.choices_json


def test_publish_strips_choice_internals_to_position_and_label(db):
    publish_screen_state(
        db,
        heard="tell... the...",
        speech="Did you mean...",
        kind="choices",
        choices=[
            {
                "position": 1,
                "label": "send Sarah a message",
                "action_type": "family_message",
                "recipient": "Sarah",
                "intent_text": "the physio went well",
                "subject": "message Sarah",
            },
            {"position": 2, "label": "none of these", "action_type": None},
        ],
        awaiting=AWAITING_CHOICE,
    )

    serialized = serialize_screen_state(get_screen_state(db))
    assert serialized["choices"] == [
        {"position": 1, "label": "send Sarah a message"},
        {"position": 2, "label": "none of these"},
    ]
    # The raw row also never holds the enrichment internals.
    raw = get_screen_state(db).choices_json
    for private in ("recipient", "intent_text", "subject", "action_type", "Sarah's"):
        assert private not in raw


# ---------------------------------------------------------------------------
# TextSession wiring: every exchange updates the screen
# ---------------------------------------------------------------------------


def test_capture_exchange_updates_the_screen(db):
    session = _session(db)
    response = session.handle("Remind me to water the tomato plants")

    state = get_screen_state(db)
    assert state.heard == "Remind me to water the tomato plants"
    assert state.speech == response["speech"]
    assert state.kind == "captured"
    assert serialize_screen_state(state)["choices"] == []
    assert state.awaiting == AWAITING_NOTHING


def test_choice_cards_match_the_spoken_numbers_exactly(db):
    session = _session(db)
    response = session.handle("Call... the... you know... the one with the garden...")

    assert response["kind"] == "choices"
    state = serialize_screen_state(get_screen_state(db))
    assert state["awaiting"] == AWAITING_CHOICE
    assert state["choices"] == [
        {"position": choice["position"], "label": choice["label"]}
        for choice in response["choices"]
    ]
    # The cards are the same numbered options the spoken prompt reads aloud.
    for choice in state["choices"]:
        assert f"{choice['position']}) {choice['label']}" in response["speech"]


def test_selection_clears_the_cards(db):
    session = _session(db)
    session.handle("Call... the... you know... the one with the garden...")
    response = session.handle("1")

    assert response["kind"] == "captured"
    state = serialize_screen_state(get_screen_state(db))
    assert state["heard"] == "1"
    assert state["choices"] == []
    assert state["awaiting"] == AWAITING_NOTHING


def test_silence_keeps_pending_cards_on_screen(db):
    # The whole point of the screen: options stay visible while the person
    # works out what to say — silence must not blank the cards.
    session = _session(db)
    offered = session.handle("Call... the... you know... the one with the garden...")
    session.handle("")

    state = serialize_screen_state(get_screen_state(db))
    assert state["kind"] == "noop"
    assert state["awaiting"] == AWAITING_CHOICE
    assert [c["label"] for c in state["choices"]] == [
        c["label"] for c in offered["choices"]
    ]


def test_confirm_offer_shows_parker_speaking_first(db):
    session = _session(db)
    session.handle("Remind me to do the evening stretches")
    resolve_captured_intents(db)
    stage_resolved_actions(db)

    offer = session.offer_pending_confirmation()

    assert offer is not None
    state = get_screen_state(db)
    assert state.heard == ""  # Parker-initiated: nothing was heard
    assert state.kind == "confirm_offer"
    assert state.speech == offer["speech"]
    assert state.awaiting == AWAITING_YES_NO


def test_spoken_yes_updates_screen_to_executed(db):
    session = _session(db)
    session.handle("Remind me to do the evening stretches")
    resolve_captured_intents(db)
    stage_resolved_actions(db)
    session.offer_pending_confirmation()

    session.handle("yes")

    state = get_screen_state(db)
    assert state.kind == "executed"
    assert state.heard == "yes"
    assert state.awaiting == AWAITING_NOTHING


def test_refusal_is_mirrored_without_capturing(db):
    session = _session(db)
    response = session.handle("Should I take half my pills tomorrow?")

    assert response["kind"] == "refused"
    state = get_screen_state(db)
    assert state.kind == "refused"
    assert state.speech == response["speech"]


# ---------------------------------------------------------------------------
# Demo replay: `make demo` leaves the screen populated
# ---------------------------------------------------------------------------


def test_replay_populates_the_screen_with_pending_cards(db):
    replay_transcript(db)

    state = serialize_screen_state(get_screen_state(db))
    # The script deliberately ends mid-repair so the screen shows cards.
    assert state["kind"] == "choices"
    assert state["awaiting"] == AWAITING_CHOICE
    assert len(state["choices"]) >= 2
    assert db.query(ScreenState).count() == 1  # still a mirror, not a log
