"""Scenario gauntlet — actions: the card, the tap, and the family's hands.

Dimension: everything between "put on old Hindi songs" and the TV actually
playing. The live lane never acts — it stages a card and says so; the tap
happens on the screen; the family's OpenClaw skill is the only thing with
hands. Each test is one Ravi story asserting the BRIDGE CONTRACT (acks
upstream, nudges, browser frames, DB rows) plus, where the story runs all
the way to the speaker, the DB-level confirm/execute pipeline.
"""

from __future__ import annotations

import json
import threading
import time

from scenario_harness import *  # noqa: F401,F403

TV_MUSIC = {"name": "tv-music", "action_types": ["media_playlist"], "enabled": True}
BROWSING = {"name": "browsing", "action_types": ["open_links"], "enabled": True}


def _drained() -> bool:
    """True once no bridge is running — every shutdown write has landed."""

    from app.parker import realtime as realtime_module

    return realtime_module._active_bridges == 0


def test_old_hindi_songs_go_to_the_screen_then_to_the_family_skill(voice_world):
    """Sunday afternoon: Kishore Kumar on the living-room TV.

    The whole promise of the lane in one arc — the speech is the model's,
    the action is Parker's (a card, nothing more), and the hands are the
    family's tv-music skill, which only moves after he taps yes.
    """

    world = voice_world
    world.seed_ravi()
    record: list[dict] = []
    gw = world.gateway(skills=[TV_MUSIC], record=record)
    world.enable_hands(gw)
    fake = world.script([])

    with world.connect() as ws:
        fake.feed(done())  # settle the greeting so a nudge would be legal
        assert _wait_until(lambda: context_cards(fake))  # card's DB read done
        fake.feed(
            done(
                propose_call(
                    {
                        "action_type": "media_playlist",
                        "label": "old Hindi songs",
                        "subject": "Kishore Kumar playlist on the TV",
                        "intent_text": "put on old Hindi songs, Kishore Kumar, on the TV",
                    },
                    call_id="pl",
                )
            )
        )
        assert _wait_until(lambda: _function_outputs(fake))
        ack = json.loads(_function_outputs(fake)[0]["item"]["output"])
        assert ack["status"] == "staged"
        assert "Nothing runs until he says yes" in ack["detail"]  # spoken confirmation

        assert_staged(ws.receive_json(), "old Hindi songs")
        assert _response_creates(fake) == 2  # greeting + the proposal's one nudge
        ws.send_json({"type": "end"})

    assert _wait_until(_drained)
    assert record == []  # the conversation itself never touched the gateway

    from app.db.models import StagedAction
    from app.parker.pipeline import confirm_staged_action, execute_staged_action

    world.db.expire_all()
    action = world.db.query(StagedAction).one()
    assert action.status == "staged"

    confirm_staged_action(world.db, action.id, confirmed_by="patient")
    executed = execute_staged_action(world.db, action.id)
    assert executed.status == "executed"
    assert executed.execution_result == "openclaw skill completed: done (mock)"
    assert record == [
        {
            "action_type": "media_playlist",
            "payload": {
                "subject": "Kishore Kumar playlist on the TV",
                "intent_text": "put on old Hindi songs, Kishore Kumar, on the TV",
                "recipient": None,
                "skill": "tv-music",
            },
            "idempotency_key": f"staged-action-{action.id}",
        }
    ]


def test_the_tv_stays_quiet_until_he_taps_yes(voice_world):
    """He asked for the songs and wandered off without tapping.

    Something downstream tries to run it anyway. Nothing plays — and a
    confirmation arriving after that premature attempt cannot turn the
    blocked row back into music.

    DESIGN GAP: ``blocked`` is not in ``execute_staged_action``'s terminal
    set, but ``confirm_staged_action`` only touches staged/confirmed rows —
    so one premature execute leaves the card permanently dead, and Ravi's
    later tap is silently swallowed with no visible outcome and no recovery
    path. Safe by construction (nothing can ever play), but whether the
    screen should re-stage or say so is an open product question.
    """

    world = voice_world
    world.seed_ravi()
    record: list[dict] = []
    gw = world.gateway(skills=[TV_MUSIC], record=record)
    world.enable_hands(gw)
    fake = world.script([])

    with world.connect() as ws:
        fake.feed(done())
        assert _wait_until(lambda: context_cards(fake))  # card's DB read done
        fake.feed(
            done(
                propose_call(
                    {
                        "action_type": "media_playlist",
                        "label": "old Hindi songs",
                        "subject": "Kishore Kumar playlist on the TV",
                        "intent_text": "put on old Hindi songs on the TV",
                    },
                    call_id="pl",
                )
            )
        )
        assert _wait_until(lambda: _function_outputs(fake))
        assert json.loads(_function_outputs(fake)[0]["item"]["output"])["status"] == "staged"
        assert_staged(ws.receive_json(), "old Hindi songs")
        ws.send_json({"type": "end"})

    assert _wait_until(_drained)

    from app.db.models import StagedAction
    from app.parker.pipeline import confirm_staged_action, execute_staged_action

    world.db.expire_all()
    action = world.db.query(StagedAction).one()

    premature = execute_staged_action(world.db, action.id)
    assert premature.status == "blocked"
    assert premature.execution_result == "Action requires confirmation before execution."
    assert record == []

    late_yes = confirm_staged_action(world.db, action.id, confirmed_by="patient")
    assert late_yes.status == "blocked"  # confirm only acts on staged/confirmed
    assert late_yes.confirmed_by is None

    again = execute_staged_action(world.db, action.id)
    assert again.status == "blocked"
    assert again.executed_at is None
    assert record == []  # zero gateway invocations, ever


def test_sunday_round_of_messages_sarah_meera_and_ramesh(voice_world, monkeypatch):
    """After Sarah's Sunday visit: three messages, three endings.

    Sarah (family contact) releases on his own confirmation, Meera (a name
    Parker knows but the family did not put on the allowlist) waits for
    family approval, and Ramesh — a colleague nobody ever added — is
    refused outright. Nothing leaves the machine either way.
    """

    world = voice_world
    from app.config import settings

    monkeypatch.setattr(settings, "personal_lexicon", "Sarah, Anil, Meera")
    monkeypatch.setattr(settings, "parker_family_contacts", "Sarah, Anil")
    world.seed_ravi()
    fake = world.script([])

    with world.connect() as ws:
        fake.feed(done())
        assert _wait_until(lambda: context_cards(fake))  # card's DB read done
        fake.feed(
            done(
                propose_call(
                    {
                        "action_type": "family_message",
                        "label": "message for Sarah",
                        "subject": "message for Sarah",
                        "intent_text": "tell Sarah the physio went well today",
                        "recipient": "Sara",  # ASR heard it short
                    },
                    call_id="m1",
                ),
                propose_call(
                    {
                        "action_type": "family_message",
                        "label": "thanks for the sabzi",
                        "subject": "thanks for the sabzi",
                        "intent_text": "thank Meera for the sabzi",
                        "recipient": "Meera",
                    },
                    call_id="m2",
                ),
                propose_call(
                    {
                        "action_type": "family_message",
                        "label": "call on Sunday",
                        "subject": "call on Sunday",
                        "intent_text": "tell Ramesh I will call him on Sunday",
                        "recipient": "Ramesh",
                    },
                    call_id="m3",
                ),
            )
        )
        assert _wait_until(lambda: len(_function_outputs(fake)) == 3)
        acks = [json.loads(o["item"]["output"]) for o in _function_outputs(fake)]
        assert [a["status"] for a in acks] == ["staged", "staged", "rejected"]
        assert "not in the family" in acks[2]["detail"]

        first = ws.receive_json()
        second = ws.receive_json()
        assert_staged(first, "message for Sarah")
        assert_staged(second, "thanks for the sabzi")
        # Three proposals inside ONE response.done produce exactly one nudge;
        # the rest defer behind the optimistic active response.
        assert _response_creates(fake) == 2
        ws.send_json({"type": "end"})

    assert _wait_until(_drained)

    from app.db.models import OutboxMessage, StagedAction
    from app.parker.pipeline import confirm_staged_action, execute_staged_action

    world.db.expire_all()
    actions = world.db.query(StagedAction).order_by(StagedAction.id).all()
    assert len(actions) == 2
    by_recipient = {
        json.loads(a.action_payload)["recipient"]: a for a in actions
    }
    assert set(by_recipient) == {"Sarah", "Meera"}  # "Sara" canonicalized at capture

    for action in actions:
        confirm_staged_action(world.db, action.id, confirmed_by="patient")
        execute_staged_action(world.db, action.id)

    sarah_out = (
        world.db.query(OutboxMessage)
        .filter(OutboxMessage.staged_action_id == by_recipient["Sarah"].id)
        .one()
    )
    assert sarah_out.status == "released_local"
    assert sarah_out.released_by == "capability_policy:family_contact_allowlist"

    meera_out = (
        world.db.query(OutboxMessage)
        .filter(OutboxMessage.staged_action_id == by_recipient["Meera"].id)
        .one()
    )
    assert meera_out.status == "queued_local"
    assert "awaiting family approval" in by_recipient["Meera"].execution_result
    assert world.db.query(OutboxMessage).count() == 2  # nothing for Ramesh


def test_two_cards_in_one_sitting_only_one_of_them_confirmed(voice_world):
    """Kishore Kumar first, then the US Open highlights page.

    He taps yes on the songs and leaves the highlights sitting there. The
    second card must not ride along on the first tap, and the browsing
    skill must stay untouched.
    """

    world = voice_world
    world.seed_ravi()
    record: list[dict] = []
    gw = world.gateway(skills=[TV_MUSIC, BROWSING], record=record)
    world.enable_hands(gw)
    fake = world.script([])

    with world.connect() as ws:
        fake.feed(done())
        assert _wait_until(lambda: context_cards(fake))  # card's DB read done
        fake.feed(
            done(
                propose_call(
                    {
                        "action_type": "media_playlist",
                        "label": "old Hindi songs",
                        "subject": "Kishore Kumar playlist on the TV",
                        "intent_text": "put on Kishore Kumar on the TV",
                    },
                    call_id="a",
                )
            )
        )
        assert _wait_until(lambda: len(_function_outputs(fake)) == 1)
        assert_staged(ws.receive_json(), "old Hindi songs")

        fake.feed(
            done(
                propose_call(
                    {
                        "action_type": "open_links",
                        "label": "US Open highlights",
                        "subject": "US Open highlights page",
                        "intent_text": "show the US Open highlights on the computer",
                    },
                    call_id="b",
                )
            )
        )
        assert _wait_until(lambda: len(_function_outputs(fake)) == 2)
        assert_staged(ws.receive_json(), "US Open highlights")

        acks = [json.loads(o["item"]["output"]) for o in _function_outputs(fake)]
        assert [a["status"] for a in acks] == ["staged", "staged"]
        # One nudge per separate response.done (contrast the three-in-one above).
        assert _response_creates(fake) == 3
        ws.send_json({"type": "end"})

    assert _wait_until(_drained)

    from app.db.models import StagedAction
    from app.parker.pipeline import confirm_staged_action, execute_staged_action

    world.db.expire_all()
    actions = world.db.query(StagedAction).order_by(StagedAction.id).all()
    assert [a.action_type for a in actions] == ["media_playlist", "open_links"]
    assert [a.status for a in actions] == ["staged", "staged"]
    playlist, links = actions

    confirm_staged_action(world.db, playlist.id, confirmed_by="patient")
    execute_staged_action(world.db, playlist.id)

    world.db.expire_all()
    links = world.db.get(StagedAction, links.id)
    assert links.status == "staged"
    assert links.confirmed_by is None
    assert links.executed_at is None
    assert len(record) == 1
    assert record[0]["action_type"] == "media_playlist"
    assert record[0]["idempotency_key"] == f"staged-action-{playlist.id}"


def test_never_mind_the_songs_cancels_the_card_for_good(voice_world):
    """"No, no — never mind the songs, Anil is calling."

    He changes his mind while Parker is reading the card back. The card is
    cancelled before anything plays, no later confirmation un-cancels it,
    and the session still finalizes with what he actually said.
    """

    world = voice_world
    world.seed_ravi()
    record: list[dict] = []
    gw = world.gateway(skills=[TV_MUSIC], record=record)
    world.enable_hands(gw)
    fake = world.script([])

    with world.connect() as ws:
        fake.feed(done())
        assert _wait_until(lambda: context_cards(fake))  # card's DB read done
        fake.feed(
            done(
                propose_call(
                    {
                        "action_type": "media_playlist",
                        "label": "old Hindi songs",
                        "subject": "Kishore Kumar playlist on the TV",
                        "intent_text": "put on Kishore Kumar on the TV",
                    },
                    call_id="mind",
                )
            )
        )
        assert _wait_until(lambda: _function_outputs(fake))
        assert_staged(ws.receive_json(), "old Hindi songs")

        fake.feed(speech_started())
        assert ws.receive_json() == {"type": "clear"}
        fake.feed(user_said("no no, never mind the songs, Anil is calling"))
        heard = ws.receive_json()
        assert heard["type"] == "user_transcript"
        assert "never mind" in heard["text"]
        fake.feed(done())
        # a visible event AFTER that response.done proves the exchange landed
        fake.feed(model_said("Right, leaving it off then."))
        assert ws.receive_json()["type"] == "assistant_transcript_delta"
        ws.send_json({"type": "end"})

    assert _wait_until(_drained)

    from app.db.models import CallLog, StagedAction
    from app.parker.pipeline import (
        cancel_staged_action,
        confirm_staged_action,
        execute_staged_action,
    )

    world.db.expire_all()
    action = world.db.query(StagedAction).one()

    cancelled = cancel_staged_action(world.db, action.id, cancelled_by="patient")
    assert cancelled.status == "cancelled"
    assert cancelled.cancelled_by == "patient"
    assert cancelled.execution_result.startswith("cancelled by patient before execution")

    assert confirm_staged_action(world.db, action.id, confirmed_by="patient").status == "cancelled"
    assert execute_staged_action(world.db, action.id).status == "cancelled"
    assert record == []  # tv-music was never invoked

    call = world.db.query(CallLog).filter(CallLog.call_sid.like("REALTIME-%")).one()
    assert call.ended_at is not None
    assert "never mind" in (call.summary or "")


def test_the_hermes_box_dies_between_the_card_and_the_tap(voice_world):
    """Rafi songs at eight; the gateway falls over before he taps yes.

    Parker must fail loudly into the review trail — one attempt, a failed
    row naming the skill, and no silent retry that could play twice.
    """

    world = voice_world
    world.seed_ravi()
    record: list[dict] = []
    live = world.gateway(skills=[TV_MUSIC], record=record)
    world.enable_hands(live)
    fake = world.script([])

    with world.connect() as ws:
        fake.feed(done())
        assert _wait_until(lambda: context_cards(fake))  # card's DB read done
        fake.feed(
            done(
                propose_call(
                    {
                        "action_type": "media_playlist",
                        "label": "Rafi evening",
                        "subject": "Mohammed Rafi playlist",
                        "intent_text": "play Mohammed Rafi songs on the TV",
                    },
                    call_id="pl2",
                )
            )
        )
        assert _wait_until(lambda: _function_outputs(fake))
        ack = json.loads(_function_outputs(fake)[0]["item"]["output"])
        assert ack["status"] == "staged"  # staging never depends on a live box
        assert_staged(ws.receive_json(), "Rafi evening")
        ws.send_json({"type": "end"})

    assert _wait_until(_drained)

    from app.db.models import StagedAction
    from app.parker import hands as hands_module
    from app.parker.pipeline import confirm_staged_action, execute_staged_action

    dead = world.gateway(down=True)
    # discover() against a 503 gateway raises — the box died AFTER discovery.
    hands_module.configure_hands(hands_module.OpenClawHands(dead, [TV_MUSIC]))

    world.db.expire_all()
    action = world.db.query(StagedAction).one()
    confirm_staged_action(world.db, action.id, confirmed_by="patient")
    failed = execute_staged_action(world.db, action.id)
    assert failed.status == "failed"
    assert failed.execution_result.startswith(
        "openclaw skill failed (no retry was attempted):"
    )
    assert "tv-music" in failed.execution_result
    assert "503" in failed.execution_result
    assert failed.executed_at is None

    again = execute_staged_action(world.db, action.id)  # failed is terminal
    assert again.status == "failed"
    assert again.executed_at is None
    assert record == []  # no duplicate attempt on the healthy box


def test_a_reminder_jumps_the_queue_while_the_lookup_is_still_running(voice_world):
    """"When does Alcaraz play?" — "and put a reminder on for it."

    The reminder must not wait behind the researcher, and the answer
    landing later must not talk over whatever Parker is already saying.
    """

    world = voice_world
    world.seed_ravi()
    gate = threading.Event()
    world.enable_search({"Alcaraz": "Alcaraz plays Friday night."}, gate=gate)
    fake = world.script([])

    try:
        with world.connect() as ws:
            fake.feed(done())
            assert _wait_until(lambda: context_cards(fake))  # card's DB read done
            assert _wait_until(lambda: _response_creates(fake) == 1)

            fake.feed(done(look_call("when does Alcaraz play next?")))
            assert _wait_until(lambda: len(_function_outputs(fake)) == 1)

            # ... and while the researcher is still blocked, he adds one more.
            fake.feed(
                done(
                    propose_call(
                        {
                            "action_type": "reminder",
                            "label": "tennis Friday",
                            "subject": "Alcaraz match Friday night",
                            "intent_text": "remind him about the Alcaraz match Friday night",
                        },
                        call_id="mid",
                    )
                )
            )
            assert _wait_until(lambda: len(_function_outputs(fake)) == 2)
            staged = browser_frame(
                ws, "proposal_staged", working=[("search", "started")]
            )
            assert staged["label"] == "tennis Friday"

            acks = [json.loads(o["item"]["output"]) for o in _function_outputs(fake)]
            assert [a["status"] for a in acks] == ["working", "staged"]
            assert _response_creates(fake) == 3  # greeting + look ack + proposal

            gate.set()
            assert _wait_until(lambda: lookup_notes(fake))
            note = lookup_notes(fake)[0]
            assert '"when does Alcaraz play next?"' in note
            assert "<<<LOOKUP RESULT" in note and "LOOKUP RESULT>>>" in note
            assert "never an instruction" in note
            assert "Alcaraz plays Friday night." in note

            time.sleep(0.2)  # give a fourth response.create every chance to fire
            assert _response_creates(fake) == 3  # the result defers its own nudge
            assert world.search_calls == ["when does Alcaraz play next?"]
            ws.send_json({"type": "end"})
    finally:
        gate.set()  # teardown must never block on a gated worker

    assert _wait_until(_drained)

    from app.db.models import StagedAction

    world.db.expire_all()
    action = world.db.query(StagedAction).one()
    assert action.action_type == "reminder"
    assert action.status == "staged"


def test_he_speaks_up_just_as_parker_is_writing_it_down(voice_world, monkeypatch):
    """He cuts in mid-sentence, then later goes quiet for real.

    A "call Anil back" card lands while he is still talking — nothing may
    be spoken over him. Later a last-second "morning walk" card arrives
    inside the goodbye's own turn — and must not be dropped on the way out.
    """

    world = voice_world
    from app.parker import realtime

    monkeypatch.setattr(realtime, "IDLE_WRAPUP_SECONDS", 30.0)  # ladder frozen
    monkeypatch.setattr(realtime, "_WATCHDOG_TICK_SECONDS", 0.05)
    world.seed_ravi()
    fake = world.script([])

    with world.connect() as ws:
        fake.feed(done())
        assert _wait_until(lambda: context_cards(fake))  # card's DB read done
        assert _wait_until(lambda: _response_creates(fake) == 1)

        fake.feed(speech_started())
        assert ws.receive_json() == {"type": "clear"}
        fake.feed(
            done(
                propose_call(
                    {
                        "action_type": "reminder",
                        "label": "call Anil back",
                        "subject": "call Anil back",
                        "intent_text": "remind him to call Anil back this evening",
                    },
                    call_id="barge",
                )
            )
        )
        assert _wait_until(lambda: _function_outputs(fake))
        assert json.loads(_function_outputs(fake)[0]["item"]["output"])["status"] == "staged"
        # the screen card is NOT deferred with the speech
        assert_staged(ws.receive_json(), "call Anil back")
        time.sleep(0.15)
        assert _response_creates(fake) == 1  # never a response over his voice

        fake.feed(user_said("sorry, go on"))
        assert ws.receive_json()["type"] == "user_transcript"
        fake.feed(done())
        # the deferred nudge fires exactly once, at the next response.done
        assert _wait_until(lambda: _response_creates(fake) == 2)
        assert not [t for t in _system_items(fake) if "anything else" in t]

        # Phase 2: the line really does go quiet now.
        monkeypatch.setattr(realtime, "IDLE_WRAPUP_SECONDS", 0.15)
        monkeypatch.setattr(realtime, "IDLE_GOODBYE_SECONDS", 0.4)
        monkeypatch.setattr(realtime, "CLOSING_DRAIN_SECONDS", 0.2)
        assert _wait_until(
            lambda: [t for t in _system_items(fake) if "anything else" in t]
        )
        assert _wait_until(
            lambda: [t for t in _system_items(fake) if "closes on its own" in t]
        )
        # this response.done IS the goodbye's own turn
        fake.feed(
            done(
                propose_call(
                    {
                        "action_type": "reminder",
                        "label": "morning walk",
                        "subject": "morning walk before it gets hot",
                        "intent_text": "remind him to walk before ten",
                    },
                    call_id="late",
                )
            )
        )
        assert_staged(ws.receive_json(), "morning walk")
        assert ws.receive_json() == {"type": "closing"}
        ws.send_json({"type": "end"})

    assert _wait_until(_drained)

    from app.db.models import StagedAction

    world.db.expire_all()
    assert world.db.query(StagedAction).count() == 2


def test_tuesdays_card_is_still_tuesdays_card(voice_world):
    """Monday's untapped reminder must not ride into Tuesday's session.

    He asks for a walk reminder Monday evening and never taps it. Tuesday
    he starts Parker again and asks about the tomatoes. The new session
    stages exactly its own intent, and Monday's card comes out untouched.

    Harness caveat (load-bearing): the shared in-memory connection must not
    be queried between the two bridges — an open read transaction breaks
    the second bridge's staging thread. Everything is asserted at the end.
    """

    world = voice_world
    world.seed_ravi()

    fake1 = world.script([])
    with world.connect() as ws:
        fake1.feed(done())
        assert _wait_until(lambda: context_cards(fake1))  # card's DB read done
        fake1.feed(
            done(
                propose_call(
                    {
                        "action_type": "reminder",
                        "label": "walk",
                        "subject": "morning walk before it gets hot",
                        "intent_text": "remind him to walk",
                    },
                    call_id="s1",
                )
            )
        )
        assert _wait_until(lambda: _function_outputs(fake1))
        assert json.loads(_function_outputs(fake1)[0]["item"]["output"])["status"] == "staged"
        assert_staged(ws.receive_json(), "walk")
        fake1.feed(user_said("thanks for setting that up"))
        assert ws.receive_json()["type"] == "user_transcript"
        fake1.feed(done())
        # a visible event AFTER that response.done proves the exchange landed
        fake1.feed(model_said("Any time."))
        assert ws.receive_json()["type"] == "assistant_transcript_delta"
        ws.send_json({"type": "end"})

    assert _wait_until(_drained)  # session one's finalize has landed

    fake2 = world.script([])
    with world.connect() as ws:
        fake2.feed(done())
        assert _wait_until(lambda: context_cards(fake2))  # card's DB read done
        fake2.feed(
            done(
                propose_call(
                    {
                        "action_type": "reminder",
                        "label": "tomatoes",
                        "subject": "water the tomato plants",
                        "intent_text": "remind him to water the tomatoes",
                    },
                    call_id="s2",
                )
            )
        )
        assert _wait_until(lambda: _function_outputs(fake2))
        assert json.loads(_function_outputs(fake2)[0]["item"]["output"])["status"] == "staged"
        assert_staged(ws.receive_json(), "tomatoes")
        ws.send_json({"type": "end"})

    assert _wait_until(_drained)

    from app.db.models import CallLog, StagedAction
    from app.memory.models import ConversationMemory

    world.db.expire_all()
    actions = world.db.query(StagedAction).order_by(StagedAction.id).all()
    assert len(actions) == 2  # session two re-staged nothing of session one's
    monday, tuesday = actions
    assert monday.status == "staged"
    assert monday.confirmed_by is None
    assert monday.resurface_count == 0
    payloads = [json.loads(a.action_payload) for a in actions]
    assert [p["subject"] for p in payloads] == [
        "morning walk before it gets hot",
        "water the tomato plants",
    ]
    assert payloads[0]["captured_intent_id"] != payloads[1]["captured_intent_id"]

    calls = (
        world.db.query(CallLog)
        .filter(CallLog.call_sid.like("REALTIME-%"))
        .order_by(CallLog.id)
        .all()
    )
    assert len(calls) == 2
    assert calls[0].call_sid != calls[1].call_sid
    assert calls[0].ended_at is not None
    assert calls[1].summary is None  # nothing he said, nothing invented

    memories = (
        world.db.query(ConversationMemory)
        .filter(ConversationMemory.source == "realtime")
        .all()
    )
    assert len(memories) == 1
    assert memories[0].memory_type == "topic"
    assert "thanks" in memories[0].content
