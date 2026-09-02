"""Scenario gauntlet — one owner, one line: handover, refusal, and per-line
isolation across sequential lines.

Dimension: the seams of a single-owner household. Since 2026-09-01
companion power is server-authoritative and SINGLE-OWNER
(app/parker/companion_power.py, docs/personas/ravi-scenarios.md): the page
that claimed power presents an owner token + generation on every audio
socket; a second screen's claim is answered 409 ``elsewhere`` while the
owner is actually listening; a second realtime socket from the SAME owner
(the page's own reconnect) supersedes the first, which hears
``revoked``/``superseded`` and is hung up on; and power off from any
screen kills every line with ``revoked``/``power_off``.
``MAX_LIVE_BRIDGES`` is 2 only so the superseded bridge and its
replacement may overlap for the length of a handover.

So there are never two conversations at once — but there are two BRIDGES
alive for a moment, and everything a bridge keeps per conversation
(exchanges, the finalize record, in-flight lookups, the context worker,
the post-hoc guard, the upstream socket itself) must stay on its own line
across that handover: nothing the old line started may land on the new
one, and the old line's record must close with only its own words.

Each test asserts the BRIDGE CONTRACT only: the exact ``revoked`` frame
and reason a page hears, the 409 a second screen gets, what is injected
upstream on *which* socket, and what lands in the DB.

Round-1 file (test_scenarios_degraded.py, D12) pins the refusal alone —
a second room's switch answered 409 while his line is live, on one
upstream. This file is the dimension proper: what happens ACROSS the
handover and the power-off, with a fake per bridge so routing bites.
"""

from __future__ import annotations

import json
import threading
from urllib.parse import parse_qs

import pytest
from starlette.websockets import WebSocketDisconnect

from app.parker import converse_router, realtime
from scenario_harness import *  # noqa: F401,F403

POWER = "/parker/converse/companion/power"
LINE = "/parker/converse/realtime"


# ---------------------------------------------------------------------------
# The two-fake connector — read this before adding a handover scenario
# ---------------------------------------------------------------------------


def two_upstreams(world, count: int = 2) -> list:
    """Hand a DIFFERENT FakeUpstream to each bridge, in connect order.

    The harness's ``world.script()`` monkeypatches ``connect_openai`` to
    return ONE fake forever. That is right for a single line and wrong
    here: the superseded bridge and its replacement are both alive during
    a handover, and with one shared fake every routing assertion ("that
    note went to the OLD line's upstream, never the new one") would pass
    vacuously — both bridges' traffic would sit in one ``fake.sent``.

    So: a closure over a list, popped in connect order. ``fakes[0]`` is
    the first bridge to open, ``fakes[1]`` the one that supersedes it (or
    the next screen's line). A refused socket (``revoked``) and a refused
    claim (409) consume nothing — the router checks authority BEFORE
    building a bridge, so ``connect()`` is never called for them. Open
    lines one at a time (wait for each bridge's session.update) so the
    index ordering is deterministic rather than a thread race.
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


def _claim(client_id: str):
    """A screen flips its switch on — through the route, so the router's
    ``authority`` is the one exercised (the fixture only seeds the owner)."""

    return client.post(POWER, json={"on": True, "client_id": client_id})


def _power_off(client_id: str):
    return client.post(POWER, json={"on": False, "client_id": client_id})


def _line_url(granted: dict) -> str:
    return f"{LINE}?owner={granted['owner']}&gen={granted['gen']}"


def _gen_of(power_query: str) -> int:
    return int(parse_qs(power_query.lstrip("?"))["gen"][0])


def _revoked(ws, reason: str) -> None:
    """The page hears exactly one ``revoked`` frame naming *reason*, then
    the server hangs up on it — no silent stall, no extra frame first."""

    assert ws.receive_json() == {"type": "revoked", "reason": reason}
    with pytest.raises(WebSocketDisconnect):
        ws.receive_json()


def _page_hangs_up(ws, fake) -> None:
    """What the fenced page does on ``revoked``: close its socket.

    The old bridge then finalizes on its own; its upstream closing is the
    per-line observable that its shutdown ran to the end (the upstream is
    closed last, after the finalize write).
    """

    ws.close()
    assert _wait_until(lambda: fake.closed), "the superseded bridge never shut down"


def _audio_appends(fake) -> list[str]:
    return [e["audio"] for e in fake.sent if e["type"] == "input_audio_buffer.append"]


def _cancels(fake) -> list[dict]:
    return [e for e in fake.sent if e["type"] == "response.cancel"]


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
# C01 — the tablet reconnects mid-conversation: handover, two records
# ---------------------------------------------------------------------------


def test_the_owners_reconnect_supersedes_the_old_line_and_each_line_keeps_its_own_record(
    voice_world,
):
    """Saturday: Ravi is two exchanges into the cricket when his tablet
    reconnects (a Wi-Fi blip, a reload). The reconnect is the same owner's
    second line: the old line hears ``revoked``/``superseded`` and is hung
    up on; the page closes it, and it finalizes with ONLY the cricket —
    two exchanges — on its own call log and topic memory. The new line
    carries on about the plumber on its own upstream socket, its own call
    log, its own memory. The new upstream never hears his mic or his words.
    """

    world = voice_world
    world.seed_ravi()
    world.disable_brain()
    fakes = two_upstreams(world)

    with world.connect() as ws_a:
        assert _wait_until(lambda: _opened(fakes[0]))
        world.settle_open(fakes[0])

        # -- his conversation on the first line --------------------------
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
        assert _wait_until(_mirrored(world, "is the cricket on channel four tonight?"))
        ws_a.send_json({"type": "audio", "data": "QUJD"})  # his mic, on his line
        assert _wait_until(lambda: _audio_appends(fakes[0]) == ["QUJD"])
        fakes[0].feed(user_said("and what time does it start?"))
        assert ws_a.receive_json() == {
            "type": "user_transcript",
            "text": "and what time does it start?",
        }
        fakes[0].feed(done())
        assert _wait_until(_mirrored(world, "and what time does it start?"))

        # -- the same owner opens a second line: handover -----------------
        with world.connect() as ws_b:
            assert _wait_until(lambda: _opened(fakes[1]))
            assert realtime._active_bridges == 2  # the overlap the cap exists for
            world.settle_open(fakes[1])  # her card is built before his line closes
            _revoked(ws_a, "superseded")
            _page_hangs_up(ws_a, fakes[0])
            assert _wait_until(_finalized(world, 1))
            assert _wait_until(lambda: realtime._active_bridges == 1)

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
            ws_b.send_json({"type": "audio", "data": "WFla"})
            assert _wait_until(lambda: _audio_appends(fakes[1]) == ["WFla"])

            ws_b.send_json({"type": "end"})
            assert _wait_until(_finalized(world, 2))

    assert _wait_until(lambda: realtime._active_bridges == 0)
    assert fakes[0].closed is True and fakes[1].closed is True

    # per-socket routing: the new upstream never carried the old line
    assert _audio_appends(fakes[0]) == ["QUJD"]
    assert _audio_appends(fakes[1]) == ["WFla"]
    assert "cricket" not in json.dumps(fakes[1].sent)
    assert "plumber" not in json.dumps(fakes[0].sent)

    calls = _realtime_calls(world)
    assert len(calls) == 2
    assert len({call.call_sid for call in calls}) == 2
    old = next(c for c in calls if "cricket" in (c.summary or ""))
    new = next(c for c in calls if "plumber" in (c.summary or ""))
    assert old.id != new.id
    assert old.ended_at is not None and new.ended_at is not None
    assert "2 exchange(s)" in old.summary  # his two turns, and only his
    assert "and what time does it start?" in old.summary
    assert "plumber" not in old.summary
    assert "1 exchange(s)" in new.summary
    assert "cricket" not in new.summary

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
    assert {memory.call_log_id for memory in memories} == {old.id, new.id}
    by_call = {memory.call_log_id: memory.content for memory in memories}
    assert "cricket" in by_call[old.id] and "plumber" not in by_call[old.id]
    assert "plumber" in by_call[new.id] and "cricket" not in by_call[new.id]


# ---------------------------------------------------------------------------
# C02 — a second screen while he is talking: refused, then admitted
# ---------------------------------------------------------------------------


def test_a_second_screen_is_refused_while_he_is_talking_then_admitted_once_he_hangs_up(
    voice_world,
):
    """Sarah opens the companion on her phone while Ravi's tablet is live.

    Her switch is answered 409 ``elsewhere`` — a screen that is actually
    listening cannot be silently displaced — and his line does not so
    much as hiccup: his next exchange flows as before. When he hangs up
    and the bridge slot is back to zero, her claim succeeds with a new
    generation, his old credentials are dead (``not_owner``), and her
    line opens on its own upstream socket with its own call log.
    """

    world = voice_world
    world.seed_ravi()
    world.disable_brain()
    fakes = two_upstreams(world)

    with world.connect() as ws_a:
        assert _wait_until(lambda: _opened(fakes[0]))
        world.settle_open(fakes[0])

        refused = _claim("sarah-phone")
        assert refused.status_code == 409
        assert refused.json()["detail"] == {
            "reason": "elsewhere",
            "text": "Parker is already on and listening on another screen.",
        }
        assert converse_router.authority.snapshot()["owner_client"] == "scenario-page"

        # his line is untouched by the refusal
        fakes[0].feed(user_said("is the cricket on channel four tonight?"))
        assert ws_a.receive_json() == {
            "type": "user_transcript",
            "text": "is the cricket on channel four tonight?",
        }
        fakes[0].feed(model_said("Channel four, from seven."))
        assert ws_a.receive_json() == {
            "type": "assistant_transcript_delta",
            "text": "Channel four, from seven.",
        }
        fakes[0].feed(done())
        assert _wait_until(_mirrored(world, "is the cricket on channel four tonight?"))
        assert fakes[1].sent == []  # the refusal opened nothing upstream

        # he hangs up; the slot frees when the handler returns
        ws_a.send_json({"type": "end"})
        assert _wait_until(lambda: realtime._active_bridges == 0)
        assert fakes[0].closed is True

        granted = _claim("sarah-phone")
        assert granted.status_code == 200
        hers = granted.json()
        assert hers["power_on"] is True and hers["owner"]
        assert hers["gen"] == _gen_of(world.power_query) + 1
        assert converse_router.authority.snapshot()["owner_client"] == "sarah-phone"

        # his tablet's old credentials are dead from this instant
        with client.websocket_connect(LINE + world.power_query) as stale:
            assert stale.receive_json() == {
                "type": "revoked",
                "reason": "not_owner",
                "text": "Parker is on another screen now.",
            }
        assert fakes[1].sent == []  # a refused socket opens nothing upstream

        with client.websocket_connect(_line_url(hers)) as ws_b:
            assert _wait_until(lambda: _opened(fakes[1]))
            assert fakes[1].sent[0]["type"] == "session.update"
            assert realtime._active_bridges == 1
            world.settle_open(fakes[1])
            fakes[1].feed(user_said("did the plumber confirm Thursday morning?"))
            assert ws_b.receive_json() == {
                "type": "user_transcript",
                "text": "did the plumber confirm Thursday morning?",
            }
            fakes[1].feed(done())
            assert _wait_until(_mirrored(world, "did the plumber confirm Thursday morning?"))
            ws_b.send_json({"type": "end"})
            assert _wait_until(lambda: realtime._active_bridges == 0)

    assert fakes[1].closed is True
    calls = _realtime_calls(world)
    assert len(calls) == 2  # two admitted lines, two logs; the refusals left none
    assert len({call.call_sid for call in calls}) == 2


# ---------------------------------------------------------------------------
# C03 — power off from another screen, mid-conversation
# ---------------------------------------------------------------------------


def test_power_off_from_another_screen_kills_his_line_and_his_credentials(voice_world):
    """Sarah flips Parker off from the kitchen while Ravi's line is up.

    Off means off for everyone: his tablet hears ``revoked``/``power_off``
    and is hung up on; his record still finalizes honestly (ended_at set,
    his exchange counted); and his tablet's credentials cannot reopen the
    line — answered ``power_off`` before a single audio frame, with
    nothing opened upstream.
    """

    world = voice_world
    world.seed_ravi()
    world.disable_brain()
    fakes = two_upstreams(world)

    with world.connect() as ws_a:
        assert _wait_until(lambda: _opened(fakes[0]))
        world.settle_open(fakes[0])

        fakes[0].feed(user_said("put the tennis on"))
        assert ws_a.receive_json() == {"type": "user_transcript", "text": "put the tennis on"}
        fakes[0].feed(model_said("I'll see what I can do."))
        assert ws_a.receive_json() == {
            "type": "assistant_transcript_delta",
            "text": "I'll see what I can do.",
        }
        fakes[0].feed(done())
        assert _wait_until(_mirrored(world, "put the tennis on"))

        off = _power_off("sarah-phone")
        assert off.status_code == 200
        assert off.json() == {"power_on": False, "saved": True}
        _revoked(ws_a, "power_off")
        _page_hangs_up(ws_a, fakes[0])
        assert _wait_until(_finalized(world, 1))
        assert _wait_until(lambda: realtime._active_bridges == 0)

    calls = _realtime_calls(world)
    assert len(calls) == 1
    assert calls[0].ended_at is not None
    assert "1 exchange(s)" in calls[0].summary
    assert "put the tennis on" in calls[0].summary

    with client.websocket_connect(LINE + world.power_query) as stale:
        assert stale.receive_json() == {
            "type": "revoked",
            "reason": "power_off",
            "text": "Parker is off. Nothing is listening.",
        }
    assert fakes[1].sent == []  # the refusal never reached OpenAI
    settings = client.get("/parker/converse/companion/settings").json()
    assert settings["power_on"] is False
    assert settings["live"] == {"wake": 0, "realtime": 0}


# ---------------------------------------------------------------------------
# C04 — a lookup in flight on the old line, across the handover
# ---------------------------------------------------------------------------


def test_a_lookup_in_flight_on_the_old_line_never_lands_on_the_new_one(voice_world):
    """He asks whether it will rain; his tablet reconnects before the answer.

    The old line's lookup is still with the research assistant when the
    new line supersedes it. That answer is dropped by policy: it never
    reaches the new upstream, and the old one closed without it. The new
    line does not inherit "already_working" for the same question either
    — asking again spawns a fresh worker, and exactly one note (its own)
    lands on its socket, with exactly one presence pair on its screen.
    The house pays twice, on purpose, across a handover.
    """

    world = voice_world
    world.seed_ravi()
    gate = threading.Event()
    finished: list[str] = []

    def answer(question: str) -> WorkerResult:
        finished.append(question)
        return WorkerResult(
            kind="search", question=question, speech="Rain is forecast after six this evening."
        )

    world.enable_search(answer, gate=gate)
    question = "is it going to rain this evening?"
    fakes = two_upstreams(world)
    try:
        with world.connect() as ws_a:
            assert _wait_until(lambda: _opened(fakes[0]))
            world.settle_open(fakes[0])

            fakes[0].feed(done(look_call(question, call_id="his-1")))
            assert _wait_until(lambda: len(world.search_calls) == 1)
            assert _wait_until(lambda: _function_outputs(fakes[0]))
            his_ack = _function_outputs(fakes[0])[0]
            assert his_ack["item"]["call_id"] == "his-1"
            assert json.loads(his_ack["item"]["output"])["status"] == "working"
            assert ws_a.receive_json() == {
                "type": "working",
                "kind": "search",
                "status": "started",
            }

            with world.connect() as ws_b:
                assert _wait_until(lambda: _opened(fakes[1]))
                world.settle_open(fakes[1])
                _revoked(ws_a, "superseded")
                _page_hangs_up(ws_a, fakes[0])
                assert _wait_until(lambda: realtime._active_bridges == 1)
                assert finished == []  # his answer is still being worked on

                # the same question on the new line: a fresh worker, never
                # "still checking" about work this line did not start
                fakes[1].feed(done(look_call(question, call_id="her-1")))
                assert _wait_until(lambda: len(world.search_calls) == 2)
                assert _wait_until(lambda: _function_outputs(fakes[1]))
                her_ack = _function_outputs(fakes[1])[0]
                assert her_ack["item"]["call_id"] == "her-1"
                assert json.loads(her_ack["item"]["output"])["status"] == "working"

                gate.set()  # both answers come back together
                assert _wait_until(lambda: len(finished) == 2)
                assert _wait_until(lambda: lookup_notes(fakes[1]))
                # the stale worker has fully unwound (its thread is done)
                # — so "no note anywhere else" is a settled fact, not a gap
                assert _wait_until(lambda: realtime._inflight_db_threads == 0)
                assert len(lookup_notes(fakes[1])) == 1
                assert f'"{question}"' in lookup_notes(fakes[1])[0]
                assert "Rain is forecast after six this evening." in lookup_notes(fakes[1])[0]
                assert lookup_notes(fakes[0]) == []  # closed without its answer
                assert len(_function_outputs(fakes[0])) == 1
                assert len(_function_outputs(fakes[1])) == 1

                # her screen saw exactly one lookup's presence pair, then speech
                fakes[1].feed(model_said("After six, they say."))
                delta = browser_frame(
                    ws_b,
                    "assistant_transcript_delta",
                    working=[("search", "started"), ("search", "done")],
                )
                assert delta["text"] == "After six, they say."

                ws_b.send_json({"type": "end"})
    finally:
        gate.set()

    assert _wait_until(lambda: realtime._active_bridges == 0)
    assert world.search_calls == [question, question]


# ---------------------------------------------------------------------------
# C05 — one screen row, one line at a time
# ---------------------------------------------------------------------------


def test_the_dad_screen_is_one_row_and_the_line_that_spoke_last_owns_it(voice_world):
    """The room's screen across a handover.

    The live Dad screen is a single row (``SCREEN_STATE_ROW_ID``). Under
    one owner, one line, its writers are sequential: his exchange on the
    old line is the row; after the reconnect, the new line's exchange is
    the row — never a second row, never an interleaved claim. (Round 2's
    "Sarah's phone overwrites Ravi's tablet row" design question is
    closed by the contract: there is no second live line to write it.)
    """

    world = voice_world
    world.seed_ravi()
    world.disable_brain()
    fakes = two_upstreams(world)

    from app.parker.screen import ScreenState, get_screen_state

    with world.connect() as ws_a:
        assert _wait_until(lambda: _opened(fakes[0]))
        world.settle_open(fakes[0])

        fakes[0].feed(user_said("put the tennis on"))
        assert ws_a.receive_json()["type"] == "user_transcript"
        fakes[0].feed(model_said("I'll see what I can do."))
        assert ws_a.receive_json()["type"] == "assistant_transcript_delta"
        fakes[0].feed(done())
        assert _wait_until(_mirrored(world, "put the tennis on"))
        world.db.expire_all()
        assert get_screen_state(world.db).speech == "I'll see what I can do."
        assert world.db.query(ScreenState).count() == 1

        with world.connect() as ws_b:
            assert _wait_until(lambda: _opened(fakes[1]))
            world.settle_open(fakes[1])
            _revoked(ws_a, "superseded")
            _page_hangs_up(ws_a, fakes[0])
            assert _wait_until(_finalized(world, 1))
            # the old line's close left the row exactly as he last spoke it
            world.db.expire_all()
            assert get_screen_state(world.db).heard == "put the tennis on"
            assert get_screen_state(world.db).speech == "I'll see what I can do."

            fakes[1].feed(user_said("never mind, is it raining?"))
            assert ws_b.receive_json()["type"] == "user_transcript"
            fakes[1].feed(model_said("Dry all evening."))
            assert ws_b.receive_json()["type"] == "assistant_transcript_delta"
            fakes[1].feed(done())
            assert _wait_until(_mirrored(world, "never mind, is it raining?"))

            ws_b.send_json({"type": "end"})

    assert _wait_until(lambda: realtime._active_bridges == 0)
    world.db.expire_all()
    assert world.db.query(ScreenState).count() == 1  # never two screens
    final = get_screen_state(world.db)
    assert final.heard == "never mind, is it raining?"
    assert final.speech == "Dry all evening."
    assert final.kind == "answer"
    assert len(_realtime_calls(world)) == 2  # two lines wrote that one row


# ---------------------------------------------------------------------------
# C06 — a failed write on the old line, an action on the new one
# ---------------------------------------------------------------------------


def test_a_failed_write_on_the_old_line_never_costs_the_new_line_its_action(
    voice_world, monkeypatch
):
    """His bins reminder hits a wedged write; after the reconnect, his
    plumber reminder must still stage.

    On the old line the capture write blows up: Parker answers the tool
    call honestly (rejected, "could not save") and nothing is staged. The
    reconnect supersedes that line, and the plumber reminder proposed on
    the new line stages normally through the same pipeline — the old
    line's failed action never poisons the new one's, and the old line
    never gets a second answer.
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
        # no staged frame preceded his apology on the old line
        fakes[0].feed(model_said("I couldn't save that one, sorry."))
        assert ws_a.receive_json() == {
            "type": "assistant_transcript_delta",
            "text": "I couldn't save that one, sorry.",
        }

        with world.connect() as ws_b:
            assert _wait_until(lambda: _opened(fakes[1]))
            world.settle_open(fakes[1])
            _revoked(ws_a, "superseded")
            _page_hangs_up(ws_a, fakes[0])
            assert _wait_until(lambda: realtime._active_bridges == 1)

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
            assert_staged(ws_b.receive_json(), "plumber Thursday")

            assert len(_function_outputs(fakes[0])) == 1  # no second answer
            ws_b.send_json({"type": "end"})

    assert _wait_until(lambda: realtime._active_bridges == 0)

    from app.db.models import StagedAction

    world.db.expire_all()
    staged = world.db.query(StagedAction).all()
    assert len(staged) == 1  # exactly the new line's
    assert staged[0].action_type == "reminder"
    assert "plumber" in (staged[0].action_payload or "")
    assert "bins" not in (staged[0].action_payload or "")


# ---------------------------------------------------------------------------
# C07 — a context card in flight on the old line, across the handover
# ---------------------------------------------------------------------------


def test_a_context_card_in_flight_on_the_old_line_is_dropped_and_the_new_line_gets_only_its_own(
    voice_world, monkeypatch
):
    """Both bridges' context workers are in flight during the handover.

    The old line was superseded and hung up on while its card was still
    being built; the new line's own worker is in flight at the same
    moment. When both come back: the new line gets exactly ONE card, and
    it is its own (the second worker to enter) — never the old line's;
    the old line's upstream closed without a card (dropped by policy);
    and, as always, a card nudges nothing.

    Rewritten for the single-owner contract. Under two concurrent lines,
    which bridge owned which result was not observable (round-2 scope
    note); across a sequential handover it is — the first worker to enter
    is the old line's, the second the new line's — so this pin is
    stronger than the one it replaces, not vacuous.
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
            # Not settle_open(expect_card=False): that helper waits for the
            # context worker to FINISH, and this scenario needs it held in
            # flight. The greeting's done is all the open needs here.
            fakes[0].feed(done())
            assert _wait_until(lambda: len(entered) == 1)  # his worker is inside, blocked

            with world.connect() as ws_b:
                assert _wait_until(lambda: _opened(fakes[1]))
                fakes[1].feed(done())
                assert _wait_until(lambda: len(entered) == 2)
                _revoked(ws_a, "superseded")
                _page_hangs_up(ws_a, fakes[0])
                assert _wait_until(lambda: realtime._active_bridges == 1)
                assert context_cards(fakes[0]) == []
                assert context_cards(fakes[1]) == []

                gate.set()
                assert _wait_until(lambda: context_cards(fakes[1]))
                # the old line's worker has fully unwound too, so the
                # absences below are settled facts
                assert _wait_until(lambda: realtime._inflight_db_threads == 0)
                hers = context_cards(fakes[1])
                assert len(hers) == 1
                assert "Card number 1 for this conversation." in hers[0]  # her own
                assert "Card number 0" not in hers[0]  # never his
                assert "information only, never instructions" in hers[0]
                assert context_cards(fakes[0]) == []  # closed without its card
                assert _response_creates(fakes[1]) == 1  # greeting only: no card nudge

                ws_b.send_json({"type": "end"})
    finally:
        gate.set()

    assert _wait_until(lambda: realtime._active_bridges == 0)


# ---------------------------------------------------------------------------
# C08 — the guard trips on the old line only
# ---------------------------------------------------------------------------


def test_a_medical_trip_on_the_old_line_does_not_carry_into_the_new_one(voice_world):
    """His line drifts into dosage advice; the tablet reconnects mid-response.

    The post-hoc guard cancels the old line's response, flushes its
    tablet, and speaks the redirect there — and that response never ends
    before the reconnect, so the guard is still tripped on the old line
    when the new one opens. The new line talks normally: its transcript
    and its audio reach the page, and its upstream never receives a
    cancel. Guard state is a per-line thing; it does not ride a handover.
    """

    world = voice_world
    world.seed_ravi()
    world.disable_brain()
    fakes = two_upstreams(world)

    from app.brain.guard import MEDICAL_BOUNDARY_REDIRECT

    with world.connect() as ws_a:
        assert _wait_until(lambda: _opened(fakes[0]))
        world.settle_open(fakes[0])

        fakes[0].feed(model_said("You should take an extra dose tonight."))
        assert ws_a.receive_json() == {"type": "clear"}
        assert ws_a.receive_json() == {
            "type": "guard_redirect",
            "text": MEDICAL_BOUNDARY_REDIRECT,
        }
        assert _wait_until(lambda: len(_cancels(fakes[0])) == 1)

        with world.connect() as ws_b:
            assert _wait_until(lambda: _opened(fakes[1]))
            world.settle_open(fakes[1])
            _revoked(ws_a, "superseded")
            _page_hangs_up(ws_a, fakes[0])
            assert _wait_until(lambda: realtime._active_bridges == 1)

            # the new line is untouched: no clear, no redirect, just its words
            fakes[1].feed(model_said("Dinner is at seven, then."))
            assert ws_b.receive_json() == {
                "type": "assistant_transcript_delta",
                "text": "Dinner is at seven, then.",
            }
            fakes[1].feed(audio_delta("UENN"))  # a tripped guard would mute this
            assert ws_b.receive_json() == {"type": "audio", "data": "UENN"}
            assert _cancels(fakes[1]) == []

            ws_b.send_json({"type": "end"})

    assert _wait_until(lambda: realtime._active_bridges == 0)
    assert len(_cancels(fakes[0])) == 1
    assert _cancels(fakes[1]) == []
