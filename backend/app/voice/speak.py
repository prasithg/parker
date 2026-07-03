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


def list_say_voices() -> list[dict]:
    """The installed macOS ``say`` voices for the onboarding voice picker.

    Empty on non-macOS or when ``say`` is unavailable — the wizard then
    hides the picker instead of erroring.
    """

    import re

    if shutil.which("say") is None:
        return []
    try:
        result = subprocess.run(
            ["say", "-v", "?"], capture_output=True, timeout=10, text=True, check=True
        )
    except (subprocess.SubprocessError, OSError):
        return []
    voices = []
    # Lines look like: "Samantha            en_US    # Hello! My name is Samantha."
    line_shape = re.compile(r"^(.+?)\s{2,}([A-Za-z]{2}[A-Za-z_-]*)\s+#\s*(.*)$")
    for line in result.stdout.splitlines():
        match = line_shape.match(line.strip())
        if match:
            name, lang, sample = match.groups()
            voices.append({"name": name.strip(), "lang": lang, "sample": sample})
    return voices


def speak_once(text: str, *, voice: str = "", rate_wpm: int = 0) -> bool:
    """Speak one line with explicit voice/rate — the wizard's preview.

    Unlike ``load_local_speaker`` this ignores the saved settings, so
    the family can audition a voice before writing it to config.json.
    Blocking, best-effort; returns whether anything was spoken.
    """

    if not text.strip() or shutil.which("say") is None:
        return False
    command = ["say"]
    if voice.strip():
        command += ["-v", voice.strip()]
    if rate_wpm > 0:
        command += ["-r", str(rate_wpm)]
    command.append(text.strip())
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=60)
        return True
    except (subprocess.SubprocessError, OSError):
        return False


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
