#!/usr/bin/env python3
"""Multi-turn LIVE conversation probe — scripted Ravi sessions, real lane.

Where ``realtime_live_probe.py`` proves the orchestrator contract once,
this runs a whole scripted conversation (typed user turns) through the
real bridge — real gpt-realtime, real Claude search, optionally the mock
family gateway for ambient context — and writes the full annotated
transcript as JSON for judging.

    cd backend && ./.venv/bin/python ../scripts/live_conversation_probe.py \
        --scenario alcaraz_reminder --mock-gateway

Costs real money (one realtime session + searches per run). Transcripts
land in ~/Operations/parker/live-probes/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")  # run from backend/

MOCK_GATEWAY_URL = "http://127.0.0.1:18790"

SCENARIOS: dict[str, dict] = {
    "alcaraz_reminder": {
        "story": "Tennis question -> streaming follow-up -> reminder ask. The "
        "canonical fusion chain: search behind the talk, then an action staged.",
        "turns": [
            ("When does Alcaraz play next at the US Open?", 24),
            ("We have the ESPN app on the TV, right? Anyway — can you remind me the morning of the match to check the time?", 20),
        ],
        "expect": [
            "look_that_up acked while conversation continued",
            "search note injected with sources on screen",
            "a reminder proposal staged (proposal_staged event), never executed",
        ],
    },
    "ambient_levodopa": {
        "story": "He pauses the levodopa video and opens the line. The mock "
        "gateway whispers the video context; his vague follow-up should make "
        "sense because of it.",
        "mock_gateway": True,
        "turns": [
            ("That video I was just watching — can you explain that in simple words?", 24),
            ("So is that the same as what I take at two o'clock?", 16),
        ],
        "expect": [
            "context card carried the paused-video line before the first turn",
            "vague 'that video' resolved from ambient context",
            "no dosage numbers spoken; medical guard never tripped mid-word",
        ],
    },
    "morning_walk_weather": {
        "story": "The personalized-weather case from Pras's brief: the answer "
        "should be shaped around his walk-before-the-heat habit.",
        "turns": [
            ("What's the weather looking like today?", 26),
        ],
        "expect": [
            "search ran; answer arrived while conversation stayed alive",
            "judge: was the answer shaped around the morning walk window?",
        ],
    },
}


class Recorder(logging.Handler):
    def __init__(self, sink: list) -> None:
        super().__init__(level=logging.INFO)
        self.sink = sink

    def emit(self, record) -> None:
        message = record.getMessage()
        if "receipt" in message:
            self.sink.append({"t": time.time(), "kind": "receipt", "text": message})


async def run_scenario(name: str, use_mock_gateway: bool) -> dict:
    spec = SCENARIOS[name]
    if use_mock_gateway or spec.get("mock_gateway"):
        os.environ["PARKER_OPENCLAW_GATEWAY_URL"] = MOCK_GATEWAY_URL
    # Ravi's canonical (synthetic) home grounds weather/local lookups; a
    # developer's real PARKER_HOME_PLACE, if set, wins.
    os.environ.setdefault("PARKER_HOME_PLACE", "Melbourne, Australia")

    import app.main  # noqa: F401 — registers models; reads env above
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db.database import Base
    from app.demo.persona import seed_persona_data
    from app.parker import realtime
    from app.parker.hands import configure_hands_from_settings

    if not realtime.realtime_available():
        raise SystemExit("realtime lane unavailable (no OPENAI_API_KEY?)")
    if not realtime.realtime_workers.search_worker_available():
        raise SystemExit("no brain for look_that_up (no ANTHROPIC_API_KEY?)")

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    realtime._db_session_factory = factory
    db = factory()
    seed_persona_data(db)
    db.close()
    configure_hands_from_settings()  # media/open_links via the mock gateway

    events: list[dict] = []
    recorder = Recorder(events)
    logging.getLogger("parker.realtime").addHandler(recorder)
    logging.getLogger("parker.realtime").setLevel(logging.INFO)

    to_bridge: asyncio.Queue = asyncio.Queue()
    transcript_open = {"text": ""}

    async def browser_send(event: dict) -> None:
        kind = event.get("type")
        if kind == "audio":
            return
        if kind == "assistant_transcript_delta":
            transcript_open["text"] += event.get("text", "")
            return
        # flush accumulated speech before structural events
        if transcript_open["text"]:
            events.append({"t": time.time(), "kind": "parker", "text": transcript_open["text"]})
            print(f"  PARKER: {transcript_open['text']}")
            transcript_open["text"] = ""
        events.append({"t": time.time(), "kind": kind, "data": event})
        print(f"  [{kind}] {json.dumps(event)[:220]}")

    async def browser_receive() -> dict:
        return await to_bridge.get()

    bridge = realtime.RealtimeBridge(browser_send, browser_receive)
    run_task = asyncio.create_task(bridge.run())
    print(f"\n=== {name}: {spec['story']}\n")
    try:
        await asyncio.sleep(7)  # greeting + context card
        for text, wait in spec["turns"]:
            if transcript_open["text"]:
                events.append({"t": time.time(), "kind": "parker", "text": transcript_open["text"]})
                print(f"  PARKER: {transcript_open['text']}")
                transcript_open["text"] = ""
            events.append({"t": time.time(), "kind": "ravi", "text": text})
            print(f"  RAVI:   {text}")
            await bridge._upstream.send(
                json.dumps(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": text}],
                        },
                    }
                )
            )
            await bridge._upstream.send(json.dumps({"type": "response.create"}))
            await asyncio.sleep(wait)
    finally:
        if transcript_open["text"]:
            events.append({"t": time.time(), "kind": "parker", "text": transcript_open["text"]})
            print(f"  PARKER: {transcript_open['text']}")
        await to_bridge.put({"type": "end"})
        try:
            await asyncio.wait_for(run_task, timeout=10)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            run_task.cancel()

    # what the session left behind
    db = factory()
    from app.db.models import StagedAction

    staged = [
        {"type": a.action_type, "status": a.status} for a in db.query(StagedAction).all()
    ]
    db.close()

    return {
        "scenario": name,
        "story": spec["story"],
        "expect": spec["expect"],
        "events": events,
        "staged_actions": staged,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), required=True)
    parser.add_argument("--mock-gateway", action="store_true")
    args = parser.parse_args()

    mock_proc = None
    if args.mock_gateway or SCENARIOS[args.scenario].get("mock_gateway"):
        mock_proc = subprocess.Popen(
            [sys.executable, "../scripts/mock_family_gateway.py"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.8)
    try:
        result = asyncio.run(run_scenario(args.scenario, args.mock_gateway))
    finally:
        if mock_proc is not None:
            mock_proc.terminate()

    out_dir = Path.home() / "Operations" / "parker" / "live-probes"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{args.scenario}.json"
    out_path.write_text(json.dumps(result, indent=1))
    print(f"\ntranscript -> {out_path}")
    print(f"staged actions: {result['staged_actions']}")


if __name__ == "__main__":
    main()
