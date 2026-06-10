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


def main() -> None:  # pragma: no cover — interactive entry point
    from app.db.database import SessionLocal, create_tables

    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SECONDS
    create_tables()
    db = SessionLocal()

    turn_count = 0

    def on_turn_start(turn: int) -> None:
        nonlocal turn_count
        turn_count = turn
        print(f"\n[listening for {seconds:g}s — speak now, or Ctrl-C to stop]")

    def on_exchange(exchange: dict) -> None:
        print(f"  you>    {exchange['you']}")
        print(f"  parker> {exchange['parker']}")

    def on_silence() -> None:
        print("  (nothing heard — try again or speak a bit louder)")

    print("Parker talk loop — continuous voice conversation.")
    print(f"Each window is {seconds:g}s. Captured intents stage after each turn.")
    print("Open http://localhost:8000/parker/review/ui as the caregiver view.\n")

    run_talk_loop(
        db,
        seconds=seconds,
        on_turn_start=on_turn_start,
        on_exchange=on_exchange,
        on_silence=on_silence,
    )

    print(f"\nStopped after {turn_count} turn(s). Review staged intents at /parker/review/ui")
    db.close()


if __name__ == "__main__":  # pragma: no cover
    main()
