"""Voice-loop runtime state — the tray icon's single source of truth.

One row, states only (never utterances), stale rows read as idle, and a
publish failure can never take down the voice loop.
"""

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.demo.talk import run_talk_loop
from app.main import app
from app.parker.loop_state import (
    STATE_IDLE,
    STATE_LISTENING,
    STATE_SPEAKING,
    get_loop_state,
    publish_loop_state,
)

client = TestClient(app)


def test_publish_and_get_roundtrip(db):
    assert get_loop_state(db)["state"] == STATE_IDLE  # empty table reads idle
    publish_loop_state(db, STATE_LISTENING)
    state = get_loop_state(db)
    assert state["state"] == STATE_LISTENING
    assert state["stale"] is False
    publish_loop_state(db, STATE_SPEAKING)
    assert get_loop_state(db)["state"] == STATE_SPEAKING


def test_unknown_states_are_ignored(db):
    publish_loop_state(db, STATE_LISTENING)
    publish_loop_state(db, "juggling")
    assert get_loop_state(db)["state"] == STATE_LISTENING


def test_stale_rows_read_as_idle(db):
    publish_loop_state(db, STATE_LISTENING, now=datetime.utcnow() - timedelta(minutes=10))
    state = get_loop_state(db)
    assert state["state"] == STATE_IDLE
    assert state["stale"] is True


def test_publish_failure_never_raises(db, monkeypatch):
    monkeypatch.setattr(db, "commit", lambda: (_ for _ in ()).throw(RuntimeError("db gone")))
    publish_loop_state(db, STATE_LISTENING)  # must not raise


def test_loop_state_endpoint(db):
    assert client.get("/parker/loop/state").json()["state"] == STATE_IDLE
    publish_loop_state(db, STATE_LISTENING)
    body = client.get("/parker/loop/state").json()
    assert body["state"] == STATE_LISTENING
    assert body["updated_at"]


def test_talk_loop_publishes_states(db):
    """The loop reports listening before each window and processing on words."""

    class TurnRecorder:
        def __init__(self, turns):
            self._turns = iter(turns)

        def recorder(self, path, seconds):
            path.write_bytes(b"RIFF fake")

        def transcriber(self, path):
            try:
                return next(self._turns)
            except StopIteration:
                return []

    tr = TurnRecorder([["Remind me to stretch"], []])
    states: list[str] = []
    run_talk_loop(
        db,
        seconds=1.0,
        recorder=tr.recorder,
        transcriber=tr.transcriber,
        max_turns=2,
        on_state=states.append,
    )
    # Turn 1: listening → processing; turn 2 (silence): listening only.
    assert states == ["listening", "processing", "listening"]
