"""Scenario gauntlet — his speech, and the timing of it.

Dimension: what Parkinson's speech does to a full-duplex line. He cuts in
mid-sentence, he repeats himself, he changes the subject before the answer
lands, his voice catches on a word, and sometimes the transcriber hears
nothing at all. Every one of those is a timer-vs-event race inside the
bridge, so these tests interleave upstream events by hand rather than
pre-scripting them.

Each test is one Ravi story asserting the BRIDGE CONTRACT only — what is
injected upstream, what nudges fire, what reaches the browser, what lands
in the DB. Never what gpt-realtime would say.
"""

from __future__ import annotations

import json
import threading
import time

from app.brain.adapter import Source
from scenario_harness import *  # noqa: F401,F403


# ---------------------------------------------------------------------------
# Small local readers (the harness owns everything shared)
# ---------------------------------------------------------------------------


def _acks(fake) -> list[dict]:
    return [json.loads(out["item"]["output"]) for out in _function_outputs(fake)]


def _screen(db):
    """Re-read the single mirror row, seeing the threadpool thread's write."""

    from app.parker.screen import get_screen_state

    db.expire_all()
    return get_screen_state(db)


def test_he_cuts_in_halfway_and_the_owed_nudge_waits_for_his_mouth(voice_world):
    """"No — not the doubles." Ravi wants the singles, and says so mid-word.

    Parker is reading back the Alcaraz lookup when he cuts in. The half
    sentence he actually heard is what the screen keeps, and the nudge
    Parker still owes for the injected note stays parked until his mouth
    stops moving AND a response actually closes — a transcript landing is
    not, on its own, a safe point to speak.
    """

    world = voice_world
    world.seed_ravi()
    world.enable_search({"Alcaraz": "Alcaraz plays the semifinal on Friday evening."})
    fake = world.script([])
    with world.connect() as ws:
        fake.feed(done())  # settle the greeting
        fake.feed(done(look_call("when does Alcaraz play next")))
        assert _wait_until(lambda: len(lookup_notes(fake)) == 1)
        note = lookup_notes(fake)[0]
        assert '"when does Alcaraz play next"' in note  # echoed verbatim
        assert "seconds ago" in note
        creates_before = _response_creates(fake)
        assert creates_before == 2  # greeting + the ack's nudge

        fake.feed(model_said("I checked — Alcaraz plays the "))
        assert ws.receive_json() == {
            "type": "assistant_transcript_delta",
            "text": "I checked — Alcaraz plays the ",
        }
        fake.feed(speech_started())  # he cuts in
        assert ws.receive_json() == {"type": "clear"}

        # The interrupted narration closes. The half sentence is the
        # exchange — checked NOW, before a later done overwrites the mirror.
        fake.feed(done())
        assert _wait_until(
            lambda: (state := _screen(world.db)) is not None
            and state.speech == "I checked — Alcaraz plays the "
        )
        assert _screen(world.db).heard == ""

        time.sleep(0.25)  # every chance for a wrong nudge to fire
        assert _response_creates(fake) == 2  # he is still speaking

        fake.feed(user_said("not the doubles — the singles"))
        assert ws.receive_json() == {
            "type": "user_transcript",
            "text": "not the doubles — the singles",
        }
        time.sleep(0.25)
        assert _response_creates(fake) == 2  # transcription is not a retry point

        fake.feed(done())  # the real safe point
        assert _wait_until(lambda: _response_creates(fake) == 3)
        time.sleep(0.25)
        assert _response_creates(fake) == 3  # one deferred nudge, no double-fire
        ws.send_json({"type": "end"})


def test_barge_in_over_the_wrapup_with_an_answer_already_queued(
    voice_world, monkeypatch
):
    """The tennis question is still cooking when the room goes quiet.

    Parker asks "anything else, Ravi?" and right then he finds his words.
    The wrap-up ladder stands down, the answer that lands mid-barge-in
    waits its turn, and the two things Parker owes him collapse into one
    reply — Parker must not hang up on the man mid-sentence.
    """

    from app.parker import realtime

    world = voice_world
    world.seed_ravi()
    gate = threading.Event()
    world.enable_search({"tennis": "The semifinal is on Friday at seven."}, gate=gate)
    # The ladder is armed only once the lookup is in flight, so the rung
    # cannot fire between the greeting and the tool call.
    quick_timers(monkeypatch, wrapup=30.0, goodbye=5.0)
    fake = world.script([])
    with world.connect() as ws:
        fake.feed(done())
        fake.feed(done(look_call("what channel is the tennis on tonight")))
        assert _wait_until(lambda: _acks(fake))
        assert _acks(fake)[0]["status"] == "working"
        assert _response_creates(fake) == 2  # greeting + ack nudge (active)

        monkeypatch.setattr(realtime, "IDLE_WRAPUP_SECONDS", 0.15)
        assert _wait_until(lambda: any("anything else" in t for t in _system_items(fake)))
        # His voice will stand the rung down; freeze it so it cannot re-arm
        # and mint extra nudges during the settles below.
        monkeypatch.setattr(realtime, "IDLE_WRAPUP_SECONDS", 30.0)

        fake.feed(speech_started())
        assert ws.receive_json() == {"type": "clear"}
        gate.set()  # the answer lands while he is mid-word
        assert _wait_until(lambda: len(lookup_notes(fake)) == 1)

        fake.feed(user_said("no wait — the tennis, when's it on"))
        assert ws.receive_json() == {
            "type": "user_transcript",
            "text": "no wait — the tennis, when's it on",
        }
        time.sleep(0.3)
        assert _response_creates(fake) == 2  # two owed nudges, zero fired

        fake.feed(done())
        assert _wait_until(lambda: _response_creates(fake) == 3)
        time.sleep(0.3)
        assert _response_creates(fake) == 3  # wrap-up + result coalesced into one

        note = lookup_notes(fake)[0]
        assert '"what channel is the tennis on tonight"' in note
        assert "seconds ago" in note
        assert not any("goodbye" in t for t in _system_items(fake))

        fake.feed(model_said("Friday at seven."))
        assert ws.receive_json() == {
            "type": "assistant_transcript_delta",
            "text": "Friday at seven.",
        }  # nothing closing was queued ahead of it
        ws.send_json({"type": "end"})


def test_he_changes_the_subject_before_the_answer_lands(voice_world):
    """Alcaraz, then two breaths later: is Sarah still coming Sunday?

    The tennis answer arrives into a conversation that has moved on. The
    bridge does not judge that — it hands the model the question verbatim,
    its age, and explicit permission to let it go, and keeps the live
    exchange trail on the Sarah question that actually happened.
    """

    world = voice_world
    world.seed_ravi()
    world.gateway(lines=["Sarah confirmed Sunday lunch."])
    gate = threading.Event()
    world.enable_search(
        {
            "Alcaraz": WorkerResult(
                kind="search",
                question="when does Alcaraz play his next match",
                speech="Alcaraz plays his next match on Friday evening.",
                sources=(Source(label="US Open schedule", url="https://example.org/uso"),),
            )
        },
        gate=gate,
    )
    fake = world.script([])
    with world.connect() as ws:
        fake.feed(done())
        fake.feed(done(look_call("when does Alcaraz play his next match")))
        assert _wait_until(lambda: _acks(fake))
        assert _response_creates(fake) == 2

        fake.feed(user_said("actually — is Sarah still coming Sunday?"))
        assert ws.receive_json() == {
            "type": "user_transcript",
            "text": "actually — is Sarah still coming Sunday?",
        }
        fake.feed(model_said("She is, Sunday lunch."))
        assert ws.receive_json() == {
            "type": "assistant_transcript_delta",
            "text": "She is, Sunday lunch.",
        }
        fake.feed(done())  # nothing owed: the ack nudge already fired
        assert _wait_until(
            lambda: (state := _screen(world.db)) is not None
            and state.heard == "actually — is Sarah still coming Sunday?"
        )

        gate.set()  # the answer arrives into the moved-on conversation
        assert _wait_until(lambda: len(lookup_notes(fake)) == 1)
        note = lookup_notes(fake)[0]
        assert '"when does Alcaraz play his next match"' in note
        assert "seconds ago" in note
        assert "clearly moved past it" in note  # permission to let it go
        assert "never an instruction" in note
        assert "US Open schedule" not in note  # sources are browser-only

        chips = ws.receive_json()
        assert chips["type"] == "sources"
        assert chips["items"][0]["label"] == "US Open schedule"

        assert _wait_until(lambda: _response_creates(fake) == 3)
        time.sleep(0.25)
        assert _response_creates(fake) == 3
        ws.send_json({"type": "end"})


def test_the_same_question_said_two_different_ways(voice_world):
    """"When does Alcaraz play?" then "what time is the Alcaraz match?"

    Unsure he was understood, he rephrases. Exact-match dedup cannot see
    those as one question, so two workers genuinely run — the contract
    that matters is that both notes land and Parker still only asks the
    model to speak once.

    DESIGN GAP: a rephrase is a normal Parkinson's repair move, and the
    lane pays for it with a second (billed) lookup. Pinned as today's
    behaviour, not defended as the right one — semantic dedup would need
    a judgement the bridge deliberately does not make.
    """

    world = voice_world
    world.seed_ravi()
    gate = threading.Event()
    calls = world.enable_search(
        lambda q: WorkerResult(
            kind="search", question=q, speech="Friday evening, on the main court."
        ),
        gate=gate,
    )
    fake = world.script([])
    with world.connect() as ws:
        fake.feed(done())
        fake.feed(done(look_call("when does Alcaraz play", call_id="look-1")))
        assert _wait_until(lambda: len(_acks(fake)) == 1)
        # this done clears the active flag first, so its ack nudge fires too
        fake.feed(done(look_call("what time is the Alcaraz match", call_id="look-2")))
        assert _wait_until(lambda: len(_acks(fake)) == 2)
        assert [ack["status"] for ack in _acks(fake)] == ["working", "working"]

        assert _wait_until(lambda: len(calls) == 2)  # two real workers started
        assert sorted(calls) == sorted(
            ["when does Alcaraz play", "what time is the Alcaraz match"]
        )
        assert _response_creates(fake) == 3  # greeting + two ack nudges

        gate.set()
        assert _wait_until(lambda: len(lookup_notes(fake)) == 2)
        notes = lookup_notes(fake)
        for question in ("when does Alcaraz play", "what time is the Alcaraz match"):
            assert any(f'"{question}"' in note for note in notes)  # order not promised

        time.sleep(0.3)
        assert _response_creates(fake) == 3  # two results, an active response, zero nudges

        fake.feed(done())
        assert _wait_until(lambda: _response_creates(fake) == 4)
        time.sleep(0.3)
        assert _response_creates(fake) == 4  # the two deferred nudges collapsed into one
        ws.send_json({"type": "end"})


def test_the_mumble_that_transcribes_to_nothing(voice_world):
    """Late afternoon, off-medication: his words come back as an empty string.

    Parker asks him to say it again. That non-word must not reach his
    screen as a quote, and an accidental session of mumbles must not leave
    a fake memory behind for tomorrow's context card.
    """

    from app.db.models import CallLog
    from app.memory.models import ConversationMemory

    world = voice_world  # nothing seeded: the DB counts stay unambiguous
    fake = world.script([])
    with world.connect() as ws:
        fake.feed(done())
        fake.feed(user_said(""))  # transcription completed, empty
        fake.feed(model_said("Sorry Ravi, I missed that — say it once more?"))
        # The FIRST frame of the whole scenario: no user_transcript was sent
        # for the empty transcript, and no notice preceded it.
        assert ws.receive_json() == {
            "type": "assistant_transcript_delta",
            "text": "Sorry Ravi, I missed that — say it once more?",
        }
        fake.feed(done())
        assert _wait_until(
            lambda: (state := _screen(world.db)) is not None
            and "missed that" in state.speech
        )
        assert _screen(world.db).heard == ""  # the repair prompt quotes nothing

        # a sentinel frame proves nothing (notice/closing) slipped in behind
        fake.feed(model_said("Take your time."))
        assert ws.receive_json() == {
            "type": "assistant_transcript_delta",
            "text": "Take your time.",
        }
        ws.send_json({"type": "end"})

    time.sleep(0.25)  # let a wrong finalize every chance to write
    world.db.expire_all()
    assert world.db.query(ConversationMemory).count() == 0  # no user transcript, no memory
    call = world.db.query(CallLog).filter(CallLog.call_type == "realtime").one()
    assert call.summary is None  # the eager row exists; nothing was invented
    assert call.ended_at is None


def test_stop_kills_the_ramble_but_not_the_answer_he_wanted(voice_world):
    """He taps Stop while Parker is still explaining the whole forecast.

    Ravi asked whether it's warm enough for his walk before the ten
    o'clock heat. The rambling dies instantly — but the answer he actually
    wanted is still in flight behind the conversation, and it should still
    arrive.
    """

    world = voice_world
    world.seed_ravi()
    gate = threading.Event()
    world.enable_search({"walk": "Twenty-four degrees and clear before ten."}, gate=gate)
    fake = world.script([])
    with world.connect() as ws:
        fake.feed(done())
        fake.feed(done(look_call("is it warm enough for my walk before ten")))
        assert _wait_until(lambda: _acks(fake))
        assert _acks(fake)[0]["status"] == "working"

        fake.feed(model_said("Well, the forecast today has a band of cloud moving "))
        assert ws.receive_json() == {
            "type": "assistant_transcript_delta",
            "text": "Well, the forecast today has a band of cloud moving ",
        }

        ws.send_json({"type": "stop"})
        assert ws.receive_json() == {"type": "clear"}
        assert _wait_until(
            lambda: [e for e in fake.sent if e["type"] == "response.cancel"]
        )
        assert len([e for e in fake.sent if e["type"] == "response.cancel"]) == 1

        fake.feed(done())  # the cancelled response closes
        assert _wait_until(
            lambda: (state := _screen(world.db)) is not None
            and state.speech == "Well, the forecast today has a band of cloud moving "
        )  # the stopped half-answer is the exchange, not blanked

        gate.set()  # Stop did not cancel the background work
        assert _wait_until(lambda: len(lookup_notes(fake)) == 1)
        note = lookup_notes(fake)[0]
        assert '"is it warm enough for my walk before ten"' in note
        assert "seconds ago" in note
        assert _wait_until(lambda: _response_creates(fake) == 3)
        time.sleep(0.25)
        assert _response_creates(fake) == 3

        fake.feed(model_said("Twenty-four and clear — good walking weather."))
        assert ws.receive_json() == {
            "type": "assistant_transcript_delta",
            "text": "Twenty-four and clear — good walking weather.",
        }  # the line survived the Stop
        ws.send_json({"type": "end"})


def test_sunday_afternoon_sixty_questions_deep(voice_world):
    """Sarah is visiting, the tennis is on, and the line stays open an hour.

    Sixty little exchanges about songs, matches, his walk. The bridge
    remembers only the last fifty for the outcome trail and still writes
    ONE honest summary and ONE memory — not sixty — and mirrors the whole
    afternoon through a single overwritten screen row.
    """

    from app.db.models import CallLog
    from app.memory.models import ConversationMemory
    from app.parker.screen import ScreenState

    world = voice_world  # nothing seeded: one realtime call log, one memory
    questions = [
        "is Sarah still coming Sunday",
        "put on some Kishore Kumar",
        "did Alcaraz win",
        "what time should I walk",
    ] + [f"and another thing, number {i}" for i in range(4, 60)]
    assert len(questions) == 60

    fake = world.script([])
    with world.connect() as ws:
        fake.feed(done())  # settle the greeting
        for index, question in enumerate(questions):
            fake.feed(user_said(question))
            fake.feed(model_said(f"Answer {index}."))
            fake.feed(done())
        # Read every frame in order: sixty (question, answer) pairs and
        # nothing else — no notice, no closing, mid-marathon.
        for index, question in enumerate(questions):
            assert ws.receive_json() == {"type": "user_transcript", "text": question}
            assert ws.receive_json() == {
                "type": "assistant_transcript_delta",
                "text": f"Answer {index}.",
            }
        assert _wait_until(
            lambda: (state := _screen(world.db)) is not None
            and state.heard == questions[-1],
            timeout=10,
        )
        ws.send_json({"type": "end"})

    def finalized():
        world.db.expire_all()
        call = world.db.query(CallLog).filter(CallLog.call_type == "realtime").first()
        return call is not None and call.ended_at is not None

    assert _wait_until(finalized, timeout=10)
    call = world.db.query(CallLog).filter(CallLog.call_type == "realtime").one()
    assert call.summary.startswith("Live conversation, 50 exchange(s).")  # the cap held
    topics = call.summary.split("Asked about: ", 1)[1]
    assert len(topics) <= 300
    for question in questions[:4]:
        assert question in topics  # the cap keeps the EARLIEST exchanges

    memory = world.db.query(ConversationMemory).one()  # one memory, however long
    assert memory.memory_type == "topic"
    assert memory.source == "realtime"
    assert world.db.query(ScreenState).count() == 1  # one overwritten mirror row
    assert _screen(world.db).heard == questions[-1]

    from app.parker import realtime

    assert _wait_until(lambda: realtime._active_bridges == 0)  # drained cleanly


def test_he_asks_it_again_an_hour_later(voice_world):
    """The beach weather before his walk, and the identical question after it.

    The first lookup is long finished, so this is not a stutter to dedup —
    it is a fresh question about a changed world, and it must actually run
    again. (test_realtime.py pins the other half: two identical calls
    INSIDE one flight get working/already_working. This pins that the
    dedup is scoped to in-flight only.)
    """

    world = voice_world
    world.seed_ravi()
    calls = world.enable_search({"beach": "Clear and twenty-four at the beach."})
    fake = world.script([])
    with world.connect() as ws:
        fake.feed(done())  # settle the greeting
        assert _wait_until(lambda: _response_creates(fake) == 1)

        fake.feed(done(look_call("what's the weather at the beach", call_id="look-1")))
        assert _wait_until(lambda: len(lookup_notes(fake)) == 1)
        before_first_safe_point = _response_creates(fake)
        fake.feed(done())  # the deferred nudge fires — exactly one
        assert _wait_until(
            lambda: _response_creates(fake) == before_first_safe_point + 1
        )
        time.sleep(0.25)
        assert _response_creates(fake) == before_first_safe_point + 1

        # byte-identical, after completion: the in-flight key was discarded
        fake.feed(done(look_call("what's the weather at the beach", call_id="look-2")))
        assert _wait_until(lambda: len(_acks(fake)) == 2)
        assert [ack["status"] for ack in _acks(fake)] == ["working", "working"]
        assert _wait_until(lambda: len(calls) == 2)
        assert calls == [
            "what's the weather at the beach",
            "what's the weather at the beach",
        ]
        assert _wait_until(lambda: len(lookup_notes(fake)) == 2)
        for note in lookup_notes(fake):
            assert '"what\'s the weather at the beach"' in note
            assert "seconds ago" in note

        before_second_safe_point = _response_creates(fake)
        fake.feed(done())
        assert _wait_until(
            lambda: _response_creates(fake) == before_second_safe_point + 1
        )
        time.sleep(0.25)
        assert _response_creates(fake) == before_second_safe_point + 1
        ws.send_json({"type": "end"})


def test_a_stuttered_question_with_no_brain_to_ask(voice_world):
    """"When's — when's — when's the tennis" and nobody connected a brain.

    His voice catches on the first word and he says it three times; the
    model, unsure it was heard, fires three lookups in one turn. The
    family never connected a research brain, so all three get an honest
    "can't do that" — no phantom workers, and Parker does not chatter
    three times in a row about it.
    """

    world = voice_world
    world.disable_brain()
    world.seed_ravi()  # a card still builds in a brainless world
    fake = world.script([])
    with world.connect() as ws:
        fake.feed(done())  # settle the greeting
        assert _wait_until(lambda: _response_creates(fake) == 1)

        fake.feed(
            done(
                look_call("when's the tennis", call_id="l1"),
                look_call("when's the tennis", call_id="l2"),
                look_call("when's the tennis", call_id="l3"),
            )
        )
        assert _wait_until(lambda: len(_function_outputs(fake)) == 3)
        for ack in _acks(fake):
            assert ack["status"] == "unavailable"
            assert "say so honestly" in ack["detail"]

        assert world.search_calls == []  # nothing was ever spawned
        assert lookup_notes(fake) == []
        time.sleep(0.3)
        # the first ack's nudge fired; the other two coalesced behind it
        assert _response_creates(fake) == 2

        fake.feed(model_said("No tennis listings here, but I can ask Sarah."))
        assert ws.receive_json() == {
            "type": "assistant_transcript_delta",
            "text": "No tennis listings here, but I can ask Sarah.",
        }  # no notice: an unavailable lookup is a fact, not a hiccup

        assert _wait_until(lambda: context_cards(fake))  # the card degrades gracefully
        ws.send_json({"type": "end"})
