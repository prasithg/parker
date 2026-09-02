"""Local "Hey Parker" wake detection for the companion's dormant state.

While the companion is powered on but dormant, the page streams mic PCM
(16 kHz mono s16le) to the local engine over ``/parker/converse/wake``.
Nothing leaves this machine: detection runs on the SAME warmed
faster-whisper transcriber the push-button lane uses, over a short
rolling window, and only when the window actually contains energy — a
quiet living room costs nothing.

The matcher is deliberately deterministic and unit-pinned: a greeting
token within two tokens before a parker-like token. Effortful speech
("hey... parker", "hey parka", "hey par ker") wakes; ambient mentions of
Parker without a greeting, and real words ("hey partner", "parking"), do
not. Calibrated for a Parkinson's speaker by chairman decision
(2026-09-01): while dormant the microphone is already held locally and
nothing streams, so a missed wake costs more than an occasional extra
one — the parker-like set stays generous; only accidental greeting tokens
(a bare "a") were removed.

The detection also carries the *tail* — whatever he said after the wake
phrase inside the window — so "Hey Parker, can you help me" reaches the
live line as a request, not just a wake (the page forwards it as the
session's first frame).

Plan of record: docs/plans/2026-09-01-wake-word.md.
"""

from __future__ import annotations

import audioop
import logging
import re
import tempfile
import time
import wave
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("parker.wake")

WAKE_SAMPLE_RATE = 16000
WINDOW_SECONDS = 2.4
HOP_SECONDS = 0.7
# RMS of int16 samples below this is a quiet room: never run the model.
ENERGY_GATE_RMS = 260

_GREETINGS = {"hey", "hay", "hi", "eh", "hei"}
_PARKER_EXACT = {"parker", "parka", "barker", "packer", "parcker", "parkers"}
# Real "park…" words a TV says all the time. Anything ELSE that starts
# with "park" after a greeting is treated as his attempt at "Parker"
# ("parkuh", "parkah", a trailing syllable that slurred).
_PARK_WORDS = {"park", "parks", "parked", "parking", "parkway", "parkland", "parkin"}
MAX_TAIL_WORDS = 20
# Burst-focused second look (experiment, measured by scripts/wake_soak.py
# over-TV rows): over continuous TV speech the 2.4 s window is mostly TV
# and the model transcribes the TV through a short "hey Parker". When a
# hop rises above the room's recent level, ALSO transcribe just the
# loud burst (the last BURST_SECONDS) — a window that is mostly him.
BURST_SECONDS = 1.3
BURST_RATIO = 1.25
# The adaptive gate's memory: ~60 s of hops. The "room" is a LOW
# percentile of that memory, and the gate only engages once the room has
# been continuously energetic for ~20 s (a TV) — so a few seconds of him
# talking to someone do not become the background his wake must rise
# above. It is not perfect: ~24 s of unbroken talk at one level would arm
# it until a 2.4 s pause drains the window — one reason it ships opt-in
# (fresh reviews of PR #40, 2026-09-02).
_BACKGROUND_HOPS = 86
_BACKGROUND_PERCENTILE = 0.25
_BACKGROUND_STEADY_HOPS = 28


def _levenshtein_leq1(a: str, b: str) -> bool:
    """True when edit distance between *a* and *b* is 0 or 1."""

    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la > lb:
        a, b, la, lb = b, a, lb, la
    # la <= lb, differ by 0 or 1
    i = j = edits = 0
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        edits += 1
        if edits > 1:
            return False
        if la == lb:
            i += 1
            j += 1
        else:
            j += 1  # skip one char of the longer string
    edits += (lb - j) + (la - i)
    return edits <= 1


def _parker_close(token: str) -> bool:
    return token in _PARKER_EXACT or _levenshtein_leq1(token, "parker")


def _parker_like(token: str) -> bool:
    if _parker_close(token):
        return True
    return token.startswith("park") and len(token) <= 7 and token not in _PARK_WORDS


def _tokens(text: str) -> list[str]:
    normalized = re.sub(r"[^a-z' ]+", " ", (text or "").lower())
    return [t for t in normalized.split() if t]


def wake_match(text: str) -> Optional[tuple[str, str]]:
    """``(matched span, tail)`` or None.

    A greeting token must appear within the two tokens before a
    parker-like token — "hey parker", "hey, um, parker" wake; a bare
    "parker" (the TV talking about Parker) does not. A split second
    syllable ("hey par ker") is joined before matching. The tail is what
    followed the wake phrase, bounded to MAX_TAIL_WORDS.
    """

    tokens = _tokens(text)
    for index, token in enumerate(tokens):
        span_end = index
        if _parker_like(token):
            pass
        elif (
            index + 1 < len(tokens)
            and len(token) <= 4
            and _parker_close(token + tokens[index + 1])
        ):
            span_end = index + 1  # "par" + "ker": a syllable that split
        else:
            continue
        lookback = tokens[max(0, index - 2) : index]
        if any(t in _GREETINGS for t in lookback):
            matched = " ".join(tokens[max(0, index - 2) : span_end + 1])
            tail = " ".join(tokens[span_end + 1 : span_end + 1 + MAX_TAIL_WORDS])
            return matched, tail
    return None


def wake_heard(text: str) -> Optional[str]:
    """The matched wake span, or None (see ``wake_match``)."""

    match = wake_match(text)
    return match[0] if match else None


def _write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(WAKE_SAMPLE_RATE)
        handle.writeframes(pcm)


class WakeDetector:
    """Energy-gated rolling-window keyword spotting over a transcriber.

    ``feed`` is synchronous and safe to run on a worker thread; the route
    serializes calls per connection. A detection clears the window so one
    utterance cannot double-fire.
    """

    def __init__(
        self,
        transcriber: Callable[[Path], list[str]],
        *,
        clock: Callable[[], float] = time.monotonic,
        hop_seconds: float = HOP_SECONDS,
        relative_gate: float = 0.0,
        burst_window: bool = False,
    ) -> None:
        self._transcriber = transcriber
        self._clock = clock
        self._window = bytearray()
        self._samples_since_run = 0
        self._window_samples = int(WINDOW_SECONDS * WAKE_SAMPLE_RATE)
        self._hop_samples = int(hop_seconds * WAKE_SAMPLE_RATE)
        # Adaptive gate (opt-in, ``parker_wake_relative_gate``): with a TV
        # on, every hop is "energetic" and the model runs continuously.
        # When > 0, once the room has been steadily loud for ~20 s, a hop
        # also has to be this many times louder than the room's low
        # percentile — a voice near the mic rises above the TV; the TV's
        # own steady level does not rise above itself. Off by default:
        # a missed wake costs Dad more than CPU costs the machine.
        self._relative_gate = float(relative_gate)
        self._burst_window = bool(burst_window)
        self._recent_rms: list[int] = []
        self.burst_inferences = 0  # second looks at the loud burst alone
        self.inferences = 0  # observable: the energy gate must hold in tests
        self.gated_by_background = 0  # hops the adaptive gate skipped

    def hear(self, pcm16: bytes) -> Optional[dict[str, Any]]:
        """Accumulate audio; on each energetic hop, transcribe the window.

        Returns ``{"heard", "rms", "infer_ms"}`` when an inference ran (the
        transcript may be empty), else None. ``feed`` matches on top of
        this; the post-wake tail lane uses it directly.
        """

        usable = len(pcm16) - (len(pcm16) % 2)
        if usable <= 0:
            return None
        self._window.extend(pcm16[:usable])
        self._samples_since_run += usable // 2
        overflow = len(self._window) // 2 - self._window_samples
        if overflow > 0:
            del self._window[: overflow * 2]
        if self._samples_since_run < self._hop_samples:
            return None
        self._samples_since_run = 0
        window = bytes(self._window)
        rms = audioop.rms(window, 2) if window else 0
        if rms < ENERGY_GATE_RMS:
            self._remember_rms(rms)
            return None  # a quiet room never spins the model
        if self._relative_gate > 0 and self._room_is_steadily_loud():
            ordered = sorted(self._recent_rms)
            background = ordered[int(len(ordered) * _BACKGROUND_PERCENTILE)]
            if rms < self._relative_gate * background:
                self._remember_rms(rms)
                self.gated_by_background += 1
                return None  # steady background (a TV) is not someone speaking up
        self._remember_rms(rms)
        started = self._clock()
        self.inferences += 1
        try:
            with tempfile.TemporaryDirectory(prefix="parker-wake-") as tmp:
                path = Path(tmp) / "window.wav"
                _write_wav(path, window)
                lines = self._transcriber(path)
        except Exception:  # noqa: BLE001 — a bad window must not end dormancy
            logger.warning("wake inference failed", exc_info=True)
            return None
        heard = " ".join(line.strip() for line in lines if line and line.strip())
        return {
            "heard": heard[:200],
            "rms": rms,
            "infer_ms": int((self._clock() - started) * 1000),
            "_started": started,
        }

    def _burst_transcript(self, window: bytes, rms: int) -> str:
        """Transcribe just the recent loud part when it rises above the
        room: over TV speech the full window reads as TV, the burst reads
        as him. Costs one extra inference only on a rise."""

        burst_bytes = int(BURST_SECONDS * WAKE_SAMPLE_RATE) * 2
        if len(window) <= burst_bytes:
            return ""
        burst = window[-burst_bytes:]
        before = window[:-burst_bytes]  # the same window's earlier part: the room just before
        burst_rms = audioop.rms(burst, 2)
        before_rms = max(audioop.rms(before, 2), ENERGY_GATE_RMS)
        if burst_rms < ENERGY_GATE_RMS or burst_rms < BURST_RATIO * before_rms:
            return ""  # no rise: the burst is just more of the same room
        self.burst_inferences += 1
        try:
            with tempfile.TemporaryDirectory(prefix="parker-wake-burst-") as tmp:
                path = Path(tmp) / "burst.wav"
                _write_wav(path, burst)
                lines = self._transcriber(path)
        except Exception:  # noqa: BLE001
            logger.warning("wake burst inference failed", exc_info=True)
            return ""
        return " ".join(line.strip() for line in lines if line and line.strip())[:200]

    def _remember_rms(self, rms: int) -> None:
        self._recent_rms.append(rms)
        if len(self._recent_rms) > _BACKGROUND_HOPS:
            del self._recent_rms[0]

    def _room_is_steadily_loud(self) -> bool:
        """True only after ~20 s of continuously energetic hops — a TV,
        not a person who just spoke for a few seconds."""

        recent = self._recent_rms[-_BACKGROUND_STEADY_HOPS:]
        return len(recent) >= _BACKGROUND_STEADY_HOPS and all(
            value >= ENERGY_GATE_RMS for value in recent
        )

    def feed(self, pcm16: bytes) -> Optional[dict[str, Any]]:
        result = self.hear(pcm16)
        if result is None:
            return None
        started = result.pop("_started")
        match = wake_match(result["heard"])
        if match is None and self._burst_window:
            # Only the wake lane takes the second look (the post-wake tail
            # lane calls hear() directly and never matches).
            burst = self._burst_transcript(bytes(self._window), result["rms"])
            if burst:
                match = wake_match(burst)
                if match is not None:
                    result["heard"] = burst[:200]
            result["infer_ms"] = int((self._clock() - started) * 1000)  # both looks
        if match is None:
            return None
        self._window.clear()  # one utterance, one wake
        result["matched"], result["tail"] = match
        return result
