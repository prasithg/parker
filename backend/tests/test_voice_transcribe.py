"""Local voice transcription seam: audio file → transcript lines → text loop.

The real faster-whisper dependency is optional and never imported here —
every test injects a fake transcriber, so the suite runs without audio
deps. What these tests pin down: line cleanup, the missing-dependency
message, transcript lines flowing through the real TextSession routing
(capture, refusal), and that no audio is ever copied or written.
"""

import sys

import pytest

from app.db.models import CapturedIntent
from app.demo.voice import run_voice_demo
from app.voice.transcribe import transcribe_audio


@pytest.fixture
def audio_file(tmp_path):
    """A stand-in audio file; fake transcribers never parse its bytes."""
    path = tmp_path / "utterance.wav"
    path.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt not-real-audio")
    return path


def fake_transcriber(lines):
    def _transcribe(path):
        return list(lines)

    return _transcribe


def test_transcribe_audio_strips_and_drops_empty_lines(audio_file):
    lines = transcribe_audio(
        audio_file,
        transcriber=fake_transcriber(["  Remind me to water the plants ", "", "   "]),
    )
    assert lines == ["Remind me to water the plants"]


def test_transcribe_audio_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        transcribe_audio(tmp_path / "nope.wav", transcriber=fake_transcriber(["hi"]))


def test_transcribe_audio_without_dependency_explains_install(audio_file, monkeypatch):
    # None in sys.modules makes the lazy import fail even if the optional
    # dependency happens to be installed in this environment.
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    with pytest.raises(RuntimeError, match="make voice-deps"):
        transcribe_audio(audio_file)


def test_voice_demo_feeds_transcript_into_text_loop(db, audio_file):
    exchanges = run_voice_demo(
        db,
        audio_file,
        transcriber=fake_transcriber(
            [
                "Remind me to water the plants",
                "Tell Sarah the physio visit went well",
            ]
        ),
    )

    assert [e["kind"] for e in exchanges] == ["captured", "captured"]
    intents = db.query(CapturedIntent).order_by(CapturedIntent.id).all()
    assert len(intents) == 2
    assert intents[0].requested_action == "remind"
    assert intents[1].requested_action == "message"
    assert intents[1].recipient == "Sarah"


def test_voice_demo_unsafe_line_refused_not_captured(db, audio_file):
    exchanges = run_voice_demo(
        db,
        audio_file,
        transcriber=fake_transcriber(["Should I take half my pills tomorrow?"]),
    )

    assert [e["kind"] for e in exchanges] == ["refused"]
    assert db.query(CapturedIntent).count() == 0


def test_voice_demo_empty_transcript_is_a_noop(db, audio_file):
    assert run_voice_demo(db, audio_file, transcriber=fake_transcriber([])) == []
    assert db.query(CapturedIntent).count() == 0


def test_voice_demo_never_copies_or_writes_audio(db, audio_file, tmp_path):
    original_bytes = audio_file.read_bytes()
    before = {p.name for p in tmp_path.iterdir()}

    run_voice_demo(db, audio_file, transcriber=fake_transcriber(["Remind me to stretch"]))

    assert audio_file.read_bytes() == original_bytes
    assert {p.name for p in tmp_path.iterdir()} == before
