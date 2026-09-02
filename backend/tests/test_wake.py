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
from app.parker.wake import (
    ENERGY_GATE_RMS,
    WAKE_SAMPLE_RATE,
    WakeDetector,
    wake_heard,
    wake_match,
)

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


@pytest.mark.parametrize(
    "text",
    [
        "hey par ker",  # a syllable that split under effort
        "hey park er",
        "hey parkuh",  # a slurred trailing syllable
        "hey parkah",
        "hey um parka",
        "hi parkers",
    ],
)
def test_effortful_parker_attempts_wake(text):
    """Chairman calibration (2026-09-01): this is a Parkinson's user; the
    parker-like set stays generous. A missed wake costs more than an
    extra perk-up while the mic is already held locally."""

    assert wake_heard(text) is not None, text


@pytest.mark.parametrize(
    "text",
    ["hey darker", "hey marker", "hey barker", "hey packer", "hey parked"],
)
def test_greeting_plus_near_parker_is_an_accepted_extra_wake(text):
    """The independent review listed these as false wakes; the chairman
    kept them deliberately (see the session-3 plan, "Wake: calibrate for
    Dad"). Pinned so tightening is a conscious change, not drift. The
    ambient-TV soak (scripts/wake_soak.py) reports how often they occur."""

    assert wake_heard(text) is not None, text


@pytest.mark.parametrize(
    "text",
    [
        "a parker",  # a bare article is not a greeting (review finding)
        "the parker brothers game",
        "hey park the car",  # real park-words never join into parker
        "hey parking lot",
        "hey parkway traffic",
        "a darker shade",
    ],
)
def test_review_negatives_stay_quiet(text):
    assert wake_heard(text) is None, text


@pytest.mark.parametrize(
    "text,expected",
    [
        ("hey parker", ""),
        ("hey parker can you help me", "can you help me"),
        ("Hey, Parker! What's on TV tonight?", "what's on tv tonight"),
        ("um hey par ker turn it up", "turn it up"),
        ("hey parker " + "go on " * 20, ("go on " * 10).strip()),  # 40 words -> 20
    ],
)
def test_the_tail_is_what_followed_the_wake_phrase_bounded(text, expected):
    matched, tail = wake_match(text)
    assert tail == expected


def test_detector_carries_the_tail_and_hear_transcribes_after_a_wake():
    replies = iter([["hey parker can you"], ["help me with the tv"]])

    def transcriber(path):
        return next(replies)

    detector = WakeDetector(transcriber)
    hit = detector.feed(_tone(0.8))
    assert hit and hit["matched"] == "hey parker" and hit["tail"] == "can you"
    heard = detector.hear(_tone(0.8))  # the post-wake lane: raw transcript
    assert heard and heard["heard"] == "help me with the tv"


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


def test_the_adaptive_gate_skips_steady_tv_but_runs_on_a_burst():
    """With a TV on, every hop is energetic; the (opt-in) relative gate
    only spends inference when a hop rises above the room's low
    percentile, and only once the room has been steadily loud for ~20 s —
    a voice near the mic does, the TV's own steady level does not (wake
    soak 2026-09-02: 312 -> 54 inferences per 4 min of TV speech)."""

    calls = []

    def transcriber(path):
        calls.append(1)
        return ["the parking garage downtown"]

    detector = WakeDetector(transcriber, relative_gate=1.3)
    for _ in range(40):  # 32 s of steady TV: the room becomes the background
        detector.feed(_tone(0.8, amplitude=3000))
    assert detector.gated_by_background >= 8 and detector.inferences < 40
    # A louder burst (someone speaking up near the mic) runs the model.
    before = detector.inferences
    detector.feed(_tone(0.8, amplitude=9000))
    assert detector.inferences == before + 1
    # Back to steady TV: once the burst has left the 2.4 s window, hops are
    # gated again (the first hop or two still hold the burst and may run).
    gated_before = detector.gated_by_background
    for _ in range(5):
        detector.feed(_tone(0.8, amplitude=3000))
    assert detector.gated_by_background >= gated_before + 2


def test_the_adaptive_gate_never_blocks_a_quiet_room():
    """Silence has no background to rise above: the first words after
    quiet always run (the recall matrix is unaffected by the gate)."""

    detector = WakeDetector(lambda path: ["hey parker"], relative_gate=1.3)
    for _ in range(6):
        assert detector.feed(_tone(0.8, amplitude=50)) is None  # below the energy gate
    hit = detector.feed(_tone(0.8, amplitude=6000))
    assert hit is not None and hit["matched"] == "hey parker"
    assert detector.gated_by_background == 0


def test_a_wake_after_his_own_speech_is_never_gated():
    """Fresh review of PR #40 (2026-09-02): the first gate treated seven
    seconds of him talking to someone as "the room" and swallowed the
    wake that followed at the same loudness. A person's own speech is
    never steady enough, long enough, to become the background."""

    detector = WakeDetector(lambda path: ["hey parker"], relative_gate=1.3)
    for _ in range(10):  # 8 s of him talking at level L
        detector.feed(_tone(0.8, amplitude=3000))
    before = detector.inferences
    hit = detector.feed(_tone(0.8, amplitude=3000))  # "hey parker" at the SAME level
    assert detector.inferences == before + 1 and hit is not None
    assert detector.gated_by_background == 0


def test_the_gate_is_opt_in_and_off_by_default(monkeypatch):
    from app.config import settings

    assert WakeDetector(lambda path: [])._relative_gate == 0.0
    assert settings.parker_wake_relative_gate == 0.0  # a missed wake costs Dad more than CPU


def test_the_burst_window_takes_a_second_look_only_on_a_rise():
    """Opt-in (parker_wake_burst_window): when the last 1.3 s of the window
    is clearly louder than what came before, the loud part alone gets a
    second transcription; a wake found there counts. Steady sound never
    costs the extra inference."""

    calls: list[float] = []

    def transcriber(path):
        import wave

        with wave.open(str(path), "rb") as handle:
            seconds = handle.getnframes() / WAKE_SAMPLE_RATE
        calls.append(round(seconds, 1))
        # Only the 1.3 s burst clip reads as him; every full window (0.8,
        # 1.6, 2.4 s as it fills) reads as the TV.
        return ["hey parker"] if 1.2 < seconds < 1.4 else ["the parking garage downtown"]

    detector = WakeDetector(transcriber, burst_window=True)
    for _ in range(3):  # steady room: only the full window is transcribed
        assert detector.feed(_tone(0.8, amplitude=2500)) is None
    assert detector.burst_inferences == 0
    hit = detector.feed(_tone(0.8, amplitude=9000))  # someone speaks up
    assert hit is not None and hit["matched"] == "hey parker"
    assert detector.burst_inferences == 1
    assert any(c <= 1.4 for c in calls), calls  # the burst alone was transcribed


def test_the_burst_window_is_off_by_default():
    from app.config import settings

    assert WakeDetector(lambda path: [])._burst_window is False
    assert settings.parker_wake_burst_window is False


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


@pytest.fixture
def wake_url():
    """The wake lane as the page that owns power (server-authoritative)."""

    from app.parker.companion_power import authority

    granted = authority.claim(lambda on: None, client_id="wake-test-page")
    yield f"/parker/converse/wake?owner={granted['owner']}&gen={granted['gen']}"
    authority.release(lambda on: None)


def test_wake_lane_detects_and_reports(monkeypatch, tmp_path, wake_url):
    from app.parker import converse_router

    def transcriber(path):
        return ["hey parker"]

    monkeypatch.setattr(
        converse_router.converse_store, "transcriber", lambda: transcriber
    )
    monkeypatch.setattr(
        "app.parker.converse.write_receipt", lambda entry: None
    )
    with client.websocket_connect(wake_url) as ws:
        ws.send_json({"type": "audio", "data": "!!!not-base64"})  # ignored
        ws.send_json({"type": "audio", "data": _b64(_tone(0.8))})
        frame = ws.receive_json()
        assert frame["type"] == "wake"
        assert frame["matched"] == "hey parker"
        assert "infer_ms" in frame and "rms" in frame
        ws.send_json({"type": "end"})


def test_wake_lane_is_honest_without_the_local_model(monkeypatch, wake_url):
    from app.parker import converse_router

    monkeypatch.setattr(converse_router.converse_store, "transcriber", lambda: None)
    with client.websocket_connect(wake_url) as ws:
        frame = ws.receive_json()
    assert frame["type"] == "unavailable"
    assert "local voice model" in frame["text"]
