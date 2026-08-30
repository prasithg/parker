#!/usr/bin/env python3
"""Live probe for the fast-voice orchestrator — real OpenAI lane, real brain.

Run from the repo root with the backend venv (keys from backend/.env):

    cd backend && ./.venv/bin/python ../scripts/realtime_live_probe.py

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
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time

sys.path.insert(0, ".")  # run from backend/

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

QUESTION = "When does Alcaraz play next at the US Open, and what channel is it on?"
SESSION_SECONDS = 45


class ReceiptCollector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if "receipt" in message:
            self.lines.append(f"{time.strftime('%H:%M:%S')} {message}")


async def main() -> int:
    from app.db.database import Base
    from app.demo.persona import seed_persona_data
    from app.parker import realtime

    if not realtime.realtime_available():
        print("FAIL: realtime lane unavailable (no OPENAI_API_KEY?)")
        return 1
    if not realtime.realtime_workers.search_worker_available():
        print("FAIL: no brain for look_that_up (no ANTHROPIC_API_KEY?)")
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
    run_task = asyncio.create_task(bridge.run())
    started = time.monotonic()

    try:
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
        while time.monotonic() - started < SESSION_SECONDS:
            await asyncio.sleep(1)
            if any(e.get("type") == "sources" for e in browser_events) and (
                time.monotonic() - started
            ) > 25:
                break
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
    print(f"\n{'PASS' if ok else 'FAIL'}: fast-voice orchestrator live probe")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
