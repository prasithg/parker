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


def sample_mic_level(
    seconds: float = 1.5,
    sample_rate: int = SAMPLE_RATE,
    *,
    sampler: Callable[[float], tuple[bytes, str]] | None = None,
) -> dict:
    """Read a short window from the default mic and return level stats.

    This is the onboarding wizard's microphone moment: the first real
    input-stream open, so macOS raises the TCC permission prompt here —
    in context, with a level meter — instead of at some random first
    conversation. Frames live in memory only and are discarded; even the
    talk loop's temporary-file contract is stronger than needed here.

    ``sampler`` is injectable for tests: it returns (raw int16 bytes,
    device name).
    """

    def _default_sampler(window: float) -> tuple[bytes, str]:
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError(TALK_DEPS_HINT) from exc
        device = sd.query_devices(kind="input")
        name = device.get("name", "unknown") if isinstance(device, dict) else "unknown"
        frames = sd.rec(
            int(window * sample_rate), samplerate=sample_rate, channels=1, dtype="int16"
        )
        sd.wait()
        return frames.tobytes(), name

    window = min(max(seconds, 0.2), 5.0)
    raw, device_name = (sampler or _default_sampler)(window)
    rms, peak = _int16_rms_peak(raw)
    return {
        "seconds": window,
        "rms": round(rms, 1),
        "peak": peak,
        "device": device_name,
        # A dead mic (or a denied permission) reads all-zero; the wizard
        # uses this to say "we can't hear anything" instead of a raw number.
        "heard_anything": peak > 0,
    }


def _int16_rms_peak(raw: bytes) -> tuple[float, int]:
    import struct

    count = len(raw) // 2
    if count == 0:
        return 0.0, 0
    values = struct.unpack(f"<{count}h", raw[: count * 2])
    peak = max(abs(v) for v in values)
    rms = (sum(v * v for v in values) / count) ** 0.5
    return rms, peak


def load_vad_recorder(
    sample_rate: int = SAMPLE_RATE,
    *,
    silence_after_speech_sec: float = 1.2,
    start_grace_sec: float = 4.0,
    energy_threshold: float = 300.0,
) -> Recorder:
    """Recorder with energy-based end-pointing — a drop-in ``Recorder``.

    The ``seconds`` argument becomes the *maximum* window; recording ends
    early once speech has been heard and is followed by
    ``silence_after_speech_sec`` of quiet. Effortful speech pauses
    mid-utterance, so the silence window is generous by default and never
    cuts in before ``start_grace_sec`` if no speech has been detected yet.
    Same privacy contract as the fixed recorder: the caller deletes the
    file right after transcription.
    """

    try:
        import numpy as np
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError(TALK_DEPS_HINT) from exc

    chunk_sec = 0.1
    chunk_frames = int(chunk_sec * sample_rate)

    def _record(path: Path, seconds: float) -> None:
        collected: list[bytes] = []
        heard_speech = False
        quiet_run = 0.0
        elapsed = 0.0
        with sd.InputStream(
            samplerate=sample_rate, channels=1, dtype="int16", blocksize=chunk_frames
        ) as stream:
            while elapsed < seconds:
                chunk, _overflowed = stream.read(chunk_frames)
                collected.append(chunk.tobytes())
                elapsed += chunk_sec
                rms = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))
                if rms >= energy_threshold:
                    heard_speech = True
                    quiet_run = 0.0
                else:
                    quiet_run += chunk_sec
                    if heard_speech and quiet_run >= silence_after_speech_sec:
                        break
                    if not heard_speech and elapsed >= start_grace_sec:
                        break  # nothing said at all — end the window early
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(b"".join(collected))

    return _record
