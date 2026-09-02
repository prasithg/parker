"""Companion power authority: server-owned, single-owner, fail-closed.

Pins the contract the independent review of PR #40 (2026-09-01) found
missing: power off must revoke every companion audio socket in every tab,
a second screen cannot silently displace one that is listening, a stale
token from before a restart or an older generation never authorizes, and
a failed settings write leaves Parker OFF and the page told.
"""

from __future__ import annotations

import asyncio

import pytest

from app.parker.companion_power import CompanionPower, PowerRefused


def _persist_ok(_on: bool) -> None:
    return None


def _closer(log: list, name: str):
    async def close(reason: str) -> None:
        log.append((name, reason))

    return close


def test_fresh_authority_is_off_and_authorizes_nothing():
    power = CompanionPower()
    assert power.authorize("anything", 0) == "power_off"
    assert power.snapshot()["power_on"] is False


def test_claim_issues_credentials_that_authorize_sockets():
    power = CompanionPower()
    granted = power.claim(_persist_ok, client_id="tab-a")
    assert granted["power_on"] is True and granted["gen"] == 1
    assert power.authorize(granted["owner"], granted["gen"]) is None
    assert power.authorize("forged", granted["gen"]) == "not_owner"
    assert power.authorize(granted["owner"], 99) == "not_owner"
    assert power.authorize(granted["owner"], "junk") == "not_owner"


def test_a_second_screen_cannot_displace_a_listening_owner():
    power = CompanionPower()
    a = power.claim(_persist_ok, client_id="tab-a")
    log: list = []
    power.register(token=a["owner"], kind="wake", close=_closer(log, "a-wake"))
    with pytest.raises(PowerRefused) as refused:
        power.claim(_persist_ok, client_id="tab-b")
    assert refused.value.status_code == 409 and refused.value.reason == "elsewhere"
    # A remains the owner, untouched.
    assert power.authorize(a["owner"], a["gen"]) is None
    assert log == []


def test_a_claim_displaces_an_owner_that_never_connected():
    """A page that claimed but holds no socket (permission still pending,
    or a dead tab) is stale: the next screen takes over and the old
    credentials stop authorizing."""

    power = CompanionPower()
    a = power.claim(_persist_ok, client_id="tab-a")
    b = power.claim(_persist_ok, client_id="tab-b")
    assert b["gen"] == a["gen"] + 1
    assert power.authorize(a["owner"], a["gen"]) == "not_owner"
    assert power.authorize(b["owner"], b["gen"]) is None


def test_release_revokes_every_socket_and_refuses_everything_after():
    power = CompanionPower()
    a = power.claim(_persist_ok, client_id="tab-a")
    log: list = []
    power.register(token=a["owner"], kind="wake", close=_closer(log, "wake"))
    power.register(token=a["owner"], kind="realtime", close=_closer(log, "line"))
    released = power.release(_persist_ok)
    assert released["power_on"] is False and released["saved"] is True
    for close in released["revoked"]:
        asyncio.run(close("power_off"))
    assert sorted(log) == [("line", "power_off"), ("wake", "power_off")]
    assert power.authorize(a["owner"], a["gen"]) == "power_off"
    assert power.snapshot()["live"] == {"wake": 0, "realtime": 0}


def test_a_failed_power_on_write_leaves_parker_off():
    power = CompanionPower()

    def persist_fails(_on: bool) -> None:
        raise RuntimeError("disk full")

    with pytest.raises(PowerRefused) as refused:
        power.claim(persist_fails, client_id="tab-a")
    assert refused.value.status_code == 503 and refused.value.reason == "not_saved"
    assert power.snapshot()["power_on"] is False
    assert power.authorize("anything", 1) == "power_off"


def test_a_failed_power_off_write_still_kills_every_line():
    power = CompanionPower()
    a = power.claim(_persist_ok, client_id="tab-a")
    log: list = []
    power.register(token=a["owner"], kind="realtime", close=_closer(log, "line"))

    def persist_fails(_on: bool) -> None:
        raise RuntimeError("disk full")

    released = power.release(persist_fails)
    assert released["saved"] is False  # the page shows this and retries
    assert len(released["revoked"]) == 1
    assert power.authorize(a["owner"], a["gen"]) == "power_off"


def test_the_same_screen_may_reclaim_while_listening():
    """The owner flipping its own switch off/on, or re-claiming after an
    engine restart, is not a second screen."""

    power = CompanionPower()
    a = power.claim(_persist_ok, client_id="tab-a")
    log: list = []
    power.register(token=a["owner"], kind="wake", close=_closer(log, "wake"))
    again = power.claim(_persist_ok, client_id="tab-a")
    assert again["gen"] == a["gen"] + 1
    assert power.authorize(a["owner"], a["gen"]) == "not_owner"
    assert power.authorize(again["owner"], again["gen"]) is None
    # The displaced registration is handed back for closing.
    assert len(again["displaced"]) == 1


def test_a_second_realtime_line_supersedes_the_first():
    power = CompanionPower()
    a = power.claim(_persist_ok, client_id="tab-a")
    log: list = []
    power.register(token=a["owner"], kind="realtime", close=_closer(log, "line-1"))
    _sid, superseded = power.register(
        token=a["owner"], kind="realtime", close=_closer(log, "line-2")
    )
    assert len(superseded) == 1
    assert power.snapshot()["live"] == {"wake": 0, "realtime": 1}


def test_registration_revalidates_the_owner():
    """A power-off (or a new owner) between the route's authorize() and
    register() must not leave a socket serving under a dead token."""

    power = CompanionPower()
    a = power.claim(_persist_ok, client_id="tab-a")
    power.release(_persist_ok)
    sid, superseded = power.register(token=a["owner"], kind="wake", close=_closer([], "x"))
    assert sid is None and superseded == []
    b = power.claim(_persist_ok, client_id="tab-b")
    sid, _ = power.register(token=a["owner"], kind="realtime", close=_closer([], "x"))
    assert sid is None
    sid, _ = power.register(token=b["owner"], kind="realtime", close=_closer([], "x"))
    assert sid is not None


def test_restart_forgets_the_owner_but_keeps_the_durable_flag_for_the_page():
    """After an engine restart nobody owns power: the booting page must
    claim again before a single frame flows. A pre-restart token is dead."""

    power = CompanionPower()
    a = power.claim(_persist_ok, client_id="tab-a")
    restarted = CompanionPower()  # a fresh process
    assert restarted.authorize(a["owner"], a["gen"]) == "power_off"
    b = restarted.claim(_persist_ok, client_id="tab-a")
    assert restarted.authorize(b["owner"], b["gen"]) is None


# ---------------------------------------------------------------------------
# The routes: what the page actually experiences.
# ---------------------------------------------------------------------------

import base64  # noqa: E402
import math  # noqa: E402
import struct  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from app.main import app  # noqa: E402
from app.parker import companion_power, converse_router  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_authority(monkeypatch):
    fresh = CompanionPower()
    monkeypatch.setattr(companion_power, "authority", fresh)
    monkeypatch.setattr(converse_router, "authority", fresh)
    yield fresh


def _claim(client_id: str):
    response = client.post(
        "/parker/converse/companion/power", json={"on": True, "client_id": client_id}
    )
    return response


def _wake_url(granted: dict) -> str:
    return f"/parker/converse/wake?owner={granted['owner']}&gen={granted['gen']}"


def _tone(seconds: float = 0.8, freq: float = 440.0) -> bytes:
    rate = 16000
    n = int(seconds * rate)
    return b"".join(
        struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * i / rate)))
        for i in range(n)
    )


def _fake_transcriber(monkeypatch, replies):
    it = iter(replies)

    def transcriber(path):
        return next(it)

    monkeypatch.setattr(converse_router.converse_store, "transcriber", lambda: transcriber)
    monkeypatch.setattr("app.parker.converse.write_receipt", lambda entry: None)


def test_a_socket_without_credentials_is_refused_before_any_audio(db, monkeypatch):
    _fake_transcriber(monkeypatch, [["hey parker"]])
    with client.websocket_connect("/parker/converse/wake") as ws:
        frame = ws.receive_json()
    assert frame["type"] == "revoked" and frame["reason"] == "power_off"
    assert "Nothing is listening" in frame["text"]


def test_power_off_from_another_tab_revokes_the_listening_tab(db, monkeypatch):
    """Two tabs: A is dormant-listening; B turns Parker off. A's wake
    socket receives `revoked` and closes; A's credentials are dead."""

    _fake_transcriber(monkeypatch, [["nothing here"]])
    a = _claim("tab-a").json()
    with client.websocket_connect(_wake_url(a)) as ws:
        assert client.get("/parker/converse/companion/settings").json()["live"]["wake"] == 1
        off = client.post(
            "/parker/converse/companion/power", json={"on": False, "client_id": "tab-b"}
        ).json()
        assert off["power_on"] is False and off["saved"] is True
        frame = ws.receive_json()
        assert frame == {"type": "revoked", "reason": "power_off"}
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    # A's token is dead now, even though A never asked to be off.
    with client.websocket_connect(_wake_url(a)) as ws:
        assert ws.receive_json()["reason"] == "power_off"
    assert client.get("/parker/converse/companion/settings").json()["power_on"] is False


def test_a_second_screen_is_refused_while_the_first_is_listening(db, monkeypatch):
    _fake_transcriber(monkeypatch, [["nothing here"]])
    a = _claim("tab-a").json()
    with client.websocket_connect(_wake_url(a)):
        refused = _claim("tab-b")
        assert refused.status_code == 409
        assert refused.json()["detail"]["reason"] == "elsewhere"
        assert "another screen" in refused.json()["detail"]["text"]


def test_a_stale_tab_from_an_older_generation_cannot_listen(db, monkeypatch):
    """A claimed (gen 1), someone turned Parker off, B claimed (gen 2):
    A's old credentials are refused as not_owner — never silently served."""

    _fake_transcriber(monkeypatch, [["nothing here"]])
    a = _claim("tab-a").json()
    client.post("/parker/converse/companion/power", json={"on": False})
    b = _claim("tab-b").json()
    assert b["gen"] > a["gen"]
    with client.websocket_connect(_wake_url(a)) as ws:
        frame = ws.receive_json()
    assert frame["reason"] == "not_owner"
    with client.websocket_connect(_wake_url(b)) as ws:
        ws.send_json({"type": "end"})  # B is served


def test_a_failed_power_on_write_answers_503_and_nothing_is_on(db, monkeypatch):
    def broken(db, **fields):
        raise RuntimeError("disk full")

    monkeypatch.setattr(converse_router, "set_companion_settings", broken)
    response = _claim("tab-a")
    assert response.status_code == 503
    assert response.json()["detail"]["reason"] == "not_saved"
    assert converse_router.authority.snapshot()["power_on"] is False


def test_a_failed_power_off_write_still_kills_the_line_and_says_so(db, monkeypatch):
    _fake_transcriber(monkeypatch, [["nothing here"]])
    a = _claim("tab-a").json()
    with client.websocket_connect(_wake_url(a)) as ws:

        def broken(db, **fields):
            raise RuntimeError("disk full")

        monkeypatch.setattr(converse_router, "set_companion_settings", broken)
        off = client.post("/parker/converse/companion/power", json={"on": False}).json()
        assert off["power_on"] is False and off["saved"] is False
        assert ws.receive_json()["reason"] == "power_off"
    # In memory it is off regardless — no socket can open.
    with client.websocket_connect(_wake_url(a)) as ws:
        assert ws.receive_json()["reason"] == "power_off"


def test_the_wake_lane_carries_the_request_tail(db, monkeypatch):
    """"Hey Parker, can you help me" — the wake frame carries the words
    inside the window, and the lane keeps transcribing briefly so the rest
    of the request reaches the page as `tail` frames."""

    _fake_transcriber(
        monkeypatch,
        [["hey parker can you"], ["help me with the tv"], ["help me with the tv please"]],
    )
    a = _claim("tab-a").json()
    with client.websocket_connect(_wake_url(a)) as ws:
        ws.send_json({"type": "audio", "data": base64.b64encode(_tone(0.8)).decode()})
        wake = ws.receive_json()
        assert wake["type"] == "wake" and wake["matched"] == "hey parker"
        assert wake["tail"] == "can you"
        ws.send_json({"type": "audio", "data": base64.b64encode(_tone(0.8)).decode()})
        assert ws.receive_json() == {"type": "tail", "text": "help me with the tv"}
        ws.send_json({"type": "audio", "data": base64.b64encode(_tone(0.8)).decode()})
        assert ws.receive_json() == {"type": "tail", "text": "help me with the tv please"}
        ws.send_json({"type": "end"})


def test_the_tail_lane_finishes_on_tail_end_with_sub_hop_audio(db, monkeypatch):
    """F2: the live line opens ~10 ms after the wake, long before the
    lane's first 0.7 s hop, so the words he spoke during the wake
    inference sat only in the lane and were lost. On `tail_end` the lane
    transcribes whatever it holds once — 0.4 s here, below the hop —
    answers with a FINAL tail frame and closes. The loop is sequential:
    every audio frame the page sent before `tail_end` is in that window."""

    _fake_transcriber(monkeypatch, [["hey parker can you"], ["help me with the tv"]])
    a = _claim("tab-a").json()
    with client.websocket_connect(_wake_url(a)) as ws:
        ws.send_json({"type": "audio", "data": base64.b64encode(_tone(0.8)).decode()})
        assert ws.receive_json()["tail"] == "can you"
        ws.send_json({"type": "audio", "data": base64.b64encode(_tone(0.4)).decode()})
        ws.send_json({"type": "tail_end"})
        assert ws.receive_json() == {"type": "tail", "text": "help me with the tv", "final": True}
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()  # the lane closed itself: the line has the words

    # Nothing new since the wake: the answer is still a final frame (empty),
    # so the page never waits on the lane, and no inference is spent.
    calls: list[str] = []

    def transcriber(path):
        calls.append("run")
        return ["hey parker"]

    monkeypatch.setattr(converse_router.converse_store, "transcriber", lambda: transcriber)
    b = _claim("tab-a").json()
    with client.websocket_connect(_wake_url(b)) as ws:
        ws.send_json({"type": "audio", "data": base64.b64encode(_tone(0.8)).decode()})
        assert ws.receive_json()["type"] == "wake"
        ws.send_json({"type": "tail_end"})
        assert ws.receive_json() == {"type": "tail", "text": "", "final": True}
    assert calls == ["run"]


def test_tail_frames_stop_after_the_window(db, monkeypatch):
    """Past the tail window the lane stops spending inference on audio it
    will never forward — the page is about to end it anyway."""

    calls: list[str] = []

    def transcriber(path):
        calls.append("run")
        return ["hey parker"] if len(calls) == 1 else ["late words"]

    monkeypatch.setattr(converse_router.converse_store, "transcriber", lambda: transcriber)
    monkeypatch.setattr("app.parker.converse.write_receipt", lambda entry: None)
    monkeypatch.setattr(converse_router, "WAKE_TAIL_SECONDS", 0.0)
    a = _claim("tab-a").json()
    with client.websocket_connect(_wake_url(a)) as ws:
        ws.send_json({"type": "audio", "data": base64.b64encode(_tone(0.8)).decode()})
        assert ws.receive_json()["type"] == "wake"
        ws.send_json({"type": "audio", "data": base64.b64encode(_tone(0.8)).decode()})
        ws.send_json({"type": "end"})
    assert calls == ["run"]  # the post-window audio never reached the model


def test_a_wake_hit_racing_a_revoke_is_swallowed_not_raised(db, monkeypatch):
    """Power off lands while the model is mid-inference on a frame that
    turns out to be a wake: the socket is already closed, and the route
    must simply end (fresh review of PR #40, 2026-09-02)."""

    import threading

    gate = threading.Event()

    def transcriber(path):
        gate.wait(timeout=5)
        return ["hey parker"]

    monkeypatch.setattr(converse_router.converse_store, "transcriber", lambda: transcriber)
    monkeypatch.setattr("app.parker.converse.write_receipt", lambda entry: None)
    a = _claim("tab-a").json()
    with client.websocket_connect(_wake_url(a)) as ws:
        ws.send_json({"type": "audio", "data": base64.b64encode(_tone(0.8)).decode()})
        off = client.post("/parker/converse/companion/power", json={"on": False}).json()
        assert off["power_on"] is False
        assert ws.receive_json()["reason"] == "power_off"
        gate.set()  # the inference finishes after the revoke
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


# ---------------------------------------------------------------------------
# Strict power-off (P0.1 F1): the lines die BEFORE the durable write.
# ---------------------------------------------------------------------------


def test_release_without_a_persist_skips_the_write_and_reports_no_save():
    """The route persists AFTER revoking every line; ``release()`` alone
    flips memory, hands back the closers, and reports that no write ran."""

    power = CompanionPower()
    a = power.claim(_persist_ok, client_id="tab-a")
    log: list = []
    power.register(token=a["owner"], kind="realtime", close=_closer(log, "line"))
    released = power.release()
    assert released["power_on"] is False and released["saved"] is None
    assert len(released["revoked"]) == 1
    assert power.authorize(a["owner"], a["gen"]) == "power_off"


def _audio_appends(fake) -> list[str]:
    return [e["audio"] for e in fake.sent if e["type"] == "input_audio_buffer.append"]


def test_power_off_revokes_the_line_before_the_durable_write(voice_world, monkeypatch):
    """F1 probe 3b (2026-09-02): with the settings write slowed, the live
    line was revoked only AFTER the write landed — his mic audio reached
    OpenAI a second after the switch was flipped. Off means off: the
    ``revoked`` frame and the hang-up arrive while the write is still
    running, a new line is refused meanwhile, only pre-flip audio ever
    went upstream, and the ack still reports whether the write saved."""

    import threading
    import time

    from scenario_harness import _wait_until

    world = voice_world
    world.disable_brain()
    fake = world.script([])
    persist: dict = {}
    real_set = converse_router.set_companion_settings

    def slow_set(db, **fields):
        persist["start"] = time.monotonic()
        time.sleep(0.5)
        try:
            return real_set(db, **fields)
        finally:
            persist["end"] = time.monotonic()

    monkeypatch.setattr(converse_router, "set_companion_settings", slow_set)
    response: dict = {}

    def flip_off() -> None:
        response.update(
            client.post(
                "/parker/converse/companion/power", json={"on": False, "client_id": "sarah-phone"}
            ).json()
        )

    with world.connect() as ws:
        world.settle_open(fake, expect_card=False)
        ws.send_json({"type": "audio", "data": "QUJD"})
        assert _wait_until(lambda: _audio_appends(fake) == ["QUJD"])  # the line is live

        post_start = time.monotonic()
        poster = threading.Thread(target=flip_off, daemon=True)
        poster.start()
        try:
            assert ws.receive_json() == {"type": "revoked", "reason": "power_off"}
            revoked_at = time.monotonic()
            assert "end" not in persist, "the line was revoked only after the durable write"
            assert revoked_at - post_start < 0.45  # well inside the 0.5 s write; the ordering asserts carry the pin
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()  # hung up on, still inside the write
            assert "end" not in persist
            # Nothing can open while the write runs: off is already off.
            with client.websocket_connect("/parker/converse/realtime" + world.power_query) as again:
                assert again.receive_json()["reason"] == "power_off"
            assert "end" not in persist
        finally:
            poster.join(5.0)
        assert not poster.is_alive()
        assert response == {"power_on": False, "saved": True}
        assert persist["end"] > revoked_at
        assert _audio_appends(fake) == ["QUJD"]  # only what he said before the flip
    assert _wait_until(lambda: fake.closed)


def test_a_revoked_screen_reads_off_while_the_durable_write_is_still_landing(voice_world, monkeypatch):
    """Negative-space review (2026-09-02): the route now revokes BEFORE it
    persists, and the revoked page's engine-restart check reads the
    settings inside that window. It used to see the still-ON durable flag
    with no owner — exactly the restart shape it re-claims on — and turned
    Parker back ON from the screen that had just been turned off. The
    settings must read OFF for as long as this process has released power;
    a fresh authority (an engine restart) still shows the durable flag so
    the restart re-claim keeps working."""

    import threading
    import time

    from scenario_harness import _wait_until

    from app.parker.companion_power import CompanionPower

    world = voice_world
    world.disable_brain()
    fake = world.script([])
    persist: dict = {}
    real_set = converse_router.set_companion_settings

    def slow_set(db, **fields):
        persist["start"] = time.monotonic()
        time.sleep(0.5)
        try:
            return real_set(db, **fields)
        finally:
            persist["end"] = time.monotonic()

    monkeypatch.setattr(converse_router, "set_companion_settings", slow_set)
    seen_by_page: dict = {}

    def flip_off() -> None:
        client.post("/parker/converse/companion/power", json={"on": False, "client_id": "sarah-phone"})

    with world.connect() as ws:
        world.settle_open(fake, expect_card=False)
        poster = threading.Thread(target=flip_off, daemon=True)
        poster.start()
        try:
            assert ws.receive_json() == {"type": "revoked", "reason": "power_off"}
            assert "end" not in persist, "the revoke must land before the durable write"
            # What the revoked page asks next, inside the write window.
            seen_by_page.update(client.get("/parker/converse/companion/settings").json())
            assert "end" not in persist
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
        finally:
            poster.join(5.0)
    assert seen_by_page["power_on"] is False, seen_by_page  # never "on with no owner"
    assert seen_by_page["owner_client"] == ""
    # After the write, off is durable too.
    assert client.get("/parker/converse/companion/settings").json()["power_on"] is False
    # An engine restart: a fresh authority has released nothing, so the
    # durable flag (whatever it says) is what the page reads — the restart
    # re-claim path is untouched.
    world.mp.setattr(converse_router, "authority", CompanionPower())
    converse_router.set_companion_settings(world.db, power_on=True)
    assert client.get("/parker/converse/companion/settings").json()["power_on"] is True
    assert _wait_until(lambda: fake.closed)
