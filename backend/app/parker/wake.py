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
# ``finish``: less new audio than this since the last run is not worth an
# inference (a cut syllable at most).
MIN_FLUSH_SECONDS = 0.25

_GREETINGS = {"hey", "hay", "hi", "eh", "hei"}
_PARKER_EXACT = {"parker", "parka", "barker", "packer", "parcker", "parkers"}
# Real "park…" words a TV says all the time. Anything ELSE that starts
# with "park" after a greeting is treated as his attempt at "Parker"
# ("parkuh", "parkah", a trailing syllable that slurred).
_PARK_WORDS = {"park", "parks", "parked", "parking", "parkway", "parkland", "parkin"}
MAX_TAIL_WORDS = 20
# A greeting latched across a pause: "Hey" ... (a Parkinsonian hesitation
# longer than the window) ... "Parker". Bounded in AUDIO time from the
# last window that held the greeting and nothing else of substance;
# measured silence envelope 6-7 s (F3 bound sweep, keyless and real base
# model). A window with this many non-greeting tokens — a sentence, his or
# the TV's — clears it; a word or two on a cut syllable ("Okay.") does not.
GREETING_LATCH_SECONDS = 6.0
_LATCH_RESET_TOKENS = 3
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
        self._recent_rms: list[int] = []
        # The greeting latch's clock is audio fed, not the wall clock, so
        # scripts that stream faster than real time and the live lane
        # (where audio time == wall time) behave identically.
        self._fed_samples = 0
        self._greeting: Optional[tuple[str, int]] = None  # (token, fed samples)
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
        self._fed_samples += usable // 2
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
        return self._transcribe(window, rms)

    def begin_tail(self, max_seconds: float) -> None:
        """After a wake: stop sliding. The window was cleared at the wake,
        so from here it grows and holds everything he says (up to
        ``max_seconds``) for the tail lane's lifetime — every tail frame is
        a superset transcript and the first post-wake words never slide
        out (F2: a 2.4 s rolling window had erased them by the 5th hop)."""

        self._window_samples = int(max_seconds * WAKE_SAMPLE_RATE)

    def finish(self) -> Optional[dict[str, Any]]:
        """Transcribe the current window once, regardless of the hop: the
        lane's last words before the live line takes over. Same return
        shape as ``hear``; None when less than MIN_FLUSH_SECONDS of audio
        arrived since the last run or the window is quiet."""

        if self._samples_since_run < int(MIN_FLUSH_SECONDS * WAKE_SAMPLE_RATE):
            return None
        self._samples_since_run = 0
        window = bytes(self._window)
        rms = audioop.rms(window, 2) if window else 0
        if rms < ENERGY_GATE_RMS:
            return None
        return self._transcribe(window, rms)

    def _transcribe(self, window: bytes, rms: int) -> Optional[dict[str, Any]]:
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
        }

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
        heard = result["heard"]
        match = wake_match(heard)
        if match is None:
            greeting = self._pending_greeting()
            if greeting is not None:
                # His greeting, carried across the pause, through the same
                # single-window grammar (one filler/slip token tolerated).
                match = wake_match(f"{greeting} {heard}")
                if match is not None:
                    result["latch_s"] = round(
                        (self._fed_samples - self._greeting[1]) / WAKE_SAMPLE_RATE, 1
                    )
        if match is None:
            self._note_greeting(heard)
            return None
        self._greeting = None
        self._window.clear()  # one utterance, one wake
        result["matched"], result["tail"] = match
        return result

    def _pending_greeting(self) -> Optional[str]:
        if self._greeting is None:
            return None
        token, at = self._greeting
        if (self._fed_samples - at) / WAKE_SAMPLE_RATE > GREETING_LATCH_SECONDS:
            self._greeting = None
            return None
        return token

    def _note_greeting(self, heard: str) -> None:
        tokens = _tokens(heard)
        others = [t for t in tokens if t not in _GREETINGS]
        if len(others) >= _LATCH_RESET_TOKENS:
            self._greeting = None  # a sentence — his or the TV's: he moved on
        elif any(t in _GREETINGS for t in tokens):
            self._greeting = (
                next(t for t in tokens if t in _GREETINGS),
                self._fed_samples,
            )
        # else: a word or two on a cut syllable ("Okay.", "Thank you.",
        # "TARAKER" — observed real-model drain windows) change nothing.
