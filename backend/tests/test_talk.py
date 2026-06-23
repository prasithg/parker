"""Microphone talk seam: record → transcribe → text loop, audio deleted.

The real sounddevice dependency is optional and never imported here —
every test injects a fake recorder/transcriber. What these pin down:
the spoken utterance flows through the real TextSession routing, the
recording is deleted after transcription (success AND failure paths —
the no-audio-retention invariant), the requested duration reaches the
recorder, and the missing-dependency error points at make voice-deps.
"""

import sys

import pytest

from app.db.models import CapturedIntent
from app.demo.talk import run_talk
from app.voice.record import load_local_recorder


class SpyRecorder:
    """Writes a stand-in wav and remembers where/how long it recorded."""

    def __init__(self):
        self.path = None
        self.seconds = None

    def __call__(self, path, seconds):
        self.path = path
        self.seconds = seconds
        path.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt not-real-audio")


def fake_transcriber(lines):
    def _transcribe(path):
        return list(lines)

    return _transcribe


def test_talk_routes_spoken_utterance_through_text_loop(db):
    recorder = SpyRecorder()
    exchanges = run_talk(
        db,
        seconds=4.0,
        recorder=recorder,
        transcriber=fake_transcriber(["Remind me to do my stretches"]),
    )

    assert recorder.seconds == 4.0
    assert [e["kind"] for e in exchanges] == ["captured"]
    intent = db.query(CapturedIntent).one()
    assert intent.requested_action == "remind"


def test_talk_deletes_recording_after_transcription(db):
    recorder = SpyRecorder()
    run_talk(db, recorder=recorder, transcriber=fake_transcriber(["Remind me to stretch"]))

    assert recorder.path is not None
    assert not recorder.path.exists()
    assert not recorder.path.parent.exists()


def test_talk_deletes_recording_when_transcription_fails(db):
    recorder = SpyRecorder()

    def exploding_transcriber(path):
        raise RuntimeError("model blew up")

    with pytest.raises(RuntimeError, match="model blew up"):
        run_talk(db, recorder=recorder, transcriber=exploding_transcriber)

    assert recorder.path is not None
    assert not recorder.path.exists()
    assert db.query(CapturedIntent).count() == 0


def test_talk_with_silence_is_a_noop(db):
    exchanges = run_talk(db, recorder=SpyRecorder(), transcriber=fake_transcriber([]))

    assert exchanges == []
    assert db.query(CapturedIntent).count() == 0


def test_talk_without_dependency_explains_install(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    with pytest.raises(RuntimeError, match="make voice-deps"):
        load_local_recorder()
