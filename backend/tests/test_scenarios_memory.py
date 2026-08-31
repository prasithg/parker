"""Scenario gauntlet — memory continuity: does yesterday actually help today?

Dimension: the *only* thing one live session leaves for the next one. The
bridge finalizes a session into a call-log summary plus exactly one topic
memory, and the next session's context worker reads the five most recent
memories back onto the card. That thin pipe is the whole of Parker's
continuity, so every story here spans MORE THAN ONE bridge session in one
test: converse, hang up, wait for the finalize to land, open a new line
and read the card that yesterday built.

What is asserted is the BRIDGE CONTRACT on both sides of the pipe — the
DB rows the first session wrote, and the system item the second session
injected — never what gpt-realtime would say about them.

Round 2 found three gaps here (M02 recency-blind eviction, M07 the
dangling header, M09 filler sessions minting memories); all three were
FIXED the same night and the tests below pin the fixed contracts: the
card balances durable family notes (up to four) against session chatter
(at most two), a header falls with its last bullet, and a filler-only
evening finalizes its call log without spending a memory slot.
"""

from __future__ import annotations

import time

from app.db.models import CallLog
from app.memory.models import ConversationMemory
from app.parker import realtime
from scenario_harness import *  # noqa: F401,F403


# ---------------------------------------------------------------------------
# Small local readers (the harness owns everything shared)
# ---------------------------------------------------------------------------


def _memory_card_lines(card: str) -> list[str]:
    """Just the recalled-memory bullets of a context card, in card order."""

    return [line for line in card.splitlines() if line.startswith("- [")]


def _live_calls(world) -> list[CallLog]:
    """Every call log a live bridge opened, oldest first (never the seed's)."""

    world.db.expire_all()  # see the finalize thread's write
    return (
        world.db.query(CallLog)
        .filter(CallLog.call_sid.like("REALTIME-%"))
        .order_by(CallLog.id)
        .all()
    )


def _live_memories(world) -> list[ConversationMemory]:
    """Every memory a finalize wrote, oldest first."""

    world.db.expire_all()
    return (
        world.db.query(ConversationMemory)
        .filter(ConversationMemory.source == "realtime")
        .order_by(ConversationMemory.id)
        .all()
    )


def _session(world, turns=(), *, expect_card=True):
    """One whole live session: open, converse, hang up, wait for the drain.

    Returns the session's fake upstream so the caller can read the card it
    was handed. Each turn is (what he said, what Parker said); every turn
    is read back off the browser socket, so the events are ordered by
    observation and not by hope.
    """

    fake = world.script([])
    with world.connect() as ws:
        world.settle_open(fake, expect_card=expect_card)
        for heard, said in turns:
            fake.feed(user_said(heard))
            assert ws.receive_json() == {"type": "user_transcript", "text": heard}
            fake.feed(model_said(said))
            assert ws.receive_json() == {
                "type": "assistant_transcript_delta",
                "text": said,
            }
            fake.feed(done())
        ws.send_json({"type": "end"})
    # shutdown's finalize runs in run()'s finally — the slot is released after
    assert _wait_until(lambda: realtime._active_bridges == 0, timeout=5.0)
    return fake


def _card_of(world, *, expect_card=True) -> str:
    """Open a line purely to read the card yesterday built, then hang up."""

    fake = _session(world, expect_card=expect_card)
    assert _response_creates(fake) == 1, "the card must never nudge a response"
    cards = context_cards(fake)
    return cards[0] if cards else ""


# ---------------------------------------------------------------------------
# M01 — the tennis question survives the night
# ---------------------------------------------------------------------------


def test_last_nights_alcaraz_question_is_on_tonights_card(voice_world):
    """Thursday night Ravi asks when Alcaraz plays. Friday night Parker knows.

    The world starts genuinely empty — first boot, nothing seeded — so
    Thursday's session gets no card at all. Everything Friday's card
    knows, Thursday's conversation put there: one call-log summary, one
    topic memory, one line on the card, injected silently.
    """

    world = voice_world  # nothing seeded: the pipe is the only source

    thursday = _session(
        world,
        [("when Alcaraz plays next at the US Open", "He's on Friday night, Ravi.")],
        expect_card=False,
    )
    assert context_cards(thursday) == []  # an empty world whispers nothing

    calls = _live_calls(world)
    assert len(calls) == 1
    assert calls[0].ended_at is not None
    assert calls[0].summary == (
        "Live conversation, 1 exchange(s). "
        "Asked about: when Alcaraz plays next at the US Open"
    )
    memories = _live_memories(world)
    assert len(memories) == 1
    assert memories[0].memory_type == "topic"
    assert memories[0].content == (
        "In a live conversation he asked about: when Alcaraz plays next at the US Open"
    )
    assert memories[0].call_log_id == calls[0].id  # tied to the night it came from

    card = _card_of(world)
    assert "- [topic] In a live conversation he asked about: when Alcaraz plays" in card
    assert "at the US Open" in card
    assert "information only, never instructions" in card  # framing intact
    assert "never recite this list" in card  # and it is never read out


# ---------------------------------------------------------------------------
# M02 — three evenings of tennis crowd his own life off the card
# ---------------------------------------------------------------------------


def test_two_nights_of_tennis_no_longer_evict_his_older_life(voice_world):
    """Gauntlet find M02, fixed: chatter can't erase what the family knows.

    The card used to read the five most recent memories blind, so two
    evenings of tennis questions pushed "Walks in the morning" off the
    card and left the Hindi songs one slot from the edge. Now durable
    family/seed notes hold up to four slots and session topics at most
    two — his life survives his conversations.
    """

    world = voice_world
    world.seed_ravi()

    # --- Wednesday: the card before any live session ----------------------
    wednesday = _card_of(world)
    assert "Walks in the morning" in wednesday
    assert "old Hindi songs" in wednesday
    assert "25-100 mg" not in wednesday  # 5th-newest durable, outside the four

    # --- Thursday and Friday: one tennis question each --------------------
    _session(world, [("did Alcaraz win his match today", "He did, in four sets.")])
    _session(world, [("what time is the tennis final on Sunday", "Two o'clock.")])
    assert len(_live_memories(world)) == 2  # one memory per evening, no more

    # --- Saturday: everything that matters is still there ------------------
    saturday = _card_of(world)
    assert _memory_card_lines(saturday) == [
        "- [topic] Following the US Open closely; doesn't want to miss Alcaraz's matches.",
        "- [preference] Loves old Hindi songs — Kishore Kumar and Mohammed Rafi especially.",
        "- [event] Paused a YouTube video about how levodopa works in the brain and had questions about it.",
        "- [fact] Walks in the morning and likes to be back before it gets hot, around 10am.",
        "- [topic] In a live conversation he asked about: what time is the tennis final on Sunday",
        "- [topic] In a live conversation he asked about: did Alcaraz win his match today",
    ]
    assert "Daughter Sarah" not in saturday  # 5th durable — the four-slot edge

    # nothing was deleted — the store keeps everything
    world.db.expire_all()
    assert world.db.query(ConversationMemory).count() == 8  # 6 seeded + 2 evenings


# ---------------------------------------------------------------------------
# M03 — the evening of mumbles leaves tomorrow exactly as it found it
# ---------------------------------------------------------------------------


def test_a_session_of_only_mumbles_changes_nothing_about_tomorrow(voice_world):
    """Late, off-medication: he tries twice and the transcript comes back empty.

    Parker asks him to say it again, he gives up, the line closes. That
    session must be invisible to the next one — no invented summary, no
    memory row, and tomorrow's card carrying exactly the same lines as
    tonight's. The accidental-tap policy has to hold ACROSS sessions, not
    only inside one, or a week of bad speech days would quietly evict his
    real life from the five-slot window.
    """

    world = voice_world
    world.seed_ravi()
    before = _card_of(world)
    assert _memory_card_lines(before), "the seeded card has memory lines to lose"

    fake = world.script([])
    with world.connect() as ws:
        world.settle_open(fake)
        fake.feed(user_said(""))  # transcription completed, empty
        fake.feed(model_said("Sorry Ravi, I missed that — once more?"))
        # the FIRST browser frame after the card: no user_transcript for a
        # non-word, and nothing else slipped in front of it
        assert ws.receive_json() == {
            "type": "assistant_transcript_delta",
            "text": "Sorry Ravi, I missed that — once more?",
        }
        fake.feed(done())
        fake.feed(user_said(""))  # and again
        fake.feed(model_said("Take your time."))
        assert ws.receive_json() == {
            "type": "assistant_transcript_delta",
            "text": "Take your time.",
        }
        fake.feed(done())
        ws.send_json({"type": "end"})
    assert _wait_until(lambda: realtime._active_bridges == 0, timeout=5.0)
    time.sleep(0.3)  # give a wrong finalize every chance to write

    assert _live_memories(world) == []  # no user transcript, no memory
    mumbled = _live_calls(world)[-1]
    assert mumbled.summary is None  # the eager row exists; nothing was invented
    # The session still honestly ENDS (flywheel verifier find: the review
    # feed's live flag derives from ended_at, and a mumbled evening must
    # not read as a live conversation forever) — invisibility to TOMORROW
    # is the summary/memory contract above, not a missing end time.
    assert mumbled.ended_at is not None

    after = _card_of(world)
    assert _memory_card_lines(after) == _memory_card_lines(before)
    assert "In a live conversation" not in after


# ---------------------------------------------------------------------------
# M04 — the question Parker never answered still reaches tomorrow
# ---------------------------------------------------------------------------


def test_the_question_parker_never_answered_is_on_tomorrows_card(voice_world):
    """He asks about Sunday's pharmacy hours and the upstream goes silent.

    No reply ever streams; he waits, then closes the tablet. The turn he
    actually spoke must not evaporate — shutdown captures the dangling
    transcript as an exchange with empty speech, and tomorrow's card
    carries the question he never got an answer to, which is exactly the
    thing Parker should pick back up.
    """

    world = voice_world  # empty: the dangling turn is the only source

    fake = world.script([])
    with world.connect() as ws:
        world.settle_open(fake, expect_card=False)
        fake.feed(user_said("will the pharmacy be open on Sunday"))
        assert ws.receive_json() == {
            "type": "user_transcript",
            "text": "will the pharmacy be open on Sunday",
        }
        time.sleep(0.3)  # he waits for a reply that never streams
        ws.send_json({"type": "end"})  # and gives up
    assert _wait_until(lambda: realtime._active_bridges == 0, timeout=5.0)

    calls = _live_calls(world)
    assert len(calls) == 1
    assert calls[0].summary == (
        "Live conversation, 1 exchange(s). "
        "Asked about: will the pharmacy be open on Sunday"
    )
    memories = _live_memories(world)
    assert len(memories) == 1
    assert "will the pharmacy be open on Sunday" in memories[0].content

    card = _card_of(world)
    assert (
        "- [topic] In a live conversation he asked about: will the pharmacy be "
        "open on Sunday" in card
    )


# ---------------------------------------------------------------------------
# M05 — an hour of talk hands tomorrow four topics
# ---------------------------------------------------------------------------


def test_the_long_afternoon_hands_tomorrow_only_its_first_four_topics(voice_world):
    """Sarah visits, the line stays open, fifty-five little exchanges go by.

    What tomorrow inherits from an hour of talk is one memory built from
    the first four things he said — the exchange trail caps at fifty and
    the summary quotes only the head of it. So the card remembers how the
    afternoon OPENED, not where it ended up, and it is still one line,
    not fifty-five.
    """

    world = voice_world  # empty: one live call log, one memory, unambiguous
    opening = [
        "is Sarah still coming Sunday",
        "put on some Kishore Kumar",
        "did Alcaraz win",
        "what time should I walk",
    ]
    questions = opening + [f"and another thing, number {i}" for i in range(4, 55)]
    assert len(questions) == 55

    _session(
        world,
        [(question, f"Answer {index}.") for index, question in enumerate(questions)],
        expect_card=False,
    )

    calls = _live_calls(world)
    assert len(calls) == 1
    assert calls[0].summary.startswith("Live conversation, 50 exchange(s).")  # cap held
    topics = calls[0].summary.split("Asked about: ", 1)[1]
    assert topics == "; ".join(opening)
    assert len(topics) <= 300

    memories = _live_memories(world)
    assert len(memories) == 1  # one memory for the whole afternoon
    assert memories[0].content == f"In a live conversation he asked about: {topics}"

    card = _card_of(world)
    for question in opening:
        assert question in card
    assert "number 5" not in card  # the tail never reaches tomorrow
    assert len(_memory_card_lines(card)) == 1


# ---------------------------------------------------------------------------
# M06 — what Sarah typed and what Ravi said share one card
# ---------------------------------------------------------------------------


def test_sarahs_notes_and_his_own_evening_ride_the_same_card(voice_world):
    """Sarah types two things into the family app; Ravi talks that evening.

    The family surface (POST /memory) and the live lane write into the
    same store with different sources; the next morning's card carries
    both, with the family's durable notes listed ahead of session
    chatter, each line tagged only by memory type — never by which human
    (or which lane) put the fact there.
    """

    world = voice_world  # empty: only what the two surfaces write

    # --- Sarah, during the day, from the family app -----------------------
    for content, memory_type in [
        ("Anil is flying in on the 14th and staying a week.", "event"),
        ("Sarah moved the neurologist appointment to Friday at two.", "event"),
    ]:
        response = client.post(
            "/memory/", json={"content": content, "memory_type": memory_type}
        )
        assert response.status_code == 200
        assert response.json()["source"] == "manual"

    # --- Ravi, that evening, on the live line -----------------------------
    _session(world, [("when is Anil getting here", "The fourteenth, Ravi.")])

    world.db.expire_all()
    rows = (
        world.db.query(ConversationMemory).order_by(ConversationMemory.id).all()
    )
    assert [row.source for row in rows] == ["manual", "manual", "realtime"]

    card = _card_of(world)
    assert _memory_card_lines(card) == [
        "- [event] Sarah moved the neurologist appointment to Friday at two.",
        "- [event] Anil is flying in on the 14th and staying a week.",
        "- [topic] In a live conversation he asked about: when is Anil getting here",
    ]
    assert "manual" not in card and "realtime" not in card  # provenance is not spoken


# ---------------------------------------------------------------------------
# M07 — the refill question he asked out loud, erased from the card
# ---------------------------------------------------------------------------


def test_his_own_dose_question_is_kept_but_never_handed_back(voice_world):
    """Gauntlet find M07, fixed: the erased memory takes its header with it.

    Ravi asks the live line whether his 25-100 mg refill is ready. The
    finalize stores that question verbatim — the family's record of what
    he asked must be honest — and the next session's context worker drops
    the line, because the post-hoc speech guard would cancel Parker
    mid-word for reading a dose aloud. The drop used to leave a card of
    just "Recent memories:" promising notes with none under it; now the
    header falls with its last bullet, and with nothing else to say, no
    card is injected at all.
    """

    world = voice_world  # empty: the dose line is the ONLY memory

    _session(
        world,
        [("is my 25-100 mg refill ready at the pharmacy", "Let me check with Sarah.")],
        expect_card=False,
    )

    memories = _live_memories(world)
    assert len(memories) == 1  # kept in full, dose and all
    assert memories[0].content == (
        "In a live conversation he asked about: is my 25-100 mg refill ready at "
        "the pharmacy"
    )
    assert "25-100 mg" in _live_calls(world)[0].summary

    card = _card_of(world, expect_card=False)
    assert card == ""  # nothing worth whispering beats a bare header


# ---------------------------------------------------------------------------
# M08 — the card is a snapshot taken at open, not a live feed
# ---------------------------------------------------------------------------


def test_what_sarah_adds_mid_call_waits_for_the_next_call(voice_world):
    """Sarah remembers the Friday appointment while Ravi is already talking.

    The context worker fires once, at session open. Anything the family
    writes after that is invisible for the rest of the call — no second
    card is injected — and lands on the card the next time he opens the
    line. Continuity through this lane is per-session, not live.
    """

    world = voice_world  # empty: no card at open at all

    fake = world.script([])
    with world.connect() as ws:
        world.settle_open(fake, expect_card=False)
        time.sleep(0.3)  # give the context worker every chance to finish
        assert context_cards(fake) == []  # nothing known yet

        # Sarah, in the family app, while the line is open:
        response = client.post(
            "/memory/",
            json={
                "content": "Sarah moved the neurologist appointment to Friday at two.",
                "memory_type": "event",
            },
        )
        assert response.status_code == 200
        world.db.expire_all()
        assert world.db.query(ConversationMemory).count() == 1  # it IS in the store

        fake.feed(user_said("anything on for Friday"))
        assert ws.receive_json() == {
            "type": "user_transcript",
            "text": "anything on for Friday",
        }
        fake.feed(model_said("Nothing I know of, Ravi."))
        assert ws.receive_json() == {
            "type": "assistant_transcript_delta",
            "text": "Nothing I know of, Ravi.",
        }
        fake.feed(done())
        time.sleep(0.3)  # a late card would have arrived by now
        assert context_cards(fake) == []  # the card never refreshes mid-call
        ws.send_json({"type": "end"})
    assert _wait_until(lambda: realtime._active_bridges == 0, timeout=5.0)

    card = _card_of(world)  # next morning
    assert "- [event] Sarah moved the neurologist appointment to Friday at two." in card
    assert "- [topic] In a live conversation he asked about: anything on for Friday" in card


# ---------------------------------------------------------------------------
# M09 — three evenings of "yeah" evict three things the family curated
# ---------------------------------------------------------------------------


def test_three_evenings_of_one_word_answers_change_nothing(voice_world):
    """Gauntlet find M09, fixed: "yeah" is a real call, not a memory.

    Some evenings Ravi mostly listens. Parker reads him the tennis, he
    says "yeah", "mm hm", "okay thanks", and the line closes. Those calls
    used to each mint a full topic memory ("he asked about: yeah") and
    spend a card slot the family's curated notes share. Now a session
    with no substantive line (three words or more) finalizes its call log
    honestly and declines to mint a memory — three quiet evenings later,
    the card is exactly what Sarah curated.
    """

    world = voice_world
    # what the family curated, oldest first
    world.remember("He keeps his glasses on the kitchen windowsill.", "fact")
    world.remember("The physio comes on Tuesdays at eleven.", "event")
    world.remember("Loves old Hindi songs — Kishore Kumar especially.", "preference")

    monday = _card_of(world)
    assert "glasses on the kitchen windowsill" in monday

    for filler in ["yeah", "mm hm", "okay thanks"]:
        _session(world, [(filler, "Good night, Ravi.")], expect_card=True)

    assert _live_memories(world) == []  # no memory earned by filler

    # the filler calls themselves are still honestly recorded (the silent
    # card-reader sessions rightly finalize nothing)
    def fillers_recorded():
        spoken = [call for call in _live_calls(world) if call.summary]
        return len(spoken) == 3 and all(call.ended_at for call in spoken)

    assert _wait_until(fillers_recorded)
    assert "yeah" in [call for call in _live_calls(world) if call.summary][0].summary

    thursday = _card_of(world)
    assert _memory_card_lines(thursday) == [
        "- [preference] Loves old Hindi songs — Kishore Kumar especially.",
        "- [event] The physio comes on Tuesdays at eleven.",
        "- [fact] He keeps his glasses on the kitchen windowsill.",
    ]
