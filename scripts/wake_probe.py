"""Real-audio wake probe: synthesized speech through the REAL stack.

Runs OUTSIDE the (keyless, fake-transcriber) test suite: macOS `say`
synthesizes utterances, afconvert renders 16 kHz mono s16le, and the
audio streams through the real WakeDetector + the real local
faster-whisper model in browser-sized frames — the same shape the
companion's wake lane sends. Proves the lane end-to-end without a human
in the room (the human/room pass remains Pras's gate).

Usage: backend/.venv/bin/python scripts/wake_probe.py
Exit 0 = every positive woke and no negative did.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.parker.wake import WAKE_SAMPLE_RATE, WakeDetector  # noqa: E402
from app.voice.transcribe import load_local_transcriber  # noqa: E402

POSITIVES = ["hey parker", "hey, parker", "um, hey parker, can you help me"]
NEGATIVES = ["hey partner", "the parking lot is full", "nice weather today"]
FRAME_SAMPLES = 1365  # one browser ScriptProcessor callback resampled to 16 kHz


def synth(text: str, out_dir: Path, voice: str | None = None) -> Path:
    aiff = out_dir / "utterance.aiff"
    wav = out_dir / "utterance.wav"
    cmd = ["say", "-o", str(aiff)]
    if voice:
        cmd += ["-v", voice]
    subprocess.run(cmd + [text], check=True)
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(wav)],
        check=True,
        capture_output=True,
    )
    return wav


def stream(detector: WakeDetector, wav_path: Path):
    with wave.open(str(wav_path), "rb") as handle:
        assert handle.getframerate() == WAKE_SAMPLE_RATE, handle.getframerate()
        pcm = handle.readframes(handle.getnframes())
    # lead-in/out silence so the window slides like a real room
    silence = b"\x00\x00" * (WAKE_SAMPLE_RATE // 2)
    pcm = silence + pcm + silence
    hit = None
    for start in range(0, len(pcm), FRAME_SAMPLES * 2):
        result = detector.feed(pcm[start : start + FRAME_SAMPLES * 2])
        if result and hit is None:
            hit = result
    return hit


def main() -> int:
    print("loading local transcriber (faster-whisper)…")
    transcriber = load_local_transcriber()
    failures = 0
    for text in POSITIVES:
        with tempfile.TemporaryDirectory() as tmp:
            detector = WakeDetector(transcriber)
            hit = stream(detector, synth(text, Path(tmp)))
        ok = hit is not None
        failures += 0 if ok else 1
        print(
            f"{'WAKE ' if ok else 'MISS '} positive {text!r}"
            + (f" -> matched={hit['matched']!r} infer_ms={hit['infer_ms']} inferences={detector.inferences}" if ok else f" (inferences={detector.inferences})")
        )
    for text in NEGATIVES:
        with tempfile.TemporaryDirectory() as tmp:
            detector = WakeDetector(transcriber)
            hit = stream(detector, synth(text, Path(tmp)))
        ok = hit is None
        failures += 0 if ok else 1
        print(
            f"{'QUIET' if ok else 'FALSE'} negative {text!r}"
            + ("" if ok else f" -> wrongly matched={hit['matched']!r}")
        )
    print("PASS" if failures == 0 else f"FAIL ({failures})")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
