"""Local-only audio transcription for the voice demo seam.

Transcribes an audio file with faster-whisper (CTranslate2 Whisper)
running entirely on this machine — no cloud speech APIs. The dependency
is optional and lazily imported: install it with ``make voice-deps``;
the core test suite injects a fake transcriber instead.

Privacy: this module only *reads* the input file. It never copies,
moves, or re-encodes audio, and nothing audio-derived is persisted
except the transcript text that flows into the normal capture pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

# A transcriber maps an audio file path to raw utterance lines.
Transcriber = Callable[[Path], list[str]]

VOICE_DEPS_HINT = (
    "faster-whisper is not installed. It is an optional, local-only "
    "dependency: run 'make voice-deps' to install it (the model runs on "
    "this machine; the first run downloads model weights to the local "
    "Hugging Face cache, after which transcription is fully offline)."
)


def load_local_transcriber(model_size: str = "tiny", language: str | None = "en") -> Transcriber:
    """Build a transcriber backed by a local faster-whisper model."""

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(VOICE_DEPS_HINT) from exc

    model = WhisperModel(model_size, device="cpu", compute_type="int8")

    def _transcribe(audio_path: Path) -> list[str]:
        segments, _info = model.transcribe(str(audio_path), language=language)
        return [segment.text for segment in segments]

    return _transcribe


def transcribe_audio(
    audio_path: str | Path,
    *,
    transcriber: Transcriber | None = None,
    model_size: str = "tiny",
) -> list[str]:
    """Transcribe a local audio file into stripped, non-empty utterance lines.

    One line per recognized segment; each line is one utterance for
    ``TextSession.handle``.
    """

    path = Path(audio_path)
    if not path.is_file():
        raise FileNotFoundError(f"audio file not found: {path}")
    transcribe = transcriber or load_local_transcriber(model_size=model_size)
    return [line for line in (raw.strip() for raw in transcribe(path)) if line]
