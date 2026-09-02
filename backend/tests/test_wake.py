"""Local "Hey Parker" wake detection: matcher, energy gate, ws lane.

Pinned contracts (docs/plans/2026-09-01-wake-word.md):

- the matcher wakes on effortful/variant greetings + parker-like tokens
  and NEVER on ambient mentions of Parker or near-words;
- a quiet room never runs the model (energy gate) and one utterance
  fires exactly one wake;
- the ws lane is localhost-only plumbing: junk frames are ignored, an
  unavailable model is honest, and a detection emits one wake frame.
"""

from __future__ import annotations

import base64
import math
import struct

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.parker import wake
from app.parker.wake import ENERGY_GATE_RMS, WAKE_SAMPLE_RATE, WakeDetector, wake_heard

client = TestClient(app)


# ---------------------------------------------------------------------------
# Matcher
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "hey parker",
        "Hey, Parker!",
        "hey... parker",
        "um hey parker can you help",
        "hey parka",  # effortful/ASR variant
        "hay parker",
        "hi parker",
        "hey um parker",  # one filler between greeting and name
        "eh parker",
    ],
)
def test_wake_matches_greeting_plus_parker(text):
    assert wake_heard(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "",
        "parker",  # the TV mentioning Parker must not wake
        "parker come here",
        "tell parker I said hi",  # no greeting directly before
        "hey partner",  # near-word, distance 2
        "hey parking",
        "hey park the car",
        "they parked her car",
        "hey there mister parker fan club",  # greeting 3+ tokens away
        "peter parker was here",
    ],
)
def test_ambient_speech_never_wakes(text):
    assert wake_heard(text) is None


# ---------------------------------------------------------------------------
# Detector: energy gate + hop cadence + single fire
# ---------------------------------------------------------------------------


def _silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(seconds * WAKE_SAMPLE_RATE)


def _tone(seconds: float, amplitude: int = 6000) -> bytes:
    count = int(seconds * WAKE_SAMPLE_RATE)
    return b"".join(
        struct.pack(
            "<h", int(amplitude * math.sin(2 * math.pi * 220 * i / WAKE_SAMPLE_RATE))
        )
        for i in range(count)
    )


def test_a_quiet_room_never_runs_the_model():
    calls = []

    def transcriber(path):
        calls.append(path)
        return ["hey parker"]

    detector = WakeDetector(transcriber)
    for _ in range(8):
        assert detector.feed(_silence(0.5)) is None
    assert detector.inferences == 0
    assert calls == []


def test_energetic_audio_runs_on_the_hop_and_wakes_once():
    replies = iter([["um so anyway"], ["hey parker"], ["hey parker"]])

    def transcriber(path):
        return next(replies)

    detector = WakeDetector(transcriber)
    first = detector.feed(_tone(0.8))
    assert first is None  # transcript had no wake phrase
    hit = detector.feed(_tone(0.8))
    assert hit is not None
    assert hit["matched"] == "hey parker"
    assert hit["rms"] >= ENERGY_GATE_RMS
    assert detector.inferences == 2
    # The window cleared on detection: the SAME utterance cannot re-fire
    # before fresh audio accumulates past the hop.
    assert detector.feed(_tone(0.2)) is None


def test_sub_hop_frames_accumulate_without_inference():
    def transcriber(path):
        raise AssertionError("must not run before a full hop of new audio")

    detector = WakeDetector(transcriber)
    for _ in range(6):  # 6 x 0.1 s < 0.7 s hop
        assert detector.feed(_tone(0.1)) is None
    assert detector.inferences == 0


def test_a_crashing_transcriber_never_ends_dormancy():
    def transcriber(path):
        raise RuntimeError("model exploded")

    detector = WakeDetector(transcriber)
    assert detector.feed(_tone(0.8)) is None
    assert detector.feed(_tone(0.8)) is None  # keeps trying, keeps calm


# ---------------------------------------------------------------------------
# The ws lane
# ---------------------------------------------------------------------------


def _b64(pcm: bytes) -> str:
    return base64.b64encode(pcm).decode("ascii")


def test_wake_lane_detects_and_reports(monkeypatch, tmp_path):
    from app.parker import converse_router

    def transcriber(path):
        return ["hey parker"]

    monkeypatch.setattr(
        converse_router.converse_store, "transcriber", lambda: transcriber
    )
    monkeypatch.setattr(
        "app.parker.converse.write_receipt", lambda entry: None
    )
    with client.websocket_connect("/parker/converse/wake") as ws:
        ws.send_json({"type": "audio", "data": "!!!not-base64"})  # ignored
        ws.send_json({"type": "audio", "data": _b64(_tone(0.8))})
        frame = ws.receive_json()
        assert frame["type"] == "wake"
        assert frame["matched"] == "hey parker"
        assert "infer_ms" in frame and "rms" in frame
        ws.send_json({"type": "end"})


def test_wake_lane_is_honest_without_the_local_model(monkeypatch):
    from app.parker import converse_router

    monkeypatch.setattr(converse_router.converse_store, "transcriber", lambda: None)
    with client.websocket_connect("/parker/converse/wake") as ws:
        frame = ws.receive_json()
    assert frame["type"] == "unavailable"
    assert "local voice model" in frame["text"]
