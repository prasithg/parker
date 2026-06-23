"""ElevenLabs voice clone helper for ParkinsClaw calls."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable

import httpx

from app.config import settings

logger = logging.getLogger("parker.voice.clone")

ELEVENLABS_API_BASE = "https://api.elevenlabs.io/v1"


class ElevenLabsVoiceCloneError(RuntimeError):
    """Raised when ElevenLabs voice clone operations fail."""


class ElevenLabsVoiceCloner:
    """Small async client for ElevenLabs instant voice cloning."""

    def __init__(self, api_key: str | None = None, api_base: str = ELEVENLABS_API_BASE) -> None:
        self.api_key = api_key if api_key is not None else settings.elevenlabs_api_key
        self.api_base = api_base.rstrip("/")

    @property
    def configured(self) -> bool:
        """Return True when the API key is available."""

        return bool(self.api_key)

    async def list_voices(self) -> list[dict[str, Any]]:
        """List available voices for the configured ElevenLabs account."""

        response = await self._request("GET", "/voices")
        voices = response.get("voices", [])
        return voices if isinstance(voices, list) else []

    async def create_voice_clone(
        self,
        name: str,
        audio_files: Iterable[str | Path],
        description: str | None = None,
        labels: dict[str, str] | None = None,
    ) -> str:
        """Create a voice clone from audio files and return the resulting voice ID."""

        paths = [Path(path) for path in audio_files]
        if not paths:
            raise ValueError("At least one audio file is required to create a voice clone")
        missing = [str(path) for path in paths if not path.exists()]
        if missing:
            raise FileNotFoundError(f"Audio file(s) not found: {', '.join(missing)}")

        handles: list[Any] = []
        try:
            files = []
            for path in paths:
                handle = path.open("rb")
                handles.append(handle)
                files.append(("files", (path.name, handle, _content_type(path))))

            data: dict[str, Any] = {"name": name}
            if description:
                data["description"] = description
            if labels:
                import json

                data["labels"] = json.dumps(labels)

            response = await self._request("POST", "/voices/add", data=data, files=files)
        finally:
            for handle in handles:
                handle.close()

        voice_id = response.get("voice_id") or response.get("voiceId")
        if not voice_id:
            raise ElevenLabsVoiceCloneError("ElevenLabs did not return a voice_id")
        logger.info("Created ElevenLabs voice clone %s (%s)", name, voice_id)
        return str(voice_id)

    async def get_voice_id(self, preferred_name: str | None = None) -> str:
        """Return configured voice ID, or find one by name in ElevenLabs voices."""

        if settings.elevenlabs_voice_id:
            return settings.elevenlabs_voice_id
        if not preferred_name:
            return ""
        for voice in await self.list_voices():
            if str(voice.get("name", "")).lower() == preferred_name.lower():
                return str(voice.get("voice_id") or voice.get("voiceId") or "")
        return ""

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if not self.api_key:
            raise ElevenLabsVoiceCloneError("ElevenLabs API key is not configured")
        headers = kwargs.pop("headers", {})
        headers["xi-api-key"] = self.api_key
        async with httpx.AsyncClient(base_url=self.api_base, timeout=60.0) as client:
            response = await client.request(method, path, headers=headers, **kwargs)
        if response.status_code >= 400:
            raise ElevenLabsVoiceCloneError(
                f"ElevenLabs API error {response.status_code}: {response.text[:300]}"
            )
        data = response.json()
        return data if isinstance(data, dict) else {"data": data}


def _content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        return "audio/mpeg"
    if suffix == ".wav":
        return "audio/wav"
    if suffix == ".m4a":
        return "audio/mp4"
    if suffix == ".ogg":
        return "audio/ogg"
    return "application/octet-stream"


async def create_voice_clone(
    name: str,
    audio_files: Iterable[str | Path],
    description: str | None = None,
    labels: dict[str, str] | None = None,
) -> str:
    """Convenience wrapper around ElevenLabsVoiceCloner.create_voice_clone."""

    return await ElevenLabsVoiceCloner().create_voice_clone(name, audio_files, description, labels)


async def list_voices() -> list[dict[str, Any]]:
    """Convenience wrapper to list ElevenLabs voices."""

    return await ElevenLabsVoiceCloner().list_voices()


async def get_voice_id(preferred_name: str | None = None) -> str:
    """Convenience wrapper returning the configured or matching ElevenLabs voice ID."""

    return await ElevenLabsVoiceCloner().get_voice_id(preferred_name)
