"""Scenario gauntlet — long haul & storms: the quantity edges.

Dimension: what happens when the numbers get large. Ravi in a talkative
mood asks a dozen things in one sitting; his tremor sets the VAD off
fifteen times in twenty seconds; a session runs past the exchange cap; a
model streams one enormous turn; the tablet spits junk between every real
microphone chunk.

The contract everywhere here is the *orchestrator's* arithmetic, not the
model's words: exactly one response.create may be outstanding without a
response.done, every acked lookup produces exactly one note, the idle
ladder never escalates while he is speaking, and no bound (exchange cap,
card line cap) turns into a crash or a lie.

Each test asserts the BRIDGE CONTRACT only.
"""

from __future__ import annotations

import base64
import json
import threading
import time

from app.brain.guard import speech_violates_medical_boundary
from app.parker import realtime, realtime_workers
from scenario_harness import *  # noqa: F401,F403


# ---------------------------------------------------------------------------
# helpers local to this file (quantity bookkeeping)
# ---------------------------------------------------------------------------


def _acks(fake) -> list[dict]:
    return [json.loads(o["item"]["output"]) for o in _function_outputs(fake)]


def _audio_appends(fake) -> list[dict]:
    return [e for e in fake.sent if e["type"] == "input_audio_buffer.append"]


def _mirrored(world, heard: str):
    from app.parker.screen import get_screen_state

    def check() -> bool:
        world.db.expire_all()
        state = get_screen_state(world.db)
        return state is not None and state.heard == heard

    return check


# ---------------------------------------------------------------------------
# ST01 — a dozen questions, every worker gated, then the dam breaks
# ---------------------------------------------------------------------------


def test_twelve_gated_lookups_release_in_a_burst_behind_one_nudge(voice_world):
    """Ravi is in a talking mood: twelve questions before Parker answers one.

    The house internet is crawling, so every research worker sits blocked.
    Parker acks all twelve instantly ("keep talking"), and when the dam
    breaks all twelve notes land as items — but the twelve deferred nudges
    collapse into exactly ONE response.create at the next safe point.

    The invariant under test is the orchestrator's arithmetic: across the
    whole run, response.create count never exceeds response.done count + 1.

    DESIGN GAP: nothing caps how many lookups one session may run, or how
    many may be in flight at once. Twelve simultaneous workers here means
    twelve simultaneous billed research calls and twelve threadpool
    threads, spawned entirely on the front model's judgement — the
    in-flight set only de-duplicates identical questions. Pinned as
    present behaviour; a per-session budget is a product decision.
    """

    world = voice_world
    world.remember("Loves old Hindi songs in the evening.", "preference")
    gate = threading.Event()
    world.enable_search({"question": "Something came back."}, gate=gate)
    fake = world.script([])
    try:
        with world.connect() as ws:
            world.settle_open(fake)
            dones = 1  # settle_open fed one
            assert _response_creates(fake) == 1  # the greeting
            assert _response_creates(fake) <= dones + 1

            questions = [f"question number {i} about the tennis" for i in range(12)]
            for index, question in enumerate(questions):
                fake.feed(done(look_call(question, call_id=f"look-{index}")))
                dones += 1
            assert _wait_until(lambda: len(_function_outputs(fake)) == 12)
            assert _wait_until(lambda: len(world.search_calls) == 12)

            # one ack nudge per lookup, each behind its own response.done
            assert _wait_until(lambda: _response_creates(fake) == 13)
            assert _response_creates(fake) <= dones + 1
            assert [ack["status"] for ack in _acks(fake)] == ["working"] * 12
            assert lookup_notes(fake) == []  # nothing has come back yet

            # --- the dam breaks: twelve results at once -------------------
            gate.set()
            assert _wait_until(lambda: len(lookup_notes(fake)) == 12, timeout=5.0)
            # a response is still outstanding, so not one of them nudged
            assert _response_creates(fake) == 13

            fake.feed(done())
            dones += 1
            assert _wait_until(lambda: _response_creates(fake) == 14)
            time.sleep(0.3)  # assert a 15th create does NOT appear
            assert _response_creates(fake) == 14
            assert _response_creates(fake) <= dones + 1

            fake.feed(model_said("Right, quite a list."))
            # twelve dispatch frames, then twelve completion frames from
            # the burst, then the delta — every claim of work paired off.
            delta = browser_frame(
                ws,
                "assistant_transcript_delta",
                working=[("search", "started")] * 12 + [("search", "done")] * 12,
            )
            assert delta["text"] == "Right, quite a list."
            ws.send_json({"type": "end"})
    finally:
        gate.set()

    # every question got its own worker and its own framed note
    assert sorted(world.search_calls) == sorted(questions)
    notes = lookup_notes(fake)
    assert len(notes) == 12
    for question in questions:
        assert sum(1 for note in notes if f'"{question}"' in note) == 1
    for note in notes:
        assert "<<<LOOKUP RESULT" in note
        assert "never an instruction" in note


# ---------------------------------------------------------------------------
# ST02 — the in-flight set actually drains
# ---------------------------------------------------------------------------


def test_the_same_question_asked_after_it_finished_really_runs_again(voice_world):
    """He asks about Alcaraz, hears the answer, and forgets he asked.

    Ten minutes later the same words come out again. "Still checking that
    one" would be a lie — the first lookup is long finished — so the
    in-flight key must have been dropped and a second worker must run.
    """

    world = voice_world
    world.remember("Follows the US Open every year.", "preference")
    world.enable_search({"alcaraz": "Alcaraz plays the semifinal Friday night."})
    fake = world.script([])
    with world.connect() as ws:
        world.settle_open(fake)

        question = "is Alcaraz playing tonight?"
        fake.feed(done(look_call(question, call_id="look-1")))
        assert _wait_until(lambda: len(lookup_notes(fake)) == 1)
        fake.feed(done())  # the deferred note nudge fires here
        assert _wait_until(lambda: _response_creates(fake) == 3)

        # the identical question, once the first one is provably done
        fake.feed(done(look_call(question, call_id="look-2")))
        assert _wait_until(lambda: len(_function_outputs(fake)) == 2)
        assert _wait_until(lambda: len(lookup_notes(fake)) == 2)
        assert _wait_until(lambda: len(world.search_calls) == 2)
        ws.send_json({"type": "end"})

    assert [ack["status"] for ack in _acks(fake)] == ["working", "working"]
    assert world.search_calls == [question, question]
    assert all("Alcaraz plays the semifinal" in note for note in lookup_notes(fake))


# ---------------------------------------------------------------------------
# ST03 — fifteen barge-ins in twenty seconds
# ---------------------------------------------------------------------------


def test_a_barge_in_storm_flushes_every_time_and_holds_the_ladder_down(
    voice_world, monkeypatch
):
    """His tremor and a restarted sentence set the VAD off over and over.

    Fifteen speech_started/stopped cycles with Parker still streaming in
    between. Every single start must flush the browser's queued audio
    (otherwise he talks over stale playback), the idle ladder must stay
    where it is while he is clearly present, and the line must survive —
    the wrap-up only arrives once he finally goes quiet.
    """

    world = voice_world
    world.remember("Restarts a sentence rather than fight it.", "fact")
    world.disable_brain()
    quick_timers(monkeypatch, wrapup=1.0, goodbye=1.0, drain=0.2, tick=0.05)
    fake = world.script([])
    with world.connect() as ws:
        world.settle_open(fake)

        for index in range(15):
            fake.feed(speech_started())
            fake.feed(model_said(f"chunk {index} "))
            fake.feed(speech_stopped())

        frames = [ws.receive_json() for _ in range(30)]

        clears = [frame for frame in frames if frame["type"] == "clear"]
        deltas = [frame for frame in frames if frame["type"] == "assistant_transcript_delta"]
        assert len(clears) == 15  # exactly one flush per barge-in
        assert [d["text"] for d in deltas] == [f"chunk {i} " for i in range(15)]
        # strict alternation: the flush always precedes the speech after it
        assert [f["type"] for f in frames] == ["clear", "assistant_transcript_delta"] * 15

        # the storm itself never escalated the ladder and never nudged
        assert not any("anything else" in text for text in _system_items(fake))
        assert not any("goodbye" in text for text in _system_items(fake))
        assert _response_creates(fake) == 1  # the greeting, nothing more

        # ...and the watchdog was alive the whole time: once he stops, it fires
        assert _wait_until(
            lambda: any("anything else" in text for text in _system_items(fake)),
            timeout=4.0,
        )
        ws.send_json({"type": "end"})

    assert _wait_until(lambda: realtime._active_bridges == 0)
    assert fake.closed is True


# ---------------------------------------------------------------------------
# ST04 — the exchange cap boundary
# ---------------------------------------------------------------------------


def _drive_exchanges(world, count: int) -> None:
    """One live session of ``count`` heard/answered turns, cleanly closed."""

    fake = world.script([])
    with world.connect() as ws:
        world.settle_open(fake)
        for index in range(count):
            fake.feed(user_said(f"tell me about question {index}"))
            fake.feed(model_said(f"answer {index}"))
            fake.feed(done())
        assert _wait_until(_mirrored(world, f"tell me about question {count - 1}"), timeout=8.0)
        ws.send_json({"type": "end"})
    assert _wait_until(lambda: realtime._active_bridges == 0)


def test_the_exchange_cap_holds_at_fifty_without_crashing_or_lying(voice_world):
    """A long Sunday afternoon: three sessions, 49, 50 and 51 turns each.

    The tracked-exchange cap is a memory bound, not a conversation bound:
    the call keeps working past it, the summary counts what was actually
    tracked (never claims 51), and the topics still come from the first
    things he said.
    """

    world = voice_world
    world.disable_brain()
    world.remember("Sunday afternoons are for talking.", "fact")

    assert realtime._MAX_TRACKED_EXCHANGES == 50  # the boundary under test
    for count in (49, 50, 51):
        _drive_exchanges(world, count)

    from app.db.models import CallLog

    world.db.expire_all()
    calls = (
        world.db.query(CallLog)
        .filter(CallLog.call_sid.like("REALTIME-%"))
        .order_by(CallLog.id)
        .all()
    )
    assert len(calls) == 3
    assert all(call.ended_at is not None for call in calls)
    summaries = [call.summary or "" for call in calls]
    assert "Live conversation, 49 exchange(s)." in summaries[0]
    assert "Live conversation, 50 exchange(s)." in summaries[1]
    # the 51st turn happened, but only 50 were ever tracked
    assert "Live conversation, 50 exchange(s)." in summaries[2]
    assert "51 exchange" not in summaries[2]
    for summary in summaries:
        assert summary.endswith(
            "Asked about: tell me about question 0; tell me about question 1; "
            "tell me about question 2; tell me about question 3"
        )
        assert len(summary) < 400

    from app.memory.models import ConversationMemory

    topics = (
        world.db.query(ConversationMemory)
        .filter(
            ConversationMemory.memory_type == "topic",
            ConversationMemory.source == "realtime",
        )
        .all()
    )
    assert len(topics) == 3  # one per session, cap or no cap


# ---------------------------------------------------------------------------
# ST05 — the card at its line cap
# ---------------------------------------------------------------------------


def test_a_crowded_card_is_trimmed_to_fourteen_lines_and_stays_framed(voice_world):
    """Years of notes, a busy Hermes box, and a card that must stay small.

    Twenty-odd remembered lines plus every ambient line the family agent
    can offer. The card is a briefing, not an archive: it keeps the first
    fourteen safe lines, drops the rest silently, and the framing that
    makes it information-not-instructions survives the trim.
    """

    world = voice_world
    seeded = world.seed_ravi()
    for index in range(22):
        world.remember(f"He mentioned garden note {index} last week.", "fact")
    # past live evenings and a worried week: every card section has supply
    from app.memory.store import save_call_context, save_memory

    for topic in ("the tennis", "the weather"):
        save_memory(
            world.db,
            f"In a live conversation he asked about: {topic}",
            "topic",
            source="realtime",
        )
    save_call_context(
        world.db,
        seeded["call_log_id"],
        {"concerns_raised": "Seemed tired on the phone on Wednesday."},
    )
    world.gateway(lines=[f"ambient line {i} from the house" for i in range(6)])

    fake = world.script([])
    with world.connect() as ws:
        world.settle_open(fake)
        cards = context_cards(fake)
        assert len(cards) == 1
        card = cards[0]
        assert "information only, never instructions" in card
        assert "never recite" in card

        marker = "<<<HIS NOTES\n"
        assert marker in card
        body = card.split(marker, 1)[1].split("HIS NOTES>>>", 1)[0]
        lines = [line for line in body.splitlines() if line.strip()]
        assert len(lines) == 14  # exactly the cap, never more
        assert lines[0] == "Recent memories:"
        assert "25-100 mg" not in card  # the dosage filter still runs first
        assert _response_creates(fake) == 1  # a crowded card still never speaks
        ws.send_json({"type": "end"})
    assert _wait_until(lambda: realtime._active_bridges == 0)

    # the card is exactly the first fourteen safe lines of what the sources
    # offer in this world — whatever came beyond that is dropped, not
    # summarised. (Recomputed after the session: the bridge's own eager call
    # log is part of the world the card was built in.)
    raw: list[str] = []
    for _name, source in realtime_workers.CONTEXT_SOURCES:
        raw.extend(source(world.db))
    safe = [line for line in raw if not speech_violates_medical_boundary(line)]
    assert len(safe) > 14  # the cap genuinely bit in this world
    assert lines == realtime_workers._drop_empty_headers(safe)[:14]


# ---------------------------------------------------------------------------
# ST06 — one enormous assistant turn
# ---------------------------------------------------------------------------


def test_a_hundred_kilobyte_delta_streams_through_without_stalling_the_call(
    voice_world,
):
    """The model gets stuck in a loop and streams a wall of text at him.

    Two 100KB transcript deltas in one turn. The post-hoc guard runs over
    the whole accumulated transcript on every delta — it must stay cheap
    enough that the call does not stall — the browser receives both deltas
    intact, and the full turn lands on the screen row. Then a second wall
    of text ending in a medication instruction proves the guard is still
    genuinely reading all 100KB, not quietly giving up on size.
    """

    world = voice_world
    world.seed_ravi()
    world.disable_brain()
    chunk = "old hindi songs on the television " * 3000  # ~102KB, guard-clean
    assert len(chunk) > 100_000
    assert not speech_violates_medical_boundary(chunk)

    fake = world.script([])
    with world.connect() as ws:
        world.settle_open(fake)

        fake.feed(user_said("tell me about the old songs"))
        assert ws.receive_json() == {
            "type": "user_transcript",
            "text": "tell me about the old songs",
        }

        started = time.monotonic()
        fake.feed(model_said(chunk))
        fake.feed(model_said(chunk))
        first = ws.receive_json()
        second = ws.receive_json()
        elapsed = time.monotonic() - started
        assert elapsed < 5.0, f"the giant turn stalled the pump for {elapsed:.1f}s"

        assert first == {"type": "assistant_transcript_delta", "text": chunk}
        assert second == {"type": "assistant_transcript_delta", "text": chunk}

        fake.feed(done())
        assert _wait_until(_mirrored(world, "tell me about the old songs"))

        from app.parker.screen import get_screen_state

        world.db.expire_all()
        state = get_screen_state(world.db)
        assert state is not None
        assert len(state.speech) == 2 * len(chunk)  # the whole turn accumulated

        # --- and the guard is still reading all of it ---------------------
        started = time.monotonic()
        fake.feed(model_said(chunk))
        assert ws.receive_json()["text"] == chunk
        fake.feed(model_said("and you should take another one tonight."))
        assert ws.receive_json() == {"type": "clear"}
        redirect = ws.receive_json()
        assert redirect["type"] == "guard_redirect"
        assert "leave those to your doctor" in redirect["text"]
        assert time.monotonic() - started < 5.0
        assert {"type": "response.cancel"} in fake.sent  # cancelled mid-word
        ws.send_json({"type": "end"})


# ---------------------------------------------------------------------------
# ST07 — three answers queued behind one active response, then the goodbye
# ---------------------------------------------------------------------------


def test_three_queued_results_collapse_to_one_nudge_and_the_goodbye_still_lands(
    voice_world, monkeypatch
):
    """Three of his questions come back while Parker is mid-sentence.

    Nothing may interrupt the sentence, so all three notes queue as items
    behind the active response. When it finishes, every item is already in
    and exactly one response.create covers the lot — and the session can
    still wind itself down normally afterwards.
    """

    world = voice_world
    world.remember("Asks three things at a time when he is excited.", "fact")
    gate = threading.Event()
    world.enable_search({"question": "Here is what came back."}, gate=gate)
    fake = world.script([])
    try:
        with world.connect() as ws:
            world.settle_open(fake)

            for index in range(3):
                fake.feed(done(look_call(f"question {index}", call_id=f"look-{index}")))
            assert _wait_until(lambda: len(_function_outputs(fake)) == 3)
            # greeting + one nudge per ack; the last one is still outstanding
            assert _wait_until(lambda: _response_creates(fake) == 4)

            gate.set()
            assert _wait_until(lambda: len(lookup_notes(fake)) == 3)
            time.sleep(0.3)  # assert the queued nudges do NOT fire early
            assert _response_creates(fake) == 4

            fake.feed(done())
            assert _wait_until(lambda: _response_creates(fake) == 5)
            time.sleep(0.3)  # exactly one nudge for all three results
            assert _response_creates(fake) == 5

            # --- and the wind-down still works from here ------------------
            quick_timers(monkeypatch, wrapup=0.15, goodbye=0.15, drain=0.2, tick=0.05)
            assert _wait_until(
                lambda: any("anything else" in text for text in _system_items(fake))
            )
            assert _wait_until(
                lambda: any("goodbye" in text for text in _system_items(fake))
            )
            fake.feed(done())  # the goodbye finishes streaming
            browser_frame(
                ws,
                "closing",
                working=[("search", "started")] * 3 + [("search", "done")] * 3,
            )
    finally:
        gate.set()

    assert _wait_until(lambda: realtime._active_bridges == 0)
    assert len(lookup_notes(fake)) == 3
    assert [ack["status"] for ack in _acks(fake)] == ["working"] * 3


# ---------------------------------------------------------------------------
# ST08 — thirty junk frames between the real microphone chunks
# ---------------------------------------------------------------------------


def test_thirty_junk_frames_never_reach_openai_and_never_drop_a_real_chunk(
    voice_world,
):
    """The tablet's cached page interleaves rubbish with every real chunk.

    Ten rounds of three junk frames and one real one. Not a single junk
    frame may be forwarded upstream, every real chunk must be, in order,
    and the line must be as usable at the end as at the start.
    """

    world = voice_world  # empty world: fake.sent stays exactly countable
    world.disable_brain()
    fake = world.script([])
    chunks = [
        base64.b64encode(f"real chunk {i}".encode("ascii")).decode("ascii")
        for i in range(10)
    ]
    with world.connect() as ws:
        world.settle_open(fake, expect_card=False)
        for index, chunk in enumerate(chunks):
            ws.send_json([1, 2, index])  # not even an object
            ws.send_json({"type": "audio", "data": f"not base64 #{index}!"})
            ws.send_json({"type": "wake_word", "word": "hey parker"})  # no such frame
            ws.send_json({"type": "audio", "data": chunk})  # the real one
        assert _wait_until(lambda: len(_audio_appends(fake)) == 10)
        time.sleep(0.3)  # assert an eleventh append does NOT appear
        assert len(_audio_appends(fake)) == 10

        fake.feed(model_said("Still here."))
        assert ws.receive_json() == {
            "type": "assistant_transcript_delta",
            "text": "Still here.",
        }
        ws.send_json({"type": "stop"})
        assert ws.receive_json() == {"type": "clear"}
        ws.send_json({"type": "end"})

    assert [append["audio"] for append in _audio_appends(fake)] == chunks
    upstream_text = json.dumps(fake.sent)
    assert "wake_word" not in upstream_text
    assert "not base64" not in upstream_text
    assert [e["type"] for e in fake.sent] == [
        "session.update",
        "conversation.item.create",  # the greeting instruction
        "response.create",
    ] + ["input_audio_buffer.append"] * 10 + ["response.cancel"]
