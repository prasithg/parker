"""Continuous talk loop: persistent TextSession across recording turns.

The central invariant these tests pin down: one TextSession lives for
the whole conversation, so repair-choice state carries across recording
windows. Everything uses fake recorders/transcribers — no audio deps.
"""

from app.db.models import CallLog, CapturedIntent
from app.demo.talk import run_talk_loop


class TurnRecorder:
    """Writes a stand-in wav per call; feed transcripts one turn at a time."""

    def __init__(self, transcripts_per_turn: list[list[str]]):
        self._turns = iter(transcripts_per_turn)
        self._remaining: list[str] = []
        self.call_count = 0

    def recorder(self, path, seconds):
        path.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt not-real-audio")
        self.call_count += 1

    def transcriber(self, path) -> list[str]:
        try:
            return next(self._turns)
        except StopIteration:
            return []


def _run(db, turns: list[list[str]], *, call_sid="TEST-LOOP") -> list[dict]:
    tr = TurnRecorder(turns)
    return run_talk_loop(
        db,
        seconds=1.0,
        recorder=tr.recorder,
        transcriber=tr.transcriber,
        call_sid=call_sid,
        max_turns=len(turns),
    )


def test_single_turn_capture(db):
    exchanges = _run(db, [["Remind me to do my stretches"]])

    assert [e["kind"] for e in exchanges] == ["captured"]
    assert db.query(CapturedIntent).one().requested_action == "remind"


def test_repair_choice_selection_spans_turns(db):
    """Turn 1 offers repair choices; turn 2 picks '1' — session must persist."""
    exchanges = _run(db, [
        ["Call... the... you know... the one with the garden..."],
        ["1"],  # selects "set a reminder about this"
    ])

    kinds = [e["kind"] for e in exchanges]
    assert kinds == ["choices", "captured"]
    assert db.query(CapturedIntent).one().requested_action == "reminder"


def test_multiple_captures_across_turns(db):
    exchanges = _run(db, [
        ["Remind me to water the plants"],
        ["Tell Sarah the physio visit went well"],
    ])

    assert [e["kind"] for e in exchanges] == ["captured", "captured"]
    intents = db.query(CapturedIntent).order_by(CapturedIntent.id).all()
    assert intents[0].requested_action == "remind"
    assert intents[1].requested_action == "message"
    assert intents[1].recipient == "Sarah"


def test_silent_turn_skipped_session_continues(db):
    """A silent window (empty transcript) does not break the session."""
    exchanges = _run(db, [
        [],                          # silence — nothing captured, no crash
        ["Remind me to stretch"],    # next turn works normally
    ])

    assert [e["kind"] for e in exchanges] == ["captured"]
    assert db.query(CapturedIntent).count() == 1


def test_silence_then_repair_choice_then_selection(db):
    """Silence between offer and selection must not reset pending choices."""
    exchanges = _run(db, [
        ["Call... the... you know..."],   # turn 1: offers repair choices
        [],                               # turn 2: silence (mic noise)
        ["1"],                            # turn 3: selects option 1
    ])

    kinds = [e["kind"] for e in exchanges]
    assert kinds == ["choices", "captured"]
    assert db.query(CapturedIntent).one() is not None


def test_refusal_does_not_capture(db):
    exchanges = _run(db, [["Should I take half my pills tomorrow?"]])

    assert [e["kind"] for e in exchanges] == ["refused"]
    assert db.query(CapturedIntent).count() == 0


def test_session_shared_across_turns_same_call_log(db):
    """All turns write intents under one CallLog row."""
    _run(db, [
        ["Remind me to stretch"],
        ["Tell Sarah hi"],
    ])

    calls = db.query(CallLog).all()
    assert len(calls) == 1
    intents = db.query(CapturedIntent).all()
    assert all(i.call_log_id == calls[0].id for i in intents)


def test_keyboard_interrupt_returns_exchanges_so_far(db):
    """KeyboardInterrupt mid-loop returns all exchanges collected up to that point."""
    turn = 0

    def recorder(path, seconds):
        nonlocal turn
        path.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt not-real-audio")
        turn += 1
        if turn == 2:
            raise KeyboardInterrupt

    transcripts = iter([["Remind me to stretch"], ["Tell Sarah hi"]])

    def transcriber(path):
        try:
            return next(transcripts)
        except StopIteration:
            return []

    exchanges = run_talk_loop(
        db,
        seconds=1.0,
        recorder=recorder,
        transcriber=transcriber,
        call_sid="TEST-INTERRUPT",
    )

    # First turn captured before the interrupt
    assert len(exchanges) == 1
    assert exchanges[0]["kind"] == "captured"


def test_recordings_deleted_between_turns(db):
    """Temp recording is gone after every turn, including silent ones."""
    seen_paths = []

    def recorder(path, seconds):
        path.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt not-real-audio")
        seen_paths.append(path)

    def transcriber(path):
        return ["Remind me to stretch"]

    run_talk_loop(
        db,
        seconds=1.0,
        recorder=recorder,
        transcriber=transcriber,
        call_sid="TEST-DELETE",
        max_turns=2,
    )

    assert len(seen_paths) == 2
    for path in seen_paths:
        assert not path.exists()


def test_exchanges_carry_per_turn_latency_fields(db):
    """Every exchange reports asr/route seconds for the live latency line."""
    exchanges = _run(db, [["Remind me to do my stretches"], ["What day is it today?"]])

    assert len(exchanges) == 2
    for exchange in exchanges:
        assert exchange["asr_seconds"] >= 0.0
        assert exchange["route_seconds"] >= 0.0
