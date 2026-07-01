"""Configurable local ASR over manifest clips, with a content-hash cache.

Transcription is faster-whisper on this machine (same optional dependency
as the voice demo seam). Results are cached under the Operations
artifacts directory keyed by (clip sha256, ASR config), so re-scoring and
model comparisons never re-run ASR on unchanged audio. The cache holds
transcript text and timing only — no audio is ever copied.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .manifest import Clip

# Maps a clip to raw segment texts; injectable so tests never need audio.
SegmentTranscriber = Callable[[Clip], list[str]]

CACHE_DIRNAME = "asr_cache"


@dataclass(frozen=True)
class ASRConfig:
    model_size: str = "tiny"
    beam_size: int = 5
    initial_prompt: str | None = None

    @property
    def key(self) -> str:
        prompt_part = (
            hashlib.sha256(self.initial_prompt.encode()).hexdigest()[:8]
            if self.initial_prompt
            else "none"
        )
        return f"{self.model_size}-b{self.beam_size}-p{prompt_part}"


@dataclass
class ASRResult:
    segments: list[str]
    runtime_sec: float
    cached: bool

    @property
    def text(self) -> str:
        return " ".join(part.strip() for part in self.segments).strip()


def load_whisper_transcriber(config: ASRConfig) -> SegmentTranscriber:
    """Real faster-whisper transcriber for one config; language comes per clip."""

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover — exercised via voice-deps hint
        from app.voice.transcribe import VOICE_DEPS_HINT

        raise RuntimeError(VOICE_DEPS_HINT) from exc

    model = WhisperModel(config.model_size, device="cpu", compute_type="int8")

    def _transcribe(clip: Clip) -> list[str]:
        segments, _info = model.transcribe(
            str(clip.path),
            language=clip.language if clip.language in ("en", "it", "zh") else None,
            beam_size=config.beam_size,
            initial_prompt=config.initial_prompt,
        )
        return [segment.text for segment in segments]

    return _transcribe


class CachedASR:
    """Wraps a transcriber with a JSON cache in the artifacts directory."""

    def __init__(self, config: ASRConfig, cache_root: Path, transcriber: SegmentTranscriber | None = None):
        self.config = config
        self.cache_dir = cache_root / CACHE_DIRNAME
        self._transcriber = transcriber
        self.live_runs = 0

    def _cache_path(self, clip: Clip) -> Path:
        return self.cache_dir / f"{clip.sha256}.{self.config.key}.json"

    def transcribe(self, clip: Clip) -> ASRResult:
        cache_path = self._cache_path(clip)
        if cache_path.is_file():
            payload = json.loads(cache_path.read_text())
            return ASRResult(payload["segments"], payload["runtime_sec"], cached=True)
        if self._transcriber is None:
            self._transcriber = load_whisper_transcriber(self.config)
        start = time.monotonic()
        segments = self._transcriber(clip)
        runtime = time.monotonic() - start
        self.live_runs += 1
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps({"segments": segments, "runtime_sec": runtime, "config": self.config.key})
        )
        return ASRResult(segments, runtime, cached=False)
