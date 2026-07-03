"""Continuous talk loop CLI — ``make talk-loop`` / ``parker talk``.

Wraps ``run_talk_loop`` with an interactive terminal UI: prints a
listening prompt before each window, prints Parker's response after,
and exits cleanly on Ctrl-C with a final summary of what was staged.

This is the entry point a pilot user leaves running while the caregiver
review page stays open in the browser. The desktop shell runs the same
loop as its second sidecar process; the loop publishes its state
(listening/processing/speaking) to the local DB so the tray icon can
mirror it.
"""

from __future__ import annotations

import sys

from app.demo.talk import DEFAULT_SECONDS, run_talk_loop
from app.voice.speak import load_local_speaker


def run_cli_loop(seconds: float = DEFAULT_SECONDS, server_port: int = 8000) -> None:
    """The interactive loop shared by ``make talk-loop`` and ``parker talk``."""

    # Line-buffer the transcript even when stdout is a file: the desktop
    # shell redirects this loop to PARKER_HOME/logs/talk.log, and a frozen
    # (PyInstaller) interpreter ignores PYTHONUNBUFFERED — a family member
    # tailing the log must see turns as they happen, not on exit.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)
        except (AttributeError, OSError):  # pragma: no cover — non-file streams
            pass

    from app.db.database import SessionLocal, create_tables
    from app.parker.hands import configure_hands_from_settings
    from app.parker.loop_state import (
        STATE_IDLE,
        STATE_SPEAKING,
        publish_loop_state,
    )
    from app.voice.record import load_local_recorder, load_vad_recorder

    create_tables()
    configure_hands_from_settings()
    db = SessionLocal()
    speak = load_local_speaker()
    # End-pointed recording: `seconds` becomes the max window; a natural
    # pause ends the turn early. Falls back to the fixed window when the
    # VAD path is unavailable.
    try:
        recorder = load_vad_recorder()
        listening_hint = f"[listening — pause when you're done, up to {seconds:g}s; Ctrl-C to stop]"
    except RuntimeError:
        recorder = load_local_recorder()
        listening_hint = f"[listening for {seconds:g}s — speak now, or Ctrl-C to stop]"

    turn_count = 0
    latencies: list[float] = []

    def on_turn_start(turn: int) -> None:
        nonlocal turn_count
        turn_count = turn
        print(f"\n{listening_hint}")

    def on_exchange(exchange: dict) -> None:
        if exchange["you"]:
            print(f"  you>    {exchange['you']}")
        print(f"  parker> {exchange['parker']}")
        if exchange["kind"] != "confirm_offer":  # Parker-initiated turns have no latency story
            # Speech can start once ASR + routing (brain on answer turns) are
            # done — this is Parker's added delay after the person stops talking.
            to_speech = exchange["asr_seconds"] + exchange["route_seconds"]
            latencies.append(to_speech)
            slow = "  ← over the 4s budget" if to_speech > 4.0 else ""
            print(
                f"  [latency: asr {exchange['asr_seconds']:.2f}s + "
                f"{exchange['kind']} {exchange['route_seconds']:.2f}s "
                f"→ speech starts {to_speech:.2f}s after you stop]{slow}"
            )
        # Speaking blocks until done so the next window never records
        # Parker's own voice.
        publish_loop_state(db, STATE_SPEAKING)
        speak(exchange["parker"])

    def on_silence() -> None:
        print("  (nothing heard — try again or speak a bit louder)")

    print("Parker talk loop — continuous voice conversation.")
    print("Parker answers out loud (set PARKER_TTS_ENABLED=false for text-only).")
    print(f"Open http://localhost:{server_port}/parker/review/ui as the caregiver view.\n")

    try:
        run_talk_loop(
            db,
            seconds=seconds,
            recorder=recorder,
            on_turn_start=on_turn_start,
            on_exchange=on_exchange,
            on_silence=on_silence,
            on_state=lambda state: publish_loop_state(db, state),
        )
    finally:
        publish_loop_state(db, STATE_IDLE)

        print(f"\nStopped after {turn_count} turn(s). Review staged intents at /parker/review/ui")
        if latencies:
            print(
                f"Session latency (utterance end → speech start): "
                f"mean {sum(latencies) / len(latencies):.2f}s, max {max(latencies):.2f}s "
                f"over {len(latencies)} exchange(s)"
            )
        db.close()


def main() -> None:  # pragma: no cover — interactive entry point
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SECONDS
    run_cli_loop(seconds=seconds)


if __name__ == "__main__":  # pragma: no cover
    main()
