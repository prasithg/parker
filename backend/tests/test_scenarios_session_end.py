"""Scenario gauntlet — he says he is done (docs/plans/2026-09-02-spoken-session-end.md).

Call 41 (Pras's real-mic test): "OK, thanks." left the line listening
until he power-cycled. Now his words end the session: an explicit ender
always, gratitude only when nothing is open. Parker says one short
goodbye, the existing `closing` handshake winds the page down to dormancy,
and his voice during the goodbye cancels the whole thing.

Each test asserts the BRIDGE CONTRACT only: the injected instruction, the
`closing` frame (or its absence), what was expired, and the journal.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from app.parker import realtime
from app.parker.realtime import spoken_session_end
from app.parker.session_review import RealtimeSessionEvent
from scenario_harness import *  # noqa: F401,F403


def _drain_until_closing(ws, limit: int = 12) -> bool:
    for _ in range(limit):
        frame = ws.receive_json()
        if frame.get("type") == "closing":
            return True
    return False


def _no_closing(ws, fake, *, sentinel: str = "still here") -> None:
    """Prove no `closing` was sent: a later turn still flows normally."""

    fake.feed(user_said(sentinel))
    frames = []
    for _ in range(6):
        frame = ws.receive_json()
        frames.append(frame.get("type"))
        if frame.get("type") == "user_transcript" and frame.get("text") == sentinel:
            break
    assert "closing" not in frames, frames


def _answer(world, ws, fake, heard: str, said: str) -> None:
    fake.feed(user_said(heard))
    assert ws.receive_json() == {"type": "user_transcript", "text": heard}
    fake.feed(model_said(said))
    assert ws.receive_json() == {"type": "assistant_transcript_delta", "text": said}
    fake.feed(done())


_REMINDER = {
    "action_type": "reminder",
    "label": "a 3 PM pill reminder",
    "subject": "take the pills at 3 PM",
    "intent_text": "remind me to take the pills at 3 PM",
}


def _end_events(world) -> list[RealtimeSessionEvent]:
    world.db.expire_all()
    return (
        world.db.query(RealtimeSessionEvent)
        .filter(RealtimeSessionEvent.kind == "session_end")
        .all()
    )


# ---------------------------------------------------------------------------
# The grammar
# ---------------------------------------------------------------------------


def test_hard_enders_and_gratitude_are_deterministic():
    for text in ("That's all.", "goodbye Parker", "Bye, Parker!", "I'm done",
                 "go back to sleep", "stop listening", "OK that's all", "that's it for now",
                 # bounded leads and trailers (fresh review of PR #43)
                 "okay that's all for now", "that's all for today", "that's all thanks",
                 "that's all thank you parker", "alright that's all thank you",
                 "goodbye parker thanks", "that's it thanks parker", "i'm done now",
                 "okay i'm done thanks", "you can go to sleep now", "go to sleep parker",
                 "goodnight", "good night", "goodbye", "we're done", "i'm finished",
                 "no thanks, I'm done", "see you later parker",
                 # re-review follow-up
                 "bye bye parker", "okay bye bye", "hey parker go to sleep",
                 "that's all. goodbye.", "that's it for tonight", "see you tomorrow parker",
                 "that's all, thanks", "thank you so much parker, that's all"):
        assert spoken_session_end(text) == "hard", text
    # Compound closers only — the evidence-backed forms (PR #43 review):
    # an acknowledgment lead + thanks ("OK, thanks" — call 41), or
    # "that's helpful, thanks". Optionally "Parker" on the end.
    for text in ("OK, thanks.", "great, thanks", "great thank you", "okay, thank you, parker",
                 "that's helpful, thanks", "that helps, thank you", "alright thanks parker",
                 "ok great thanks", "all right, thank you", "Okay thanks Parker",
                 "ok thanks so much", "okay thank you very much parker"):
        assert spoken_session_end(text) == "gratitude", text
    # Bare gratitude is conversation: a pause after "thanks" is him
    # composing, never completion (PR #43 review blocker).
    for text in ("thanks", "Thanks!", "thank you", "thank you Parker", "thanks parker",
                 "thanks so much", "thanks very much", "thank you so much parker",
                 "thanks a lot", "that's helpful", "that helps",
                 "ok thanks for that one what about golf"):
        assert spoken_session_end(text) is None, text
    for text in ("stop", "bye", "ok", "thanks for nothing tell me more",
                 "I'm done with the tennis, what about golf?", "that's all I know about him",
                 # questions and reports that merely END with an ender phrase
                 # (fresh review of PR #43): never hang up on him mid-thought
                 "should I go to sleep?", "when should I go to sleep", "is it time to go to sleep",
                 "I can't go to sleep", "you said that's all", "what do you mean that's all",
                 "he said I'm done", "why did you stop listening", "you can rest now?",
                 "did you go to sleep", "that's it", "nothing else", "talk to you later",
                 "that's enough for now", "thanks bye",
                 # re-review follow-up: first-person reports and declines are not exits
                 "I go to sleep", "and I go to sleep", "so I go to sleep", "i stop listening",
                 "I go to sleep now", "no thanks", "no thank you"):
        assert spoken_session_end(text) is None, text


# ---------------------------------------------------------------------------
# S01 — "OK, thanks." after a real answer ends the session (call 41)
# ---------------------------------------------------------------------------


def test_ok_thanks_after_a_real_answer_says_goodbye_and_winds_down(voice_world):
    world = voice_world
    world.disable_brain()
    fake = world.script([])
    with world.connect() as ws:
        world.settle_open(fake, expect_card=False)
        _answer(world, ws, fake, "what's the weather like",
                "It is warm and sunny this afternoon, around twenty-six degrees.")
        fake.feed(user_said("OK, thanks."))
        assert ws.receive_json() == {"type": "user_transcript", "text": "OK, thanks."}
        assert _wait_until(lambda: any("sounds finished" in i for i in _system_items(fake)))
        # The goodbye is a nudged response; its done hands the page the drain.
        fake.feed(model_said("Any time. Say Hey Parker whenever you like."))
        fake.feed(done())
        assert _drain_until_closing(ws)
    assert _wait_until(lambda: realtime._active_bridges == 0, timeout=5.0)
    events = _end_events(world)
    assert len(events) == 1 and '"kind": "soft"' in events[0].detail
    assert events[0].heard == "OK, thanks."


# ---------------------------------------------------------------------------
# S02 — an explicit ender always ends it
# ---------------------------------------------------------------------------


def test_thats_all_ends_the_session_even_mid_flow(voice_world):
    world = voice_world
    world.disable_brain()
    fake = world.script([])
    with world.connect() as ws:
        world.settle_open(fake, expect_card=False)
        fake.feed(user_said("That's all, Parker."))
        assert ws.receive_json()["type"] == "user_transcript"
        assert _wait_until(lambda: any("said he is done" in i for i in _system_items(fake)))
        fake.feed(model_said("Goodbye for now."))
        fake.feed(done())
        assert _drain_until_closing(ws)
    assert _wait_until(lambda: realtime._active_bridges == 0, timeout=5.0)
    assert '"kind": "hard"' in _end_events(world)[0].detail


# ---------------------------------------------------------------------------
# S02b — bare "thanks" after a real answer is conversation; his follow-up
# after a pause is answered (PR #43 review blocker: a Parkinsonian pause
# after an acknowledgment must never read as completion)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("gratitude", ["thanks", "thank you"])
def test_bare_thanks_after_an_answer_stays_conversational_and_a_late_question_is_answered(
    voice_world, gratitude
):
    world = voice_world
    world.disable_brain()
    fake = world.script([])
    with world.connect() as ws:
        world.settle_open(fake, expect_card=False)
        _answer(world, ws, fake, "what's the weather like",
                "It is warm and sunny this afternoon, around twenty-six degrees.")
        fake.feed(user_said(gratitude))
        assert ws.receive_json()["type"] == "user_transcript"
        fake.feed(done())  # the auto-response to his thanks finishes: nothing may ride it
        assert not _wait_until(lambda: any("sounds finished" in i for i in _system_items(fake)), timeout=0.5)
        # …then, after his pause, the follow-up flows like any other turn.
        _answer(world, ws, fake, "and what about tomorrow",
                "Tomorrow looks cooler, with showers in the afternoon.")
        _no_closing(ws, fake)
        assert not any("sounds finished" in i for i in _system_items(fake))
        ws.send_json({"type": "end"})
    assert _wait_until(lambda: realtime._active_bridges == 0, timeout=5.0)
    assert _end_events(world) == []


# ---------------------------------------------------------------------------
# S02c — "that's helpful, thanks" (the review's cited form) winds down
# ---------------------------------------------------------------------------


def test_thats_helpful_thanks_after_an_answer_winds_down(voice_world):
    world = voice_world
    world.disable_brain()
    fake = world.script([])
    with world.connect() as ws:
        world.settle_open(fake, expect_card=False)
        _answer(world, ws, fake, "who won the tennis",
                "Alcaraz won the final in four sets on Sunday afternoon.")
        fake.feed(user_said("that's helpful, thanks"))
        assert ws.receive_json()["type"] == "user_transcript"
        assert _wait_until(lambda: any("sounds finished" in i for i in _system_items(fake)))
        fake.feed(model_said("Any time. Say Hey Parker whenever you like."))
        fake.feed(done())
        assert _drain_until_closing(ws)
    assert _wait_until(lambda: realtime._active_bridges == 0, timeout=5.0)
    events = _end_events(world)
    assert len(events) == 1 and '"kind": "soft"' in events[0].detail
    assert events[0].heard == "that's helpful, thanks"


# ---------------------------------------------------------------------------
# S03 — thanks after a QUESTION is conversation, not an ending
# ---------------------------------------------------------------------------


def test_thanks_after_parker_asked_a_question_keeps_listening(voice_world):
    world = voice_world
    world.disable_brain()
    fake = world.script([])
    with world.connect() as ws:
        world.settle_open(fake, expect_card=False)
        _answer(world, ws, fake, "set a reminder",
                "Sure. Would you like it for this afternoon or tomorrow morning?")
        # The compound closer itself, so the QUESTION gate is what holds
        # (bare "thanks" never reaches the gates any more).
        fake.feed(user_said("OK, thanks."))
        assert ws.receive_json()["type"] == "user_transcript"
        fake.feed(done())  # the auto-response to "thanks" finishes: no closing may ride it
        _no_closing(ws, fake)
        assert not any("sounds finished" in i for i in _system_items(fake))
        ws.send_json({"type": "end"})
    assert _wait_until(lambda: realtime._active_bridges == 0, timeout=5.0)
    assert _end_events(world) == []


# ---------------------------------------------------------------------------
# S04 — thanks while a lookup is in flight: the answer still comes
# ---------------------------------------------------------------------------


def test_thanks_while_a_lookup_is_in_flight_does_not_hang_up(voice_world):
    world = voice_world
    release = threading.Event()
    calls = world.enable_search(
        {"us open": "Djokovic plays at seven tonight."}, gate=release
    )
    fake = world.script([])
    with world.connect() as ws:
        world.settle_open(fake, expect_card=False)  # nothing seeded: no card
        _answer(world, ws, fake, "who plays tonight", "Let me check that for you right now.")
        fake.feed(done(look_call("who plays at the US Open tonight")))
        assert _wait_until(lambda: calls)
        assert ws.receive_json() == {"type": "working", "kind": "search", "status": "started"}
        fake.feed(user_said("OK, thanks."))  # the closer itself: the LOOKUP gate holds
        # No goodbye while the worker is out.
        assert not _wait_until(lambda: any("sounds finished" in i for i in _system_items(fake)), timeout=0.5)
        release.set()
        assert _wait_until(lambda: any("Djokovic" in i for i in _system_items(fake)))
        ws.send_json({"type": "end"})
    assert _wait_until(lambda: realtime._active_bridges == 0, timeout=5.0)
    assert _end_events(world) == []


# ---------------------------------------------------------------------------
# S05 — thanks with an offer waiting for his yes/no: the offer stays open
# ---------------------------------------------------------------------------


def test_thanks_with_an_offer_pending_keeps_the_offer_open(voice_world):
    world = voice_world
    world.disable_brain()
    fake = world.script([])
    with world.connect() as ws:
        world.settle_open(fake, expect_card=False)
        _answer(world, ws, fake, "remind me about the pills",
                "I can set a reminder for the pills at three this afternoon if you like.")
        fake.feed(done(propose_call(_REMINDER)))
        assert_staged(ws.receive_json(), "a 3 PM pill reminder")
        fake.feed(user_said("OK, thanks."))  # the closer itself: the OFFER gate holds
        assert ws.receive_json()["type"] == "user_transcript"
        assert not _wait_until(lambda: any("sounds finished" in i for i in _system_items(fake)), timeout=0.5)
        # …and his yes still executes it.
        fake.feed(user_said("yes"))
        assert ws.receive_json()["type"] == "user_transcript"
        result = ws.receive_json()
        assert result["type"] == "action_result" and result["status"] == "executed"
        ws.send_json({"type": "end"})
    assert _wait_until(lambda: realtime._active_bridges == 0, timeout=5.0)


# ---------------------------------------------------------------------------
# S06 — a hard ender with an offer pending: nothing runs, the offer lapses
# ---------------------------------------------------------------------------


def test_thats_all_with_an_offer_pending_expires_it_before_the_goodbye(voice_world):
    world = voice_world
    world.disable_brain()
    fake = world.script([])
    with world.connect() as ws:
        world.settle_open(fake, expect_card=False)
        fake.feed(done(propose_call(_REMINDER)))
        assert_staged(ws.receive_json(), "a 3 PM pill reminder")
        fake.feed(user_said("that's all"))
        assert ws.receive_json()["type"] == "user_transcript"
        lapsed = ws.receive_json()
        assert lapsed == {"type": "action_result", "status": "expired", "label": lapsed["label"]}
        fake.feed(model_said("Goodbye for now."))
        fake.feed(done())
        assert _drain_until_closing(ws)
    assert _wait_until(lambda: realtime._active_bridges == 0, timeout=5.0)
    from app.db.models import StagedAction

    world.db.expire_all()
    assert world.db.query(StagedAction).filter(StagedAction.status == "executed").count() == 0
    detail = _end_events(world)[0].detail
    assert '"kind": "hard"' in detail and '"pending_offer_expired": true' in detail


# ---------------------------------------------------------------------------
# S07 — his voice during the goodbye cancels dormancy
# ---------------------------------------------------------------------------


def test_speaking_during_the_goodbye_cancels_the_end(voice_world):
    world = voice_world
    world.disable_brain()
    fake = world.script([])
    with world.connect() as ws:
        world.settle_open(fake, expect_card=False)
        fake.feed(user_said("that's all"))
        assert ws.receive_json()["type"] == "user_transcript"
        assert _wait_until(lambda: any("said he is done" in i for i in _system_items(fake)))
        fake.feed(model_said("Goodbye for"))
        assert ws.receive_json()["type"] == "assistant_transcript_delta"
        fake.feed({"type": "input_audio_buffer.speech_started"})  # "wait —"
        assert ws.receive_json() == {"type": "clear"}
        fake.feed(done())
        _no_closing(ws, fake, sentinel="wait, one more thing")
        # A later "that's all" ends it after all.
        fake.feed(user_said("that's all"))
        assert ws.receive_json()["type"] == "user_transcript"
        fake.feed(model_said("Goodbye for now."))
        fake.feed(done())
        assert _drain_until_closing(ws)
    assert _wait_until(lambda: realtime._active_bridges == 0, timeout=5.0)
    assert len(_end_events(world)) == 2


# ---------------------------------------------------------------------------
# S08 — bare "stop" is not an ender
# ---------------------------------------------------------------------------


def test_bare_stop_never_ends_the_session(voice_world):
    world = voice_world
    world.disable_brain()
    fake = world.script([])
    with world.connect() as ws:
        world.settle_open(fake, expect_card=False)
        fake.feed(user_said("stop"))
        assert ws.receive_json()["type"] == "user_transcript"
        fake.feed(done())
        _no_closing(ws, fake)
        ws.send_json({"type": "end"})
    assert _wait_until(lambda: realtime._active_bridges == 0, timeout=5.0)
    assert _end_events(world) == []


# ---------------------------------------------------------------------------
# S09 — his voice during a SOFT goodbye cancels it too
# ---------------------------------------------------------------------------


def test_speaking_during_the_soft_goodbye_cancels_the_end(voice_world):
    world = voice_world
    world.disable_brain()
    fake = world.script([])
    with world.connect() as ws:
        world.settle_open(fake, expect_card=False)
        _answer(world, ws, fake, "what's the weather like",
                "It is warm and sunny this afternoon, around twenty-six degrees.")
        fake.feed(user_said("OK, thanks."))
        assert ws.receive_json()["type"] == "user_transcript"
        assert _wait_until(lambda: any("sounds finished" in i for i in _system_items(fake)))
        fake.feed(model_said("Any time. Say"))
        assert ws.receive_json()["type"] == "assistant_transcript_delta"
        fake.feed({"type": "input_audio_buffer.speech_started"})  # "actually —"
        assert ws.receive_json() == {"type": "clear"}
        fake.feed(done())
        _no_closing(ws, fake, sentinel="actually, one more thing")
        ws.send_json({"type": "end"})
    assert _wait_until(lambda: realtime._active_bridges == 0, timeout=5.0)


# ---------------------------------------------------------------------------
# S10 — a question that merely ends with an ender phrase is conversation
# ---------------------------------------------------------------------------


def test_a_question_ending_in_go_to_sleep_never_hangs_up(voice_world):
    world = voice_world
    world.disable_brain()
    fake = world.script([])
    with world.connect() as ws:
        world.settle_open(fake, expect_card=False)
        fake.feed(user_said("Should I go to sleep?"))
        assert ws.receive_json()["type"] == "user_transcript"
        fake.feed(done())
        _no_closing(ws, fake)
        assert not any("said he is done" in i for i in _system_items(fake))
        ws.send_json({"type": "end"})
    assert _wait_until(lambda: realtime._active_bridges == 0, timeout=5.0)
    assert _end_events(world) == []


# ---------------------------------------------------------------------------
# S11 — the real VAD order: the server's auto-reply to his closer is already
# running when the transcript lands; `closing` must ride the GOODBYE's done
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "closer, marker",
    [("OK, thanks.", "sounds finished"), ("That's all, Parker.", "said he is done")],
)
def test_the_goodbye_is_spoken_even_when_the_vad_already_answered_his_closer(voice_world, closer, marker):
    """Fresh review (2026-09-02): with create_response=true the server
    starts a reply to his closer BEFORE the transcript arrives; the goodbye
    nudge deferred behind it, and `closing` rode that auto-reply's done —
    the goodbye was never spoken and the line dropped mid-thought."""

    world = voice_world
    world.disable_brain()
    fake = world.script([])
    with world.connect() as ws:
        world.settle_open(fake, expect_card=False)
        _answer(world, ws, fake, "what's the weather like",
                "It is warm and sunny this afternoon, around twenty-six degrees.")
        creates_before = _response_creates(fake)
        fake.feed({"type": "response.created"})  # the VAD's reply to what he just said
        fake.feed(user_said(closer))
        assert ws.receive_json() == {"type": "user_transcript", "text": closer}
        assert _wait_until(lambda: any(marker in i for i in _system_items(fake)))
        assert _response_creates(fake) == creates_before  # deferred behind the active reply
        # The auto-reply finishes: NOT the hang-up — the goodbye gets nudged now.
        fake.feed(model_said("You're welcome! Anything else you need tonight?"))
        assert ws.receive_json() == {
            "type": "assistant_transcript_delta", "text": "You're welcome! Anything else you need tonight?"
        }
        fake.feed(done())
        assert _wait_until(lambda: _response_creates(fake) == creates_before + 1), "the goodbye was never nudged"
        # Then the goodbye itself streams and ITS done hands the page the drain.
        fake.feed({"type": "response.created"})
        fake.feed(model_said("Goodbye for now — say Hey Parker any time."))
        frames = [ws.receive_json()]
        assert frames[0]["type"] == "assistant_transcript_delta", frames
        fake.feed(done())
        assert _drain_until_closing(ws)
    assert _wait_until(lambda: realtime._active_bridges == 0, timeout=5.0)
    assert len(_end_events(world)) == 1


def test_compound_closer_during_lookup_result_injection_waits_for_the_answer(
    voice_world, monkeypatch
):
    """A result remains open while its system item is crossing the bridge."""

    world = voice_world
    world.enable_search({"us open": "Djokovic plays at seven tonight."})
    injection_started = threading.Event()
    release_injection = threading.Event()
    original = realtime.RealtimeBridge._send_system_item

    async def blocked_result(self, text):
        if "LOOKUP RESULT" in text:
            injection_started.set()
            await asyncio.to_thread(release_injection.wait, 3.0)
        await original(self, text)

    monkeypatch.setattr(realtime.RealtimeBridge, "_send_system_item", blocked_result)
    fake = world.script([])
    closed_early = False
    try:
        with world.connect() as ws:
            world.settle_open(fake, expect_card=False)
            _answer(
                world, ws, fake, "who plays tonight",
                "Let me check that for you right now.",
            )
            fake.feed(done(look_call("who plays at the US Open tonight")))
            assert ws.receive_json() == {
                "type": "working", "kind": "search", "status": "started"
            }
            assert injection_started.wait(timeout=1.0)
            fake.feed(user_said("OK, thanks."))
            assert ws.receive_json() == {"type": "user_transcript", "text": "OK, thanks."}
            closed_early = _wait_until(
                lambda: any("sounds finished" in item for item in _system_items(fake)),
                timeout=0.3,
            )
            release_injection.set()
            assert _wait_until(lambda: any("Djokovic" in item for item in _system_items(fake)))
            ws.send_json({"type": "end"})
    finally:
        release_injection.set()
    assert not closed_early
    assert _end_events(world) == []


def test_compound_closer_waits_for_the_result_bearing_response_done(voice_world):
    """Only the response that can include the injected result closes the obligation."""

    world = voice_world
    world.enable_search({"us open": "Djokovic plays at seven tonight."})
    fake = world.script([])
    with world.connect() as ws:
        world.settle_open(fake, expect_card=False)
        _answer(
            world, ws, fake, "who plays tonight",
            "Let me check that for you right now.",
        )
        fake.feed(done(look_call("who plays at the US Open tonight")))
        assert ws.receive_json() == {
            "type": "working", "kind": "search", "status": "started"
        }
        assert _wait_until(lambda: any("Djokovic" in item for item in _system_items(fake)))
        assert ws.receive_json() == {
            "type": "working", "kind": "search", "status": "done"
        }

        # The result arrived while the function-call acknowledgement response
        # was active. Gratitude is still conversation before that response ends.
        fake.feed(user_said("OK, thanks."))
        assert ws.receive_json() == {"type": "user_transcript", "text": "OK, thanks."}
        assert not any("sounds finished" in item for item in _system_items(fake))
        fake.feed(model_said("I'm still checking that for you now."))
        assert ws.receive_json()["type"] == "assistant_transcript_delta"
        fake.feed(done())  # sends the deferred, result-bearing nudge

        # The acknowledgement's done is not enough: the result response is
        # now active and must still be heard before a soft close is possible.
        fake.feed(user_said("OK, thanks."))
        assert ws.receive_json() == {"type": "user_transcript", "text": "OK, thanks."}
        goodbye_count = sum("sounds finished" in item for item in _system_items(fake))
        assert goodbye_count == 0
        fake.feed(model_said("Djokovic plays at seven tonight on the main court."))
        assert ws.receive_json()["type"] == "assistant_transcript_delta"
        fake.feed(done())

        # Now the answer has actually landed; the observed compound closer
        # may end the session normally.
        fake.feed(user_said("OK, thanks."))
        assert ws.receive_json() == {"type": "user_transcript", "text": "OK, thanks."}
        assert _wait_until(
            lambda: sum("sounds finished" in item for item in _system_items(fake))
            == goodbye_count + 1
        )
        ws.send_json({"type": "end"})
