"""Local microphone capture for the talk demo seam.

Records a short utterance from the default input device with
sounddevice (PortAudio) — entirely on this machine, like the
transcriber. The dependency is optional and lazily imported: install it
with ``make voice-deps``; tests inject a fake recorder instead.

Privacy: callers (``app.demo.talk``) write the recording to a temporary
file that exists only for the seconds it takes to transcribe, then
delete it unconditionally. Transcripts are the only artifact.
"""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Callable

# A recorder captures `seconds` of microphone audio into a wav file.
Recorder = Callable[[Path, float], None]

TALK_DEPS_HINT = (
    "sounddevice is not installed. It is an optional, local-only "
    "dependency: run 'make voice-deps' to install it (recording uses "
    "the default input device on this machine; macOS will ask for "
    "microphone permission on first use)."
)

SAMPLE_RATE = 16_000


def load_local_recorder(sample_rate: int = SAMPLE_RATE) -> Recorder:
    """Build a recorder backed by the default local input device."""

    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError(TALK_DEPS_HINT) from exc

    def _record(path: Path, seconds: float) -> None:
        frames = sd.rec(
            int(seconds * sample_rate), samplerate=sample_rate, channels=1, dtype="int16"
        )
        sd.wait()
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(frames.tobytes())

    return _record
