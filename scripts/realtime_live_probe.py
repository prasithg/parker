#!/usr/bin/env python3
"""Live probe for the fast-voice orchestrator — real OpenAI lane, real brain.

Run from the repo root with the backend venv (keys from backend/.env):

    cd backend && ./.venv/bin/python ../scripts/realtime_live_probe.py

To check conversation and a current score in the same session, use
``--wake-tail "What can you do besides reminders?"`` and
``--question "What was the final score of Alcaraz's most recent completed match?"``.

What it does, for real money (one realtime session + one searched brain call):

1. Seeds Ravi (docs/personas/ravi.md) into an isolated in-memory DB — the
   probe never touches backend/parker.db.
2. Opens the actual bridge (``RealtimeBridge`` + real ``connect_openai``),
   which sends the greeting nudge and fires the context worker; the card
   built from Ravi's memories/meds is injected behind the greeting.
3. Types one Ravi question upstream as a user text item ("when does
   Alcaraz play …") and lets the model decide to call ``look_that_up``.
4. Prints the conversation as it streams, the receipts (ack latency,
   worker time), the sources event, and a PASS/FAIL summary of the
   orchestrator contract: instant ack, conversation not blocked, result
   injected, sources on screen.

It ends the session cleanly and exits non-zero if the contract failed.
The first output line is ``probe revision: <git sha>`` so a saved log binds
to the tree it ran on.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

QUESTION = "When does Alcaraz play next at the US Open, and what channel is it on?"
SESSION_SECONDS = 45

# --wake-tail "<words>": the page's same-breath handoff (P0.1 F2) — the
# hello carries what he said after "Hey Parker"; with --pending the final
# words arrive on a later `tail` frame. The bridge must send the wake
# instruction, his words as a USER item, one nudge — and the live API must
# accept that payload (the one-probe-per-payload-change rule).
WAKE_TAIL = ""
PENDING = False


class ReceiptCollector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if "receipt" in message:
            self.lines.append(f"{time.strftime('%H:%M:%S')} {message}")


async def main() -> int:
    import app.main  # noqa: F401 — registers every model on Base.metadata
    from app.db.database import Base
    from app.demo.persona import seed_persona_data
    from app.parker import realtime

    if not realtime.realtime_available():
        print("FAIL: realtime lane unavailable (no OPENAI_API_KEY?)")
        return 1
    if not realtime.realtime_workers.search_worker_available():
        print("FAIL: lookups need ANTHROPIC_API_KEY + PARKER_BRAIN_WEB_SEARCH, or a research gateway")
        return 1

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    realtime._db_session_factory = factory
    db = factory()
    seeded = seed_persona_data(db)
    db.close()
    print(f"Ravi seeded into the probe DB: {seeded}")

    receipts = ReceiptCollector()
    logging.getLogger("parker.realtime").addHandler(receipts)
    logging.getLogger("parker.realtime").setLevel(logging.INFO)

    browser_events: list[dict] = []
    audio_chunks = 0
    transcript: list[str] = []
    to_bridge: asyncio.Queue = asyncio.Queue()

    async def browser_send(event: dict) -> None:
        nonlocal audio_chunks
        kind = event.get("type")
        if kind == "audio":
            audio_chunks += 1
            return
        browser_events.append(event)
        if kind == "assistant_transcript_delta":
            transcript.append(event.get("text", ""))
            print(event.get("text", ""), end="", flush=True)
        else:
            print(f"\n[browser<-] {json.dumps(event)[:300]}")

    async def browser_receive() -> dict:
        return await to_bridge.get()

    bridge = realtime.RealtimeBridge(browser_send, browser_receive)
    upstream_sent: list[dict] = []
    if WAKE_TAIL:
        # The page's first frame: the hello with the wake tail. Record what
        # the bridge sends upstream so the payload shape can be judged.
        hello_tail = WAKE_TAIL if not PENDING else " ".join(WAKE_TAIL.split()[:2])
        await to_bridge.put({"type": "hello", "tail": hello_tail, "pending": PENDING})
        real_connect = realtime.connect_openai

        async def recording_connect():
            upstream = await real_connect()
            real_send = upstream.send

            async def send(raw):
                try:
                    upstream_sent.append(json.loads(raw))
                except (TypeError, ValueError):
                    pass
                await real_send(raw)

            upstream.send = send
            return upstream

        realtime.connect_openai = recording_connect
    run_task = asyncio.create_task(bridge.run())
    started = time.monotonic()

    try:
        if WAKE_TAIL and PENDING:
            await asyncio.sleep(0.6)  # the lane's last inference
            await to_bridge.put({"type": "tail", "text": WAKE_TAIL})
        await asyncio.sleep(6)  # let the greeting stream and the card land
        print(f"\n[probe->] typing the question upstream: {QUESTION!r}")
        await bridge._upstream.send(
            json.dumps(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": QUESTION}],
                    },
                }
            )
        )
        await bridge._upstream.send(json.dumps({"type": "response.create"}))
        sources_seen_at = None
        while time.monotonic() - started < SESSION_SECONDS:
            await asyncio.sleep(1)
            if sources_seen_at is None and any(
                e.get("type") == "sources" for e in browser_events
            ):
                sources_seen_at = time.monotonic()
                print("\n[probe] sources landed — waiting for the steer-back narration…")
            if sources_seen_at is not None and time.monotonic() - sources_seen_at > 14:
                break  # long enough to hear the model weave the result in
    finally:
        await to_bridge.put({"type": "end"})
        try:
            await asyncio.wait_for(run_task, timeout=10)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            run_task.cancel()

    print("\n\n=== receipts ===")
    for line in receipts.lines:
        print(line)

    full_transcript = "".join(transcript)
    got_ack = any("kind=ack" in line and "status=working" in line for line in receipts.lines)
    got_result = any("kind=search" in line and "error=none" in line for line in receipts.lines)
    got_sources = any(e.get("type") == "sources" for e in browser_events)
    spoke = len(full_transcript) > 40 and audio_chunks > 10

    print("\n=== contract ===")
    print(f"model spoke (audio streamed, transcript {len(full_transcript)} chars): {spoke}")
    print(f"look_that_up acked as working:  {got_ack}")
    print(f"search result injected cleanly: {got_result}")
    print(f"sources reached the screen:     {got_sources}")
    ok = spoke and got_ack and got_result and got_sources
    if WAKE_TAIL:
        items = [
            (e["item"].get("role"), e["item"]["content"][0]["text"])
            for e in upstream_sent
            if e.get("type") == "conversation.item.create"
            and e.get("item", {}).get("type") == "message"
        ]
        user_items = [text for role, text in items if role == "user"]
        system_texts = [text for role, text in items if role == "system"]
        tail_as_user = user_items[:1] == [WAKE_TAIL]
        tail_never_system = not any(WAKE_TAIL in text for text in system_texts)
        wake_instruction_first = bool(system_texts) and "his own message" in system_texts[0]
        hiccup = any(
            e.get("type") == "notice" and "hiccuped" in str(e.get("text", ""))
            for e in browser_events
        )
        print(f"wake tail sent as a USER item:  {tail_as_user}  {user_items[:1]}")
        print(f"tail never in a system item:    {tail_never_system}")
        print(f"wake instruction (no greeting): {wake_instruction_first}")
        print(f"live API accepted the payload:  {not hiccup}")
        ok = ok and tail_as_user and tail_never_system and wake_instruction_first and not hiccup
    print(f"\n{'PASS' if ok else 'FAIL'}: fast-voice orchestrator live probe")
    return 0 if ok else 1


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--wake-tail", default="", help="hello tail: his words after 'Hey Parker'")
    parser.add_argument("--question", default=QUESTION, help="one current-information question to verify")
    parser.add_argument(
        "--pending", action="store_true",
        help="mark the hello pending and deliver the full tail on a later frame",
    )
    args = parser.parse_args()
    QUESTION = args.question
    WAKE_TAIL = " ".join(args.wake_tail.split())
    PENDING = bool(args.pending and WAKE_TAIL)
    from app.version import git_sha  # noqa: E402 — after the sys.path insert

    print(f"probe revision: {git_sha()}", flush=True)
    sys.exit(asyncio.run(main()))
