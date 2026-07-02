"""Local TTS speaker: config-gated, best-effort, never a hard dependency."""

from app.config import settings
from app.voice.speak import load_local_speaker


def test_disabled_tts_is_a_silent_noop(monkeypatch):
    monkeypatch.setattr(settings, "parker_tts_enabled", False)
    speak = load_local_speaker()
    speak("this must not raise or make a sound")


def test_missing_say_binary_degrades_to_noop(monkeypatch):
    monkeypatch.setattr(settings, "parker_tts_enabled", True)
    monkeypatch.setattr("app.voice.speak.shutil.which", lambda _name: None)
    speak = load_local_speaker()
    speak("still fine without say")


def test_empty_text_is_never_spoken(monkeypatch):
    monkeypatch.setattr(settings, "parker_tts_enabled", True)
    calls = []
    monkeypatch.setattr("app.voice.speak.shutil.which", lambda _name: "/usr/bin/say")
    monkeypatch.setattr(
        "app.voice.speak.subprocess.run", lambda *a, **k: calls.append(a)
    )
    speak = load_local_speaker()
    speak("   ")
    assert calls == []
    speak("hello")
    assert len(calls) == 1


def test_voice_and_rate_settings_shape_the_command(monkeypatch):
    monkeypatch.setattr(settings, "parker_tts_enabled", True)
    monkeypatch.setattr(settings, "parker_tts_voice", "Samantha")
    monkeypatch.setattr(settings, "parker_tts_rate_wpm", 165)
    commands = []
    monkeypatch.setattr("app.voice.speak.shutil.which", lambda _name: "/usr/bin/say")
    monkeypatch.setattr(
        "app.voice.speak.subprocess.run", lambda cmd, **k: commands.append(cmd)
    )
    speak = load_local_speaker()
    speak("Okay — I'll bring that up this evening.")
    assert commands[0][:5] == ["say", "-v", "Samantha", "-r", "165"]
