"""Scenario gauntlet — the human-testing flywheel: seeing the session.

Dimension: the review surface. Pras is the first human tester (docs/
next-slices.md, 2026-08-31): after a live conversation he opens
/parker/sessions/ui and judges what happened. Until this slice a session
evaporated into one summary line and one topic memory; now the bridge
journals every turn, injection, ack, proposal, and guard trip into
realtime_session_events, and one tap files "that felt wrong because…"
against the exact moment.

What is asserted is the BRIDGE CONTRACT plus the review feed built on
it — the journal rows a session leaves and what /parker/sessions serves —
never what gpt-realtime would say. The journal must also never disturb
the pinned live contracts: browser frames, nudge accounting, and the
one-topic-memory pipe stay exactly as the rest of the deck pins them.
"""

from __future__ import annotations

import json

from app.brain.guard import MEDICAL_BOUNDARY_REDIRECT
from app.parker import realtime
from scenario_harness import *  # noqa: F401,F403


def _detail(world, call_sid: str) -> dict:
    response = client.get(f"/parker/sessions/{call_sid}")
    assert response.status_code == 200
    return response.json()


def _feed(world) -> list[dict]:
    return client.get("/parker/sessions").json()["sessions"]


def _the_session(world) -> str:
    """The newest session — exactly the card the tester taps first."""

    world.db.expire_all()
    sessions = _feed(world)
    assert sessions, "the live session must appear on the review feed"
    return sessions[0]["call_sid"]


def _events(world, call_sid: str, kind: str | None = None) -> list[dict]:
    events = _detail(world, call_sid)["events"]
    return [e for e in events if kind is None or e["kind"] == kind]


def _journal_kinds(world) -> list[str]:
    """Journal kinds straight off the DB — the observable to wait on
    before hanging up, so `end` never races the write it is about to pin
    (house rule: observables, not hope)."""

    from app.parker.session_review import RealtimeSessionEvent

    world.db.expire_all()
    return [
        event.kind
        for event in world.db.query(RealtimeSessionEvent)
        .order_by(RealtimeSessionEvent.id)
        .all()
    ]


# ---------------------------------------------------------------------------
# R01 — the whole evening is reviewable the next morning
# ---------------------------------------------------------------------------


def test_the_whole_evening_is_reviewable_the_next_morning(voice_world):
    """Ravi's Alcaraz evening, replayed for Pras: the card Parker was
    handed, the turn, the instant ack, the injected answer with its
    latencies, and the staged reminder — in order, with a one-tap
    "felt wrong" filed against the turn.
    """

    world = voice_world
    world.seed_ravi()
    world.enable_search({"alcaraz": "He plays Friday night, around seven."})
    fake = world.script([])

    with world.connect() as ws:
        world.settle_open(fake)
        fake.feed(user_said("when does Alcaraz play next"))
        assert ws.receive_json() == {
            "type": "user_transcript",
            "text": "when does Alcaraz play next",
        }
        fake.feed(model_said("Let me check that for you."))
        assert ws.receive_json() == {
            "type": "assistant_transcript_delta",
            "text": "Let me check that for you.",
        }
        fake.feed(done(look_call("when does Alcaraz play next")))
        assert _wait_until(lambda: lookup_notes(fake))  # the answer landed
        fake.feed(
            done(
                propose_call(
                    {
                        "action_type": "reminder",
                        "label": "watch Alcaraz Friday",
                        "subject": "watch the Alcaraz match Friday night",
                        "intent_text": "remind me to watch the Alcaraz match Friday night",
                    }
                )
            )
        )
        assert ws.receive_json() == {
            "type": "proposal_staged",
            "label": "watch Alcaraz Friday",
        }
        assert _wait_until(lambda: "proposal" in _journal_kinds(world))
        ws.send_json({"type": "end"})
    assert _wait_until(lambda: realtime._active_bridges == 0)

    call_sid = _the_session(world)
    detail = _detail(world, call_sid)
    kinds = [event["kind"] for event in detail["events"]]
    # the journal tells the evening in order: card in, his turn, the ack,
    # the injected answer, the staged reminder
    assert kinds == ["injection", "turn", "lookup_ack", "injection", "proposal"]

    card_in, turn, ack, answer, proposal = detail["events"]
    assert card_in["detail"]["worker"] == "context"
    assert "Recent memories" in card_in["said"]  # what the model was handed

    assert turn["heard"] == "when does Alcaraz play next"
    assert turn["said"] == "Let me check that for you."
    assert turn["detail"]["guard_tripped"] is False

    assert ack["detail"]["question"] == "when does Alcaraz play next"
    assert ack["detail"]["status"] == "working"
    assert ack["detail"]["ack_ms"] >= 0  # ack latency is finally a number

    assert answer["detail"]["worker"] == "search"
    assert answer["detail"]["question"] == "when does Alcaraz play next"
    assert answer["said"] == "He plays Friday night, around seven."
    assert answer["detail"]["worker_ms"] >= 0
    assert answer["detail"]["since_ask_ms"] >= 0  # asked -> injected, measured

    assert proposal["detail"]["status"] == "staged"
    assert proposal["detail"]["action_type"] == "reminder"
    assert detail["staged_actions"], "the staged reminder shows with the session"
    assert detail["staged_actions"][0]["status"] == "staged"

    # one tap files the judgment against the turn, and the feed counts it
    filed = client.post(
        f"/parker/sessions/{call_sid}/feedback",
        json={"event_id": turn["id"], "note": "the ack felt slow tonight"},
    )
    assert filed.status_code == 200
    world.db.expire_all()
    assert _feed(world)[0]["feedback_count"] == 1
    refreshed = _events(world, call_sid, "turn")[0]
    assert refreshed["feedback"][0]["note"] == "the ack felt slow tonight"


# ---------------------------------------------------------------------------
# R02 — tomorrow's card is part of the review
# ---------------------------------------------------------------------------


def test_the_review_shows_what_tomorrows_card_now_carries(voice_world):
    """The M01 pipe, seen from the review side: after the Alcaraz evening,
    the detail page computes the next session's card and names the topic
    memory this session put there.
    """

    world = voice_world  # empty world: everything on the card came from him
    fake = world.script([])
    with world.connect() as ws:
        world.settle_open(fake, expect_card=False)
        fake.feed(user_said("when does Alcaraz play next"))
        assert ws.receive_json()["type"] == "user_transcript"
        fake.feed(model_said("Friday night."))
        assert ws.receive_json()["type"] == "assistant_transcript_delta"
        fake.feed(done())
        assert _wait_until(lambda: "turn" in _journal_kinds(world))
        ws.send_json({"type": "end"})
    assert _wait_until(lambda: realtime._active_bridges == 0)

    detail = _detail(world, _the_session(world))
    assert "when does Alcaraz play next" in detail["minted_memory"]
    assert any("Alcaraz" in line for line in detail["next_card"]["lines"])


# ---------------------------------------------------------------------------
# R03 — the guard trip is visible to the tester
# ---------------------------------------------------------------------------


def test_a_guard_trip_shows_what_was_cancelled_and_what_was_said(voice_world):
    """When the medical guard cancels the model mid-word, the room hears
    only the redirect — but the tester reviewing the session sees both:
    what the guard caught, and that the redirect is what was spoken.
    """

    world = voice_world
    fake = world.script(
        [
            model_said("Maybe try "),
            model_said("taking an extra 50 mg tonight."),
        ]
    )
    with world.connect() as ws:
        assert ws.receive_json()["type"] == "assistant_transcript_delta"
        assert ws.receive_json() == {"type": "clear"}
        assert ws.receive_json()["type"] == "guard_redirect"
        fake.feed(user_said("what should I do about my pills"))
        assert ws.receive_json()["type"] == "user_transcript"
        fake.feed(done())
        assert _wait_until(lambda: "turn" in _journal_kinds(world))
        ws.send_json({"type": "end"})
    assert _wait_until(lambda: realtime._active_bridges == 0)

    call_sid = _the_session(world)
    trips = _events(world, call_sid, "guard_trip")
    assert len(trips) == 1
    assert "50 mg" in trips[0]["said"]  # what the guard caught, journal-only
    turn = _events(world, call_sid, "turn")[0]
    assert turn["said"] == MEDICAL_BOUNDARY_REDIRECT  # what the room heard
    assert turn["detail"]["guard_tripped"] is True


# ---------------------------------------------------------------------------
# R04 — the unanswered last word still reaches the review
# ---------------------------------------------------------------------------


def test_the_unanswered_last_word_reaches_the_review(voice_world):
    """S09's read side: he spoke, the line dropped before Parker answered —
    the review shows the dangling turn instead of pretending the evening
    ended cleanly.
    """

    world = voice_world
    fake = world.script([])
    with world.connect() as ws:
        world.settle_open(fake, expect_card=False)
        fake.feed(user_said("I have fallen in the kitchen"))
        assert ws.receive_json()["type"] == "user_transcript"
        ws.send_json({"type": "end"})  # no model reply ever arrived
    assert _wait_until(lambda: realtime._active_bridges == 0)

    turns = _events(world, _the_session(world), "turn")
    assert len(turns) == 1
    assert turns[0]["heard"] == "I have fallen in the kitchen"
    assert turns[0]["said"] == ""
    assert turns[0]["detail"]["dangling"] is True


# ---------------------------------------------------------------------------
# R05 — an accidental tap leaves an empty journal, not a fake session
# ---------------------------------------------------------------------------


def test_an_accidental_tap_leaves_an_empty_journal(voice_world):
    """He tapped Live by mistake and said nothing: the feed shows the
    session honestly — zero turns, no summary invented — matching the
    accidental-tap finalize guard.
    """

    world = voice_world
    fake = world.script([])
    with world.connect() as ws:
        world.settle_open(fake, expect_card=False)
        ws.send_json({"type": "end"})
    assert _wait_until(lambda: realtime._active_bridges == 0)

    world.db.expire_all()
    sessions = _feed(world)
    assert len(sessions) == 1
    assert sessions[0]["turn_count"] == 0
    assert sessions[0]["summary"] == ""
    assert _events(world, sessions[0]["call_sid"], "turn") == []
