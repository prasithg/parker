"""Scenario gauntlet — two lines at once: the tablet and the phone, live together.

Dimension: the household's second live line. Ravi is in the recliner with
the tablet; Sarah, in the kitchen or on the drive home, opens Live on her
phone. ``MAX_LIVE_BRIDGES`` is 2, so both are legal and a third tap is not.
Everything the bridge keeps per conversation — exchanges, the finalize
record, in-flight lookups, nudge accounting, the post-hoc guard, the
upstream socket itself — must stay on its own line, and everything the
household genuinely shares (one screen row, one store, one slot budget)
must behave predictably when two lines touch it at once.

Each test asserts the BRIDGE CONTRACT only: what is injected upstream on
*which* socket, which acks and nudges fire where, what reaches which
browser, and what lands in the DB.

Round-1 file (test_scenarios_degraded.py, D12) pinned that two rooms stay
isolated and the third tap is refused. This file is the dimension proper:
concurrent *work* (lookups in flight on both at once, context workers
racing), the slot budget's full cycle (refused, freed, admitted), the
shared screen row's last-writer-wins, and per-line failure isolation.
"""

from __future__ import annotations

import json
import threading
import time

from app.brain.adapter import Source
from app.parker import realtime
from scenario_harness import *  # noqa: F401,F403


# ---------------------------------------------------------------------------
# The two-fake connector — read this before adding a concurrency scenario
# ---------------------------------------------------------------------------


def two_upstreams(world, count: int = 2) -> list:
    """Hand a DIFFERENT FakeUpstream to each concurrent bridge.

    The harness's ``world.script()`` monkeypatches ``connect_openai`` to
    return ONE fake forever. That is right for a single line and wrong
    here: two bridges would share a socket, so every routing assertion
    ("this ack went to *her* upstream, that note to *his*") would pass
    vacuously — both lines' traffic would sit in one ``fake.sent`` list.

    So: a closure over a list, popped in connect order. ``fakes[0]`` is
    the first bridge to open, ``fakes[1]`` the second. A refused third tap
    consumes nothing — the router checks the slot budget *before* building
    a bridge, so ``connect()`` is never called for it. Open the lines one
    at a time (wait for each bridge's session.update to land) so the index
    ordering is deterministic rather than a thread race.
    """

    fakes = [FakeUpstream([]) for _ in range(count)]
    handed = iter(fakes)

    async def connect():
        return next(handed)

    world.mp.setattr(realtime, "connect_openai", connect)
    return fakes


def _opened(fake) -> bool:
    """A bridge has finished opening this socket: config, greeting, nudge."""

    return len(fake.sent) >= 3


def _mirrored(world, text: str):
    from app.parker.screen import get_screen_state

    def check() -> bool:
        world.db.expire_all()
        state = get_screen_state(world.db)
        return state is not None and state.heard == text

    return check


def _finalized(world, count: int):
    from app.db.models import CallLog

    def check() -> bool:
        world.db.expire_all()
        return (
            world.db.query(CallLog)
            .filter(CallLog.call_sid.like("REALTIME-%"), CallLog.ended_at.isnot(None))
            .count()
            == count
        )

    return check


def _realtime_calls(world) -> list:
    from app.db.models import CallLog

    world.db.expire_all()
    return world.db.query(CallLog).filter(CallLog.call_sid.like("REALTIME-%")).all()


# ---------------------------------------------------------------------------
# C01 — his tennis, her groceries: two conversations, two records
# ---------------------------------------------------------------------------


def test_interleaved_lines_keep_their_own_exchanges_and_their_own_record(
    voice_world,
):
    """Saturday: Ravi asks about the cricket while Sarah checks the plumber.

    Their turns interleave — his, hers, his again — on two live lines at
    once. When both hang up, the house has two separate records: two call
    logs whose summaries carry only their own speaker's words, with his
    two exchanges counted on his log and her one on hers, and one topic
    memory each pointing at its own call.
    """

    world = voice_world
    world.seed_ravi()
    world.disable_brain()
    fakes = two_upstreams(world)

    with world.connect() as ws_a:
        assert _wait_until(lambda: _opened(fakes[0]))
        world.settle_open(fakes[0])  # his card lands before she opens hers
        with world.connect() as ws_b:
            assert _wait_until(lambda: _opened(fakes[1]))
            world.settle_open(fakes[1])

            # -- his turn ------------------------------------------------
            fakes[0].feed(user_said("is the cricket on channel four tonight?"))
            assert ws_a.receive_json() == {
                "type": "user_transcript",
                "text": "is the cricket on channel four tonight?",
            }
            fakes[0].feed(model_said("Let me think about that."))
            assert ws_a.receive_json() == {
                "type": "assistant_transcript_delta",
                "text": "Let me think about that.",
            }
            fakes[0].feed(done())
            # one shared in-memory store: serialize the two lines' writes
            assert _wait_until(_mirrored(world, "is the cricket on channel four tonight?"))

            # -- her turn, on the other line -----------------------------
            fakes[1].feed(user_said("did the plumber confirm Thursday morning?"))
            assert ws_b.receive_json() == {
                "type": "user_transcript",
                "text": "did the plumber confirm Thursday morning?",
            }
            fakes[1].feed(model_said("I have that written down."))
            assert ws_b.receive_json() == {
                "type": "assistant_transcript_delta",
                "text": "I have that written down.",
            }
            fakes[1].feed(done())
            assert _wait_until(_mirrored(world, "did the plumber confirm Thursday morning?"))

            # -- back to him ---------------------------------------------
            fakes[0].feed(user_said("and what time does it start?"))
            assert ws_a.receive_json() == {
                "type": "user_transcript",
                "text": "and what time does it start?",
            }
            fakes[0].feed(done())
            assert _wait_until(_mirrored(world, "and what time does it start?"))

            ws_a.send_json({"type": "end"})
            assert _wait_until(_finalized(world, 1))
            ws_b.send_json({"type": "end"})
            assert _wait_until(_finalized(world, 2))

    assert _wait_until(lambda: realtime._active_bridges == 0)
    assert fakes[0].closed is True and fakes[1].closed is True

    # neither socket ever carried the other line's words
    assert "plumber" not in json.dumps(fakes[0].sent)
    assert "cricket" not in json.dumps(fakes[1].sent)

    calls = _realtime_calls(world)
    assert len(calls) == 2
    assert len({call.call_sid for call in calls}) == 2
    his = next(c for c in calls if "cricket" in (c.summary or ""))
    hers = next(c for c in calls if "plumber" in (c.summary or ""))
    assert his.id != hers.id
    assert "2 exchange(s)" in his.summary  # his two turns, and only his
    assert "and what time does it start?" in his.summary
    assert "plumber" not in his.summary
    assert "1 exchange(s)" in hers.summary
    assert "cricket" not in hers.summary

    from app.memory.models import ConversationMemory

    memories = (
        world.db.query(ConversationMemory)
        .filter(
            ConversationMemory.memory_type == "topic",
            ConversationMemory.source == "realtime",
        )
        .all()
    )
    assert len(memories) == 2
    assert {memory.call_log_id for memory in memories} == {his.id, hers.id}
    by_call = {memory.call_log_id: memory.content for memory in memories}
    assert "cricket" in by_call[his.id] and "plumber" not in by_call[his.id]
    assert "plumber" in by_call[hers.id] and "cricket" not in by_call[hers.id]


# ---------------------------------------------------------------------------
# C02 — two lookups in the air at the same moment
# ---------------------------------------------------------------------------


def test_two_lookups_in_flight_land_on_the_right_line(voice_world):
    """He asks about Alcaraz; she asks about the pharmacy's opening hours.

    Both questions are with the research assistant at the same instant.
    Each ack goes back down its own socket, each finished note is injected
    into its own conversation, the sources chip appears only on the screen
    that asked for it, and one line's deferred nudge does not fire on the
    other line's response.done.
    """

    world = voice_world
    world.seed_ravi()
    gate = threading.Event()
    world.enable_search(
        {
            "Alcaraz": WorkerResult(
                kind="search",
                question="when does Alcaraz play next?",
                speech="Alcaraz plays the semifinal on Friday night.",
                sources=(Source(label="US Open schedule", url="https://example.org/uso"),),
            ),
            "pharmacy": WorkerResult(
                kind="search",
                question="what time does the pharmacy shut on Saturday?",
                speech="That pharmacy shuts at five on Saturdays.",
            ),
        },
        gate=gate,
    )
    fakes = two_upstreams(world)
    try:
        with world.connect() as ws_a:
            assert _wait_until(lambda: _opened(fakes[0]))
            world.settle_open(fakes[0])
            with world.connect() as ws_b:
                assert _wait_until(lambda: _opened(fakes[1]))
                world.settle_open(fakes[1])
                assert _response_creates(fakes[0]) == 1  # greetings only
                assert _response_creates(fakes[1]) == 1

                fakes[0].feed(
                    done(look_call("when does Alcaraz play next?", call_id="his-look"))
                )
                fakes[1].feed(
                    done(
                        look_call(
                            "what time does the pharmacy shut on Saturday?",
                            call_id="her-look",
                        )
                    )
                )
                assert _wait_until(lambda: len(world.search_calls) == 2)
                assert _wait_until(lambda: _function_outputs(fakes[0]))
                assert _wait_until(lambda: _function_outputs(fakes[1]))

                # each ack answered its own call_id on its own socket
                his_ack = _function_outputs(fakes[0])[0]
                her_ack = _function_outputs(fakes[1])[0]
                assert his_ack["item"]["call_id"] == "his-look"
                assert her_ack["item"]["call_id"] == "her-look"
                assert json.loads(his_ack["item"]["output"])["status"] == "working"
                assert json.loads(her_ack["item"]["output"])["status"] == "working"
                assert len(_function_outputs(fakes[0])) == 1
                assert len(_function_outputs(fakes[1])) == 1
                assert _response_creates(fakes[0]) == 2  # one ack nudge each
                assert _response_creates(fakes[1]) == 2

                gate.set()  # both answers come back together
                assert _wait_until(lambda: lookup_notes(fakes[0]))
                assert _wait_until(lambda: lookup_notes(fakes[1]))

                his_note = lookup_notes(fakes[0])[0]
                her_note = lookup_notes(fakes[1])[0]
                assert '"when does Alcaraz play next?"' in his_note
                assert "Alcaraz plays the semifinal on Friday night." in his_note
                assert "pharmacy" not in his_note
                assert '"what time does the pharmacy shut on Saturday?"' in her_note
                assert "That pharmacy shuts at five on Saturdays." in her_note
                assert "Alcaraz" not in her_note

                # only the line that asked gets the sources chip
                assert ws_a.receive_json() == {
                    "type": "sources",
                    "items": [
                        {
                            "label": "US Open schedule",
                            "url": "https://example.org/uso",
                            "fresh_as_of": "",
                        }
                    ],
                }

                # nudge accounting is per line: both notes are deferred
                # behind their own ack response, and only the line whose
                # response.done arrives releases its own.
                assert _response_creates(fakes[0]) == 2
                assert _response_creates(fakes[1]) == 2
                fakes[0].feed(done())
                assert _wait_until(lambda: _response_creates(fakes[0]) == 3)
                time.sleep(0.3)  # assert her line did NOT nudge
                assert _response_creates(fakes[1]) == 2
                fakes[1].feed(done())
                assert _wait_until(lambda: _response_creates(fakes[1]) == 3)

                # her browser never saw his chips: the next frame is speech
                fakes[1].feed(model_said("Five o'clock, then."))
                assert ws_b.receive_json() == {
                    "type": "assistant_transcript_delta",
                    "text": "Five o'clock, then.",
                }

                ws_b.send_json({"type": "end"})
            ws_a.send_json({"type": "end"})
    finally:
        gate.set()

    assert _wait_until(lambda: realtime._active_bridges == 0)
    assert sorted(world.search_calls) == [
        "what time does the pharmacy shut on Saturday?",
        "when does Alcaraz play next?",
    ]
    # sources are browser-only on both lines
    assert not any("https://example.org/uso" in text for text in _system_items(fakes[0]))
    assert not any("https://example.org/uso" in text for text in _system_items(fakes[1]))


# ---------------------------------------------------------------------------
# C03 — one screen, two conversations
# ---------------------------------------------------------------------------


def test_the_dad_screen_is_one_row_and_the_last_line_to_speak_owns_it(voice_world):
    """His tablet shows the room's screen. Her phone talks into the same row.

    The live Dad screen is a single row (``SCREEN_STATE_ROW_ID``), so two
    live lines are two writers to one surface: the last exchange to finish
    wins, whoever spoke it.

    DESIGN GAP: the screen mirror carries no line identity. While Sarah's
    phone conversation is live, the words on Ravi's tablet are hers — he
    can watch "did the plumber confirm Thursday morning?" appear as if he
    had said it, and his own last exchange is gone from the screen until
    he speaks again. Nothing here is wrong per row; the open question is
    whether the household screen should belong to the patient's line only
    (or name its speaker) once a second line is a normal thing. Pinned as
    the current behaviour, not filed as a bug.
    """

    world = voice_world
    world.seed_ravi()
    world.disable_brain()
    fakes = two_upstreams(world)

    from app.parker.screen import ScreenState, get_screen_state

    with world.connect() as ws_a:
        assert _wait_until(lambda: _opened(fakes[0]))
        world.settle_open(fakes[0])
        with world.connect() as ws_b:
            assert _wait_until(lambda: _opened(fakes[1]))
            world.settle_open(fakes[1])

            fakes[0].feed(user_said("put the tennis on"))
            assert ws_a.receive_json()["type"] == "user_transcript"
            fakes[0].feed(model_said("I'll see what I can do."))
            assert ws_a.receive_json()["type"] == "assistant_transcript_delta"
            fakes[0].feed(done())
            assert _wait_until(_mirrored(world, "put the tennis on"))

            # her phone writes over his row while his line is still live
            fakes[1].feed(user_said("did the plumber confirm Thursday morning?"))
            assert ws_b.receive_json()["type"] == "user_transcript"
            fakes[1].feed(model_said("Thursday at nine, yes."))
            assert ws_b.receive_json()["type"] == "assistant_transcript_delta"
            fakes[1].feed(done())
            assert _wait_until(_mirrored(world, "did the plumber confirm Thursday morning?"))

            world.db.expire_all()
            state = get_screen_state(world.db)
            assert state.speech == "Thursday at nine, yes."  # hers, on his screen
            assert world.db.query(ScreenState).count() == 1

            # and he takes it back simply by speaking again
            fakes[0].feed(user_said("never mind, is it raining?"))
            assert ws_a.receive_json()["type"] == "user_transcript"
            fakes[0].feed(model_said("Dry all evening."))
            assert ws_a.receive_json()["type"] == "assistant_transcript_delta"
            fakes[0].feed(done())
            assert _wait_until(_mirrored(world, "never mind, is it raining?"))

            ws_b.send_json({"type": "end"})
        ws_a.send_json({"type": "end"})

    assert _wait_until(lambda: realtime._active_bridges == 0)
    world.db.expire_all()
    assert world.db.query(ScreenState).count() == 1  # never two screens
    final = get_screen_state(world.db)
    assert final.heard == "never mind, is it raining?"
    assert final.speech == "Dry all evening."
    assert final.kind == "answer"


# ---------------------------------------------------------------------------
# C04 — the third tap, and the slot that frees up
# ---------------------------------------------------------------------------


def test_the_third_tap_is_refused_then_admitted_once_a_line_hangs_up(voice_world):
    """Anil tries the spare-room tablet while both lines are busy.

    He gets an honest "already running" and a clean close — not a broken
    socket, not a queue. When Ravi finishes, the slot is genuinely freed:
    Anil's next tap opens a real bridge with its own upstream socket, its
    own greeting, and its own call log.
    """

    world = voice_world
    world.seed_ravi()
    world.disable_brain()
    fakes = two_upstreams(world, count=3)

    with world.connect() as ws_a:
        assert _wait_until(lambda: _opened(fakes[0]))
        world.settle_open(fakes[0])
        with world.connect() as ws_b:
            assert _wait_until(lambda: _opened(fakes[1]))
            world.settle_open(fakes[1])
            assert realtime._active_bridges == 2

            with world.connect() as ws_c:
                refused = ws_c.receive_json()
            assert refused == {
                "type": "unavailable",
                "text": "A live conversation is already running.",
            }
            # the refusal never reached OpenAI: no third socket was opened
            assert fakes[2].sent == []
            assert realtime._active_bridges == 2

            # Ravi hangs up. The slot frees when the handler returns, not
            # when the client's context manager exits — so the spare room
            # can be admitted while Sarah's line is still up.
            fakes[0].feed(user_said("that's me done, thank you"))
            assert ws_a.receive_json()["type"] == "user_transcript"
            fakes[0].feed(done())
            assert _wait_until(_mirrored(world, "that's me done, thank you"))
            ws_a.send_json({"type": "end"})
            assert _wait_until(lambda: realtime._active_bridges == 1)
            assert fakes[0].closed is True and fakes[1].closed is False

            # ...and now Anil is admitted for real, on his own socket
            with world.connect() as ws_c:
                assert _wait_until(lambda: _opened(fakes[2]))
                world.settle_open(fakes[2])
                assert fakes[2].sent[0]["type"] == "session.update"
                assert realtime._active_bridges == 2
                fakes[2].feed(user_said("just testing it from in here"))
                assert ws_c.receive_json() == {
                    "type": "user_transcript",
                    "text": "just testing it from in here",
                }
                fakes[2].feed(done())
                assert _wait_until(_mirrored(world, "just testing it from in here"))
                ws_c.send_json({"type": "end"})
                assert _wait_until(lambda: realtime._active_bridges == 1)

            ws_b.send_json({"type": "end"})

    assert _wait_until(lambda: realtime._active_bridges == 0)
    assert fakes[1].closed is True and fakes[2].closed is True
    calls = _realtime_calls(world)
    assert len(calls) == 3  # three admitted lines, three logs; the refusal left none
    assert len({call.call_sid for call in calls}) == 3


# ---------------------------------------------------------------------------
# C05 — one line's write fails while the other stages
# ---------------------------------------------------------------------------


def test_a_failed_write_on_one_line_never_costs_the_other_line_its_action(
    voice_world, monkeypatch
):
    """His reminder hits a wedged write; her reminder must still stage.

    Ravi asks Parker to remind him about the bins and the capture write
    blows up. Parker answers his tool call honestly (nothing is waiting on
    his screen) — and Sarah's plumber reminder, proposed on the other live
    line moments later, stages normally through the same pipeline. One
    line's failed action does not poison the other's.

    DESIGN GAP (scope note): a *per-line* dead store is not expressible
    here. ``realtime._db_session_factory`` is a module-global seam with no
    bridge identity, so poisoning "his store" poisons both lines — see the
    dropped C05b note in this file's report. What is real, and what this
    test pins, is per-proposal failure isolation: the store's failure mode
    that actually distinguishes the two lines is the write each line asks
    for. (Writes are fed one at a time on purpose: the test engine shares
    one SQLite connection, per the harness's settle_open note.)
    """

    world = voice_world
    world.seed_ravi()
    world.disable_brain()

    from app.conversation import tools as tools_module

    real_execute_tool = tools_module.execute_tool

    def wedged_for_the_bins(db, call_log_id, tool_name, arguments):
        if "bins" in str(arguments.get("intent_text", "")):
            raise RuntimeError("database is locked")
        return real_execute_tool(db, call_log_id, tool_name, arguments)

    monkeypatch.setattr(tools_module, "execute_tool", wedged_for_the_bins)

    fakes = two_upstreams(world)
    with world.connect() as ws_a:
        assert _wait_until(lambda: _opened(fakes[0]))
        world.settle_open(fakes[0])
        with world.connect() as ws_b:
            assert _wait_until(lambda: _opened(fakes[1]))
            world.settle_open(fakes[1])

            fakes[0].feed(
                done(
                    propose_call(
                        {
                            "action_type": "reminder",
                            "label": "bins",
                            "subject": "bins before the walk",
                            "intent_text": "remind him to put the bins out",
                        },
                        call_id="his-prop",
                    )
                )
            )
            assert _wait_until(lambda: _function_outputs(fakes[0]))
            his = _function_outputs(fakes[0])[0]
            assert his["item"]["call_id"] == "his-prop"
            assert json.loads(his["item"]["output"])["status"] == "rejected"
            assert "could not save" in his["item"]["output"]

            fakes[1].feed(
                done(
                    propose_call(
                        {
                            "action_type": "reminder",
                            "label": "plumber Thursday",
                            "subject": "plumber on Thursday morning",
                            "intent_text": "remind him the plumber comes Thursday morning",
                        },
                        call_id="her-prop",
                    )
                )
            )
            assert _wait_until(lambda: _function_outputs(fakes[1]))
            hers = _function_outputs(fakes[1])[0]
            assert hers["item"]["call_id"] == "her-prop"
            assert json.loads(hers["item"]["output"])["status"] == "staged"
            assert ws_b.receive_json() == {
                "type": "proposal_staged",
                "label": "plumber Thursday",
            }

            # his line got no staged frame and no second answer
            fakes[0].feed(model_said("I couldn't save that one, sorry."))
            assert ws_a.receive_json() == {
                "type": "assistant_transcript_delta",
                "text": "I couldn't save that one, sorry.",
            }
            assert len(_function_outputs(fakes[0])) == 1

            ws_b.send_json({"type": "end"})
        ws_a.send_json({"type": "end"})

    assert _wait_until(lambda: realtime._active_bridges == 0)

    from app.db.models import StagedAction

    world.db.expire_all()
    staged = world.db.query(StagedAction).all()
    assert len(staged) == 1  # exactly hers
    assert staged[0].action_type == "reminder"
    assert "plumber" in (staged[0].action_payload or "")
    assert "bins" not in (staged[0].action_payload or "")


# ---------------------------------------------------------------------------
# C06 — both context workers building a card at the same moment
# ---------------------------------------------------------------------------


def test_two_context_cards_race_and_each_lands_on_its_own_conversation(
    voice_world, monkeypatch
):
    """Both lines open within a second of each other, both loading context.

    Two context workers are in flight simultaneously. Each line gets
    exactly one card, the two cards are different objects (no result is
    injected into both conversations, none is injected twice), and — as
    always — a card nudges nothing, on either line.

    The worker is stubbed so the two results are *distinguishable*: the
    real card is identical text on both lines, which would make a
    cross-injection bug invisible. Scope note: which bridge owns which
    result is deliberately not asserted — the worker seam carries no
    bridge identity, so ownership is not observable from here. What is
    observable, and what breaks if the injection path ever shared state,
    is the one-each/never-the-same-one contract below.
    """

    world = voice_world
    world.seed_ravi()
    world.disable_brain()

    gate = threading.Event()
    lock = threading.Lock()
    entered: list[int] = []

    def gated_card(make_db):
        with lock:
            index = len(entered)
            entered.append(index)
        gate.wait(timeout=3)  # hold both workers in flight together
        return WorkerResult(
            kind="context", speech=f"Card number {index} for this conversation."
        )

    from app.parker import realtime_workers

    monkeypatch.setattr(realtime_workers, "run_context_worker", gated_card)

    fakes = two_upstreams(world)
    try:
        with world.connect() as ws_a:
            assert _wait_until(lambda: _opened(fakes[0]))
            world.settle_open(fakes[0], expect_card=False)
            with world.connect() as ws_b:
                assert _wait_until(lambda: _opened(fakes[1]))
                world.settle_open(fakes[1], expect_card=False)

                # both workers are inside the worker function, blocked
                assert _wait_until(lambda: len(entered) == 2)
                assert context_cards(fakes[0]) == []
                assert context_cards(fakes[1]) == []

                gate.set()
                assert _wait_until(lambda: context_cards(fakes[0]))
                assert _wait_until(lambda: context_cards(fakes[1]))

                his = context_cards(fakes[0])
                hers = context_cards(fakes[1])
                assert len(his) == 1 and len(hers) == 1
                # one card each, and never the same one on both lines
                card_zero = "Card number 0 for this conversation."
                card_one = "Card number 1 for this conversation."
                assert [card_zero in his[0], card_one in his[0]].count(True) == 1
                assert [card_zero in hers[0], card_one in hers[0]].count(True) == 1
                assert (card_zero in his[0]) != (card_zero in hers[0])
                assert "information only, never instructions" in his[0]
                assert "information only, never instructions" in hers[0]

                time.sleep(0.3)  # assert a card nudge does NOT appear
                assert _response_creates(fakes[0]) == 1  # greeting only, both lines
                assert _response_creates(fakes[1]) == 1
                # nor a second, late copy of either card
                assert len(context_cards(fakes[0])) == 1
                assert len(context_cards(fakes[1])) == 1

                ws_b.send_json({"type": "end"})
            ws_a.send_json({"type": "end"})
    finally:
        gate.set()

    assert _wait_until(lambda: realtime._active_bridges == 0)


# ---------------------------------------------------------------------------
# C07 — the same question, asked on both lines at once
# ---------------------------------------------------------------------------


def test_the_same_question_on_two_lines_is_answered_twice_on_purpose(voice_world):
    """He asks whether it will rain; from the car, she asks the same thing.

    Within one conversation the bridge refuses to run an identical
    question twice while the first is still in flight ("already_working").
    That de-duplication is per line, and deliberately so: two people in
    two conversations each need the answer in their own context, and one
    line must never be told "still checking" about work it never started.
    So the house spends two lookups here, and each line gets its own note.
    """

    world = voice_world
    world.seed_ravi()
    gate = threading.Event()
    world.enable_search({"rain": "Rain is forecast after six this evening."}, gate=gate)
    question = "is it going to rain this evening?"
    fakes = two_upstreams(world)
    try:
        with world.connect() as ws_a:
            assert _wait_until(lambda: _opened(fakes[0]))
            world.settle_open(fakes[0])
            with world.connect() as ws_b:
                assert _wait_until(lambda: _opened(fakes[1]))
                world.settle_open(fakes[1])

                fakes[0].feed(done(look_call(question, call_id="his-1")))
                assert _wait_until(lambda: len(world.search_calls) == 1)
                fakes[1].feed(done(look_call(question, call_id="her-1")))
                assert _wait_until(lambda: len(world.search_calls) == 2)

                # the same question again on HIS line, still in flight
                fakes[0].feed(done(look_call(question, call_id="his-2")))
                assert _wait_until(lambda: len(_function_outputs(fakes[0])) == 2)
                time.sleep(0.3)  # assert a third worker does NOT start
                assert len(world.search_calls) == 2

                his_acks = [
                    json.loads(o["item"]["output"])["status"]
                    for o in _function_outputs(fakes[0])
                ]
                her_acks = [
                    json.loads(o["item"]["output"])["status"]
                    for o in _function_outputs(fakes[1])
                ]
                assert his_acks == ["working", "already_working"]
                assert her_acks == ["working"]  # her line was never "still checking"

                gate.set()
                assert _wait_until(lambda: lookup_notes(fakes[0]))
                assert _wait_until(lambda: lookup_notes(fakes[1]))
                assert len(lookup_notes(fakes[0])) == 1  # one worker, one note
                assert len(lookup_notes(fakes[1])) == 1
                for note in lookup_notes(fakes[0]) + lookup_notes(fakes[1]):
                    assert f'"{question}"' in note
                    assert "Rain is forecast after six this evening." in note

                ws_b.send_json({"type": "end"})
            ws_a.send_json({"type": "end"})
    finally:
        gate.set()

    assert _wait_until(lambda: realtime._active_bridges == 0)
    assert world.search_calls == [question, question]


# ---------------------------------------------------------------------------
# C08 — the guard trips on one line only
# ---------------------------------------------------------------------------


def test_a_medical_trip_on_one_line_leaves_the_other_line_talking(voice_world):
    """His line drifts into dosage advice; hers is mid-sentence about dinner.

    The post-hoc guard cancels *his* response, flushes *his* tablet, and
    speaks the redirect there. Her phone must not go silent, her socket
    must not receive a cancel, and once his response ends the guard state
    resets on his line alone.
    """

    world = voice_world
    world.seed_ravi()
    world.disable_brain()
    fakes = two_upstreams(world)

    from app.brain.guard import MEDICAL_BOUNDARY_REDIRECT

    with world.connect() as ws_a:
        assert _wait_until(lambda: _opened(fakes[0]))
        world.settle_open(fakes[0])
        with world.connect() as ws_b:
            assert _wait_until(lambda: _opened(fakes[1]))
            world.settle_open(fakes[1])

            fakes[0].feed(model_said("You should take an extra dose tonight."))
            assert ws_a.receive_json() == {"type": "clear"}
            assert ws_a.receive_json() == {
                "type": "guard_redirect",
                "text": MEDICAL_BOUNDARY_REDIRECT,
            }
            # the rest of the cancelled sentence never reaches his tablet
            fakes[0].feed(model_said(" and again in the morning."))

            # her line is untouched: no clear, no redirect, just her words
            fakes[1].feed(model_said("Dinner is at seven, then."))
            assert ws_b.receive_json() == {
                "type": "assistant_transcript_delta",
                "text": "Dinner is at seven, then.",
            }

            his_cancels = [e for e in fakes[0].sent if e["type"] == "response.cancel"]
            her_cancels = [e for e in fakes[1].sent if e["type"] == "response.cancel"]
            assert len(his_cancels) == 1
            assert her_cancels == []

            # his guard state resets with his response, and only his
            fakes[0].feed(done())
            fakes[0].feed(model_said("The tennis is on at seven."))
            assert ws_a.receive_json() == {
                "type": "assistant_transcript_delta",
                "text": "The tennis is on at seven.",
            }

            ws_b.send_json({"type": "end"})
        ws_a.send_json({"type": "end"})

    assert _wait_until(lambda: realtime._active_bridges == 0)
    assert len([e for e in fakes[1].sent if e["type"] == "response.cancel"]) == 0
