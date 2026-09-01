"""Local "Hey Parker" wake detection for the companion's dormant state.

While the companion is powered on but dormant, the page streams mic PCM
(16 kHz mono s16le) to the local engine over ``/parker/converse/wake``.
Nothing leaves this machine: detection runs on the SAME warmed
faster-whisper transcriber the push-button lane uses, over a short
rolling window, and only when the window actually contains energy — a
quiet living room costs nothing.

The matcher is deliberately deterministic and unit-pinned: a greeting
token within two tokens before a parker-like token. Effortful speech
("hey... parker", "hey parka") wakes; ambient mentions of Parker without
a greeting, and near-words ("hey partner", "parking"), do not.

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

_GREETINGS = {"hey", "hay", "hi", "a", "eh", "hei", "hey,"}
_PARKER_EXACT = {"parker", "parka", "barker", "packer", "parcker", "parkers"}


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


def _parker_like(token: str) -> bool:
    if token in _PARKER_EXACT:
        return True
    return _levenshtein_leq1(token, "parker")


def wake_heard(text: str) -> Optional[str]:
    """The matched wake span, or None.

    A greeting token must appear within the two tokens before a
    parker-like token — "hey parker", "hey, um, parker" wake; a bare
    "parker" (the TV talking about Parker) does not.
    """

    normalized = re.sub(r"[^a-z' ]+", " ", (text or "").lower())
    tokens = [t for t in normalized.split() if t]
    for index, token in enumerate(tokens):
        if not _parker_like(token):
            continue
        lookback = tokens[max(0, index - 2) : index]
        if any(t in _GREETINGS for t in lookback):
            return " ".join(tokens[max(0, index - 2) : index + 1])
    return None


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
    ) -> None:
        self._transcriber = transcriber
        self._clock = clock
        self._window = bytearray()
        self._samples_since_run = 0
        self._window_samples = int(WINDOW_SECONDS * WAKE_SAMPLE_RATE)
        self._hop_samples = int(HOP_SECONDS * WAKE_SAMPLE_RATE)
        self.inferences = 0  # observable: the energy gate must hold in tests

    def feed(self, pcm16: bytes) -> Optional[dict[str, Any]]:
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
            return None  # a quiet room never spins the model
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
        matched = wake_heard(heard)
        if matched is None:
            return None
        self._window.clear()  # one utterance, one wake
        return {
            "heard": heard[:200],
            "matched": matched,
            "rms": rms,
            "infer_ms": int((self._clock() - started) * 1000),
        }
