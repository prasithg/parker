"""Local text-to-speech for the talk loop — Parker answers out loud.

Uses macOS ``say`` (no dependencies, fully on-device). Speaking is
blocking by design: the microphone must never be listening while Parker
talks, or it would transcribe its own voice. On systems without ``say``
the speaker degrades to a no-op and the printed transcript remains the
interface.

Config: ``PARKER_TTS_ENABLED`` (default on), ``PARKER_TTS_VOICE``,
``PARKER_TTS_RATE_WPM`` — set by the family administrator like every
other Parker setting.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Callable

# A speaker reads one response aloud, blocking until done.
Speaker = Callable[[str], None]


def load_local_speaker() -> Speaker:
    """Build a speaker backed by macOS ``say``; silent no-op elsewhere."""

    from app.config import settings

    if not settings.parker_tts_enabled or shutil.which("say") is None:
        return lambda text: None

    voice = settings.parker_tts_voice.strip()
    rate = settings.parker_tts_rate_wpm

    def _speak(text: str) -> None:
        if not text.strip():
            return
        command = ["say"]
        if voice:
            command += ["-v", voice]
        if rate > 0:
            command += ["-r", str(rate)]
        command.append(text)
        try:
            subprocess.run(command, check=False, capture_output=True, timeout=60)
        except (subprocess.SubprocessError, OSError):
            pass  # speech is best-effort; the printed transcript remains

    return _speak
