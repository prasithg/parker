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

import audioop
import base64
import math
import struct
import wave

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


def _tone(seconds: float, amplitude: int = 6000, hz: float = 220) -> bytes:
    count = int(seconds * WAKE_SAMPLE_RATE)
    return b"".join(
        struct.pack(
            "<h", int(amplitude * math.sin(2 * math.pi * hz * i / WAKE_SAMPLE_RATE))
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


def test_a_crashing_transcriber_never_ends_dormancy():
    def transcriber(path):
        raise RuntimeError("model exploded")

    detector = WakeDetector(transcriber)
    assert detector.feed(_tone(0.8)) is None
    assert detector.feed(_tone(0.8)) is None  # keeps trying, keeps calm


def test_detector_counts_consecutive_failures_and_resets_on_success():
    """F5: a model that dies after warm-up (model.bin gone, a dead disk)
    used to mean silent dead dormancy forever — every failure swallowed,
    nothing counted. The detector counts CONSECUTIVE inference failures:
    one bad window is still just a bad window, and a recovered model
    still wakes."""

    calls: list = []

    def transcriber(path):
        calls.append(path)
        if len(calls) <= 2:
            raise OSError("model.bin vanished")
        return ["hey parker"]

    detector = WakeDetector(transcriber)
    assert detector.failures == 0
    assert detector.feed(_tone(0.8)) is None and detector.failures == 1
    assert detector.feed(_tone(0.8)) is None and detector.failures == 2
    hit = detector.feed(_tone(0.8))
    assert hit is not None and hit["matched"] == "hey parker"
    assert detector.failures == 0 and detector.inferences == 3


def _loudness(labels: dict[int, str]):
    """A stand-in ASR keyed on loudness: one phrase per amplitude level
    present in the window WAV, in order — so a test can label hops and
    see which of them the transcriber was actually handed."""

    def transcriber(path):
        with wave.open(str(path), "rb") as handle:
            pcm = handle.readframes(handle.getnframes())
        step = int(0.1 * WAKE_SAMPLE_RATE) * 2
        heard: list[str] = []
        for start in range(0, len(pcm) - step + 1, step):
            rms = audioop.rms(pcm[start : start + step], 2)
            if rms < ENERGY_GATE_RMS:
                continue
            phrase = labels[min(labels, key=lambda amp: abs(amp / math.sqrt(2) - rms))]
            if phrase not in heard:
                heard.append(phrase)
        return [" ".join(heard)] if heard else []

    return transcriber


def test_post_wake_audio_never_slides_out_of_the_tail_window():
    """F2: after a wake the lane transcribed a 2.4 s ROLLING window per hop
    — observed per-hop contents A, AB, ABC, ABCD, BCDE: the first words
    after the wake phrase were erased by the fifth hop, and sub-hop audio
    never ran at all. After ``begin_tail`` the window grows from the
    cleared wake point and holds everything he said, so every tail frame
    is a superset transcript; ``finish`` transcribes the sub-hop
    remainder exactly once."""

    from app.parker import converse_router

    labels = {7000: "hey parker", 1000: "A", 2000: "B", 3000: "C", 4000: "D", 5000: "E", 6000: "F"}
    detector = WakeDetector(_loudness(labels))
    hit = detector.feed(_tone(0.8, amplitude=7000))
    assert hit is not None and hit["matched"] == "hey parker"
    assert converse_router.TAIL_WINDOW_SECONDS == converse_router.WAKE_TAIL_SECONDS + wake.HOP_SECONDS
    detector.begin_tail(converse_router.TAIL_WINDOW_SECONDS)  # what the route does on the hit
    windows = [
        detector.hear(_tone(0.7, amplitude=amp))["heard"] for amp in (1000, 2000, 3000, 4000, 5000)
    ]
    assert windows == ["A", "A B", "A B C", "A B C D", "A B C D E"]
    assert detector.hear(_tone(0.4, amplitude=6000)) is None  # sub-hop: no inference by itself
    finished = detector.finish()
    assert finished is not None and finished["heard"] == "A B C D E F"
    assert detector.inferences == 7
    assert detector.finish() is None  # nothing new: never a second inference
    assert detector.inferences == 7


# ---------------------------------------------------------------------------
# Greeting latch: a Parkinsonian pause longer than the window
# ---------------------------------------------------------------------------

_FRAME = 1365  # samples per browser frame on the wake lane (16 kHz)
HEY, PARKING, NAME = 220.0, 440.0, 880.0  # utterances stood up as tones


def _speech(labels: dict[float, str]):
    """A stand-in ASR keyed on the WINDOW'S CONTENT: it reads the window
    WAV and "hears" one phrase per tone present, in order. A tone that
    slid out of the rolling window is not heard, so the temporal tests
    below model the detector's memory honestly."""

    def transcriber(path):
        with wave.open(str(path), "rb") as handle:
            pcm = handle.readframes(handle.getnframes())
        step = int(0.1 * WAKE_SAMPLE_RATE) * 2
        heard: list[str] = []
        for start in range(0, len(pcm) - step + 1, step):
            chunk = pcm[start : start + step]
            if audioop.rms(chunk, 2) < 4000:
                continue  # an onset/offset slice, not a full tone
            hz = audioop.cross(chunk, 2) / 2 / 0.1
            phrase = labels[min(labels, key=lambda f: abs(f - hz))]
            if phrase not in heard:
                heard.append(phrase)
        return [" ".join(heard)] if heard else []

    return transcriber


def _stream(detector: WakeDetector, pcm: bytes) -> list[dict]:
    """Feed browser-sized frames; every hit the detector fired."""

    hits = []
    for start in range(0, len(pcm), _FRAME * 2):
        hit = detector.feed(pcm[start : start + _FRAME * 2])
        if hit:
            hits.append(hit)
    return hits


def _paused(gap: float) -> bytes:
    return (
        _silence(0.5)
        + _tone(0.8, hz=HEY)
        + _silence(gap)
        + _tone(0.8, hz=NAME)
        + _silence(1.6)
    )


def test_a_greeting_then_a_long_pause_then_his_name_wakes_with_the_tail():
    """"Hey" ... 3.2 s ... "Parker, can you help me". No 2.4 s window ever
    holds both words (keyless repro: 8 inferences, the last four hearing a
    bare "parker"; the real base model missed it for three voices). The
    greeting is latched across the pause; his name wakes, with the tail."""

    detector = WakeDetector(_speech({HEY: "hey", NAME: "parker can you help me"}))
    hits = _stream(detector, _paused(3.2))
    assert len(hits) == 1, hits
    assert hits[0]["tail"] == "can you help me"
    assert 0 < hits[0]["latch_s"] <= wake.GREETING_LATCH_SECONDS
    assert detector.feed(_tone(0.2, hz=NAME)) is None  # one utterance, one wake


@pytest.mark.parametrize("name_window", ["um parker", "par ker", "on parker"])
def test_a_slip_or_filler_before_his_name_after_the_pause_still_wakes(name_window):
    """The real model heard a synthesized "um parker" as "On Parker". The
    latched greeting goes through the same single-window grammar, so one
    filler/slip token before the name is tolerated across the pause
    exactly as it is within one breath."""

    detector = WakeDetector(_speech({HEY: "hey", NAME: name_window}))
    hits = _stream(detector, _paused(3.2))
    assert len(hits) == 1 and "latch_s" in hits[0], hits


@pytest.mark.parametrize("drain_window", ["okay", "thank you"])
def test_a_cut_syllable_hallucination_does_not_cancel_the_greeting(drain_window):
    """While "hey" drains out of the window the real model hears "Hey",
    "Hey", then "Okay." / "" on the cut tail (three voices). A word or two
    on a cut syllable is not him moving on — only a sentence clears the
    latch. The latch clock is AUDIO time: 4.6 s of audio passed."""

    replies = iter([["hey"], [drain_window], ["parker"]])
    detector = WakeDetector(lambda path: next(replies))
    assert detector.feed(_tone(0.8)) is None  # "hey": armed
    assert detector.feed(_tone(0.8)) is None  # the cut-tail window
    assert detector.feed(_silence(3.0)) is None  # gated: silence never touches it
    hit = detector.feed(_tone(0.8))
    assert hit is not None and hit["matched"] == "hey parker"
    assert hit["latch_s"] == 4.6


def test_a_stale_greeting_never_lets_a_bare_parker_wake():
    """"Hey" said to someone in the room; ten seconds later the TV says
    "Parker". The latch is bounded in audio time (this streams in
    milliseconds of wall time with the default clock)."""

    detector = WakeDetector(_speech({HEY: "hey", NAME: "parker"}))
    assert _stream(detector, _paused(10.0)) == []


def test_intervening_speech_clears_the_greeting_latch():
    """"Hey" ... "I'm parking the car" ... "Parker": a sentence between the
    greeting and the name means he moved on. Pins that the latch is not
    time-only (the tempting simpler design)."""

    detector = WakeDetector(
        _speech({HEY: "hey", PARKING: "i'm parking the car", NAME: "parker"})
    )
    audio = (
        _silence(0.5)
        + _tone(0.8, hz=HEY)
        + _silence(1.0)
        + _tone(1.2, hz=PARKING)
        + _silence(2.6)
        + _tone(0.8, hz=NAME)
        + _silence(1.6)
    )
    assert _stream(detector, audio) == []


def test_a_bare_article_never_arms_the_latch():
    """The review negative ("a parker" is not a greeting) carried across
    the pause: only greeting tokens arm the latch."""

    detector = WakeDetector(_speech({HEY: "a", NAME: "parker"}))
    assert _stream(detector, _paused(3.2)) == []


def test_the_fake_asr_hears_both_words_when_the_pause_fits_the_window():
    """Harness self-check: a 1.0 s pause keeps both tones in one 2.4 s
    window and the unchanged single-window matcher wakes on its own — so
    the temporal tests above are not tautologies of the fake."""

    detector = WakeDetector(_speech({HEY: "hey", NAME: "parker"}))
    hits = _stream(detector, _paused(1.0))
    assert len(hits) == 1 and hits[0]["matched"] == "hey parker", hits
    assert "latch_s" not in hits[0]


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


def test_wake_lane_is_honest_when_the_model_files_are_missing(db, monkeypatch, wake_url):
    """F5: the realistic first-run failure — weights not cached while
    offline, hub unreachable, a half-downloaded snapshot — comes out of
    huggingface_hub as LocalEntryNotFoundError, a FileNotFoundError. It
    used to escape the store and kill the socket with no frame, so the
    page took the "network hiccup" retry path with power persisted ON
    and the mic open. Through a REAL store it must reach the same honest
    `unavailable` frame as the never-installed case, before any
    registration."""

    from app.parker import converse_router
    from app.parker.companion_power import authority
    from tests.test_converse import make_store

    def missing():
        raise FileNotFoundError("cache miss")

    monkeypatch.setattr(converse_router, "converse_store", make_store(db, loader=missing))
    with client.websocket_connect(wake_url) as ws:
        frame = ws.receive_json()
    assert frame["type"] == "unavailable"
    assert "local voice model" in frame["text"]
    assert authority.snapshot()["live"]["wake"] == 0  # nothing leaked into the authority


def test_wake_lane_gives_up_after_repeated_inference_failures(monkeypatch, wake_url):
    """F5: the warmed model dies under the lane (model.bin removed, the
    temp dir unwritable). After WAKE_FATAL_FAILURES consecutive failing
    windows the lane says so and closes, so the page powers off honestly
    instead of listening to nothing forever. The warmed model is never
    discarded here; the next power-on starts a fresh counter."""

    from app.parker import converse_router
    from starlette.websockets import WebSocketDisconnect

    def dead(path):
        raise OSError("model.bin vanished")

    monkeypatch.setattr(converse_router.converse_store, "transcriber", lambda: dead)
    monkeypatch.setattr("app.parker.converse.write_receipt", lambda entry: None)
    with client.websocket_connect(wake_url) as ws:
        for _ in range(converse_router.WAKE_FATAL_FAILURES):
            ws.send_json({"type": "audio", "data": _b64(_tone(0.8))})
        ws.send_json({"type": "end"})
        frame = ws.receive_json()
        assert frame["type"] == "unavailable" and "local voice model" in frame["text"]
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()  # the lane closed itself

    # One or two bad windows never end dormancy: the lane is still there,
    # the counter reset on the first good window, and that window wakes.
    calls: list = []

    def flaky(path):
        calls.append(path)
        if len(calls) < converse_router.WAKE_FATAL_FAILURES:
            raise OSError("model.bin vanished")
        return ["hey parker"]

    monkeypatch.setattr(converse_router.converse_store, "transcriber", lambda: flaky)
    with client.websocket_connect(wake_url) as ws:
        for _ in range(converse_router.WAKE_FATAL_FAILURES):
            ws.send_json({"type": "audio", "data": _b64(_tone(0.8))})
        assert ws.receive_json()["type"] == "wake"
        ws.send_json({"type": "end"})
