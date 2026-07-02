"""Continuous talk loop CLI — ``make talk-loop``.

Wraps ``run_talk_loop`` with an interactive terminal UI: prints a
listening prompt before each window, prints Parker's response after,
and exits cleanly on Ctrl-C with a final summary of what was staged.

This is the entry point a pilot user leaves running in a terminal while
the caregiver review page stays open in the browser.
"""

from __future__ import annotations

import sys

from app.demo.talk import DEFAULT_SECONDS, run_talk_loop
from app.voice.speak import load_local_speaker


def main() -> None:  # pragma: no cover — interactive entry point
    from app.db.database import SessionLocal, create_tables
    from app.parker.hands import configure_hands_from_settings
    from app.voice.record import load_local_recorder, load_vad_recorder

    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SECONDS
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
        print(f"  you>    {exchange['you']}")
        print(f"  parker> {exchange['parker']}")
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
        speak(exchange["parker"])

    def on_silence() -> None:
        print("  (nothing heard — try again or speak a bit louder)")

    print("Parker talk loop — continuous voice conversation.")
    print("Parker answers out loud (set PARKER_TTS_ENABLED=false for text-only).")
    print("Open http://localhost:8000/parker/review/ui as the caregiver view.\n")

    run_talk_loop(
        db,
        seconds=seconds,
        recorder=recorder,
        on_turn_start=on_turn_start,
        on_exchange=on_exchange,
        on_silence=on_silence,
    )

    print(f"\nStopped after {turn_count} turn(s). Review staged intents at /parker/review/ui")
    if latencies:
        print(
            f"Session latency (utterance end → speech start): "
            f"mean {sum(latencies) / len(latencies):.2f}s, max {max(latencies):.2f}s "
            f"over {len(latencies)} exchange(s)"
        )
    db.close()


if __name__ == "__main__":  # pragma: no cover
    main()
