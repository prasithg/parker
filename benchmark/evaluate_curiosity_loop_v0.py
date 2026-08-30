"""Patient Curiosity Loop eval v0 — the Dad-shaped traces, deterministically.

Runs the REAL converse harness path (ConverseStore -> TextSession ->
ClaudeBrainAdapter) with a scripted fake Anthropic client, so what is
scored is exactly what the browser page calls. There are deliberately no
per-subject provider lanes: every subject — weather, sports, news, people
— flows through the one general brain lane, whose web-search citations
surface as on-screen sources.

Lanes:

1. Scripted traces — the strategy doc's go/no-go loop: weather today ->
   tomorrow, one score -> contextual follow-up, one interest question ->
   follow-up. Each turn must answer briefly, carry visible sources where
   the web was searched, keep follow-up context (the fake client refuses
   to answer follow-ups unless the prior turn is in its history), and
   capture nothing.
2. Failure containment — brain lane down (honest line, session survives),
   silence, refused utterance never reaching the brain, a purchase held
   at the human gate, and a trailing-off question re-asked rather than
   offered errand choices.
3. Stop races — twenty stop-vs-response races through the store; any
   stale (non-stopped) result is a hard failure. (The 100-race version is
   pinned in backend/tests/test_converse.py.)

``--live`` sends the scripted questions through the REAL configured brain
(needs ANTHROPIC_API_KEY; searches the live web) — reachability + latency
evidence for the laptop smoke; it skips gracefully keyless and never
gates the deterministic result.

Unsafe/stale results are a hard 0 gate (non-zero exit), matching
eval-brain-lane conventions.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.brain.adapter import BrainReply
from app.brain.claude import ClaudeBrainAdapter
from app.db.database import Base
from app.parker.converse import ConverseStore, TimedBrain

MAX_ANSWER_SENTENCES = 4
MAX_ANSWER_CHARS = 420


# ---------------------------------------------------------------------------
# A scripted fake Anthropic client: web-search-shaped responses, no network
# ---------------------------------------------------------------------------


class _Citation:
    def __init__(self, url: str, title: str):
        self.url = url
        self.title = title


class _TextBlock:
    type = "text"

    def __init__(self, text: str, citations: list[_Citation] | None = None):
        self.text = text
        self.citations = citations or []


class _Response:
    stop_reason = "end_turn"

    def __init__(self, blocks: list[Any]):
        self.content = blocks


# Each entry: (utterance substring, required history substring or None,
# spoken answer, cited source or None). A follow-up answers ONLY when its
# anchor turn is in the brain history — pinning context continuity at the
# harness level, not by trusting the fake.
SCRIPT: list[tuple[str, str | None, str, tuple[str, str] | None]] = [
    (
        "weather today",
        None,
        "It's 14 and partly cloudy in Fitzroy right now, with a top of 16 expected.",
        ("weatherzone.com.au", "Fitzroy forecast — Weatherzone"),
    ),
    (
        "about tomorrow",
        "weather",
        "Tomorrow in Fitzroy looks rainy with a top of 19.",
        ("weatherzone.com.au", "Fitzroy forecast — Weatherzone"),
    ),
    (
        "Collingwood",
        None,
        "No — Collingwood lost to the Bulldogs on Friday night, 96 to 93.",
        ("espn.com.au", "AFL scores — ESPN"),
    ),
    (
        "close game",
        "Collingwood",
        "Very close — it came down to the final minute, a three-point margin.",
        ("espn.com.au", "AFL scores — ESPN"),
    ),
    (
        "Uri Levine",
        None,
        "Uri Levine is an entrepreneur best known for co-founding Waze.",
        None,
    ),
    (
        "known for",
        "Levine",
        "He champions falling in love with the problem, not the solution.",
        None,
    ),
]


class ScriptedClaudeClient:
    """Deterministic stand-in for anthropic.Anthropic; history-aware."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[dict[str, Any]] = []
        self.messages = self  # client.messages.create -> self.create

    def create(self, **kwargs: Any) -> _Response:
        self.calls.append(kwargs)
        if self.fail:
            raise ConnectionError("brain lane down (eval fake)")
        messages = kwargs.get("messages") or []
        last_user = str(messages[-1].get("content", "")) if messages else ""
        history_text = " ".join(str(m.get("content", "")) for m in messages[:-1])
        for needle, anchor, answer, source in SCRIPT:
            if needle.lower() not in last_user.lower():
                continue
            if anchor is not None and anchor.lower() not in history_text.lower():
                return _Response(
                    [_TextBlock("I'm not sure what you're referring back to.")]
                )
            citations = [_Citation(f"https://{source[0]}/", source[1])] if source else None
            return _Response([_TextBlock(answer, citations)])
        return _Response([_TextBlock("I don't have a good answer for that one.")])


def make_store(*, client: ScriptedClaudeClient | None = None, brain: Any | None = None):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    if brain is None:
        brain = ClaudeBrainAdapter(client or ScriptedClaudeClient(), model="fake", max_tokens=300)
    store = ConverseStore(
        session_factory=sessionmaker(bind=engine),
        transcriber_loader=lambda: (lambda path: []),
        brain_builder=lambda: TimedBrain(brain),
        model_client_builder=lambda: None,
        receipt_writer=lambda entry: None,
    )
    return store


def _sentences(text: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s])


# ---------------------------------------------------------------------------
# Lane 1: the scripted traces
# ---------------------------------------------------------------------------

SCRIPTED_TRACES = [
    {
        "id": "weather-today-tomorrow",
        "turns": [
            {
                "say": "What is the weather today?",
                "expect_substrings": ["Fitzroy"],
                "expect_sources": True,
            },
            {
                "say": "What about tomorrow?",
                "expect_substrings": ["Tomorrow", "19"],
                "expect_sources": True,
                "followup": True,
            },
        ],
    },
    {
        "id": "score-then-followup",
        "turns": [
            {
                "say": "Did Collingwood win on the weekend?",
                "expect_substrings": ["No — Collingwood lost", "96"],
                "expect_sources": True,
            },
            {
                "say": "Was it a close game?",
                "expect_substrings": ["final minute"],
                "expect_sources": True,
                "followup": True,
            },
        ],
    },
    {
        "id": "interest-then-followup",
        "turns": [
            {
                "say": "Tell me about Uri Levine",
                "expect_substrings": ["Waze"],
                "expect_sources": False,
            },
            {
                "say": "What is he known for?",
                "expect_substrings": ["problem"],
                "expect_sources": False,
                "followup": True,
            },
        ],
    },
]


def run_scripted(*, live: bool = False) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for trace in SCRIPTED_TRACES:
        if live:
            from app.brain.build import build_brain_adapter

            inner = build_brain_adapter()
            if inner is None:
                return rows  # keyless — the caller reports the skip
            store = make_store(brain=inner)
        else:
            store = make_store(client=ScriptedClaudeClient())
        session_id = store.create_session()["session_id"]
        for index, turn in enumerate(trace["turns"]):
            started = time.monotonic()
            result = store.run_turn(session_id, turn_id=index, text=turn["say"])
            elapsed_ms = int((time.monotonic() - started) * 1000)
            speech = result["speech"]
            missing = [] if live else [
                s for s in turn["expect_substrings"] if s not in speech
            ]
            sources_ok = live or bool(result["sources"]) == turn["expect_sources"]
            tts_ok = _sentences(speech) <= MAX_ANSWER_SENTENCES and len(speech) <= MAX_ANSWER_CHARS
            ok = result["kind"] == "answer" and not missing and sources_ok and (live or tts_ok)
            rows.append(
                {
                    "id": f"{trace['id']}.{index}",
                    "utterance": turn["say"],
                    "kind": result["kind"],
                    "speech": speech,
                    "sources": result["sources"],
                    "missing_substrings": missing,
                    "sources_ok": sources_ok,
                    "tts_ok": tts_ok,
                    "followup": bool(turn.get("followup")),
                    "elapsed_ms": elapsed_ms,
                    "timings_ms": result["timings_ms"],
                    "ok": ok,
                }
            )
    return rows


# ---------------------------------------------------------------------------
# Lane 2: failure containment
# ---------------------------------------------------------------------------


def run_failure_cases() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add(case_id: str, ok: bool, detail: str) -> None:
        rows.append({"id": case_id, "ok": ok, "detail": detail})

    # Brain lane down: honest line, then recovery in the same session.
    client = ScriptedClaudeClient(fail=True)
    store = make_store(client=client)
    session_id = store.create_session()["session_id"]
    down = store.run_turn(session_id, turn_id=0, text="What is the weather today?")
    client.fail = False
    recovered = store.run_turn(session_id, turn_id=1, text="What is the weather today?")
    add(
        "brain-down-then-recovers",
        "couldn't reach" in down["speech"].lower() and "Fitzroy" in recovered["speech"],
        f"down='{down['speech'][:60]}'",
    )

    # Silence: gentle retry, not an error.
    store = make_store(client=ScriptedClaudeClient())
    session_id = store.create_session()["session_id"]
    silent = store.run_turn(session_id, turn_id=0, audio_base64="UklGRg==")
    add("silence-gentle-retry", silent["state"] == "silence", silent["speech"][:60])

    # A refused utterance never reaches the brain at all.
    client = ScriptedClaudeClient()
    store = make_store(client=client)
    session_id = store.create_session()["session_id"]
    refused = store.run_turn(
        session_id, turn_id=0, text="Should I take half my pills tomorrow?"
    )
    add(
        "refusal-before-brain",
        refused["kind"] == "refused" and client.calls == [],
        f"kind={refused['kind']} brain_calls={len(client.calls)}",
    )

    # Purchases stay at the human gate even mid-curiosity.
    store = make_store(client=ScriptedClaudeClient())
    session_id = store.create_session()["session_id"]
    purchase = store.run_turn(
        session_id, turn_id=0, text="Buy me tickets to the Collingwood game"
    )
    add(
        "purchase-held-at-human-gate",
        purchase["kind"] == "needs_human_approval",
        purchase["kind"],
    )

    # A trailing-off question gets a re-ask, never errand choices.
    store = make_store(client=ScriptedClaudeClient())
    session_id = store.create_session()["session_id"]
    vague = store.run_turn(session_id, turn_id=0, text="What is the... um... in Ball... Ballar...")
    add(
        "vague-question-reasks",
        vague["kind"] == "retry" and not vague["choices"],
        f"kind={vague['kind']}",
    )

    return rows


# ---------------------------------------------------------------------------
# Lane 3: stop races
# ---------------------------------------------------------------------------


class GateBrain:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def respond(self, history, utterance, context):
        self.entered.set()
        assert self.release.wait(timeout=5)
        return BrainReply(speech="late answer that must never surface")


def run_stop_races(rounds: int = 20) -> dict[str, Any]:
    gate = GateBrain()
    store = make_store(brain=gate)
    session_id = store.create_session()["session_id"]
    stale = 0
    for round_number in range(rounds):
        gate.entered.clear()
        gate.release.clear()
        results: list[dict[str, Any]] = []
        thread = threading.Thread(
            target=lambda: results.append(
                store.run_turn(session_id, turn_id=round_number, text="What day is it?")
            )
        )
        thread.start()
        gate.entered.wait(timeout=5)
        store.stop(session_id)
        gate.release.set()
        thread.join(timeout=5)
        if not results or results[0]["state"] != "stopped" or results[0]["speech"]:
            stale += 1
    return {"rounds": rounds, "stale_results": stale, "ok": stale == 0}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument(
        "--live",
        action="store_true",
        help="also run the scripted questions through the real configured brain",
    )
    args = parser.parse_args()

    today = str(date.today())
    print(f"Patient Curiosity Loop eval v0 — {today} (general brain lane, no subject providers)")

    print(f"\n[1/3] Scripted Dad traces ({len(SCRIPTED_TRACES)} traces, fake search client)")
    scripted = run_scripted()
    for row in scripted:
        flag = "ok" if row["ok"] else "FAIL"
        print(f"  {flag:5s} {row['id']:26s} [{row['kind']}] {row['speech'][:70]}")

    print("\n[2/3] Failure containment")
    failures = run_failure_cases()
    for row in failures:
        flag = "ok" if row["ok"] else "FAIL"
        print(f"  {flag:5s} {row['id']:26s} {row['detail'][:70]}")

    print("\n[3/3] Stop races")
    races = run_stop_races()
    print(f"  {'ok' if races['ok'] else 'FAIL':5s} {races['rounds']} races, "
          f"{races['stale_results']} stale results")

    live_rows: list[dict[str, Any]] = []
    if args.live:
        print("\n[live] Real brain lane (needs ANTHROPIC_API_KEY; live web search)")
        live_rows = run_scripted(live=True)
        if not live_rows:
            print("  skip — no brain configured")
        for row in live_rows:
            sources = ",".join(s["label"][:24] for s in row["sources"]) or "-"
            print(f"  {row['id']:26s} {row['elapsed_ms']:5d}ms sources[{sources}] "
                  f"{row['speech'][:60]}")

    failed = (
        [row["id"] for row in scripted if not row["ok"]]
        + [row["id"] for row in failures if not row["ok"]]
        + ([] if races["ok"] else ["stop-races"])
    )
    summary = {
        "date": today,
        "scripted_turns": len(scripted),
        "failure_cases": len(failures),
        "stop_races": races,
        "failed_ids": failed,
        "live_turns": len(live_rows) or None,
        "gate": "PASS" if not failed else "FAIL",
        "note": (
            "Deterministic harness-path eval with a scripted fake search client; "
            "every subject flows through the one general brain lane (no per-subject "
            "providers). The live lane is latency/reachability evidence only."
        ),
    }
    print(f"\ngate: {summary['gate']}" + (f" — failed: {failed}" if failed else ""))

    if args.write_report:
        reports_dir = Path(__file__).parent / "reports"
        reports_dir.mkdir(exist_ok=True)
        payload = {
            "summary": summary,
            "scripted": scripted,
            "failure_cases": failures,
            "live": live_rows,
        }
        for name in (f"curiosity_loop_eval_{today}.json", "curiosity_loop_eval_latest.json"):
            (reports_dir / name).write_text(json.dumps(payload, indent=2))
        lines = [
            f"# Patient Curiosity Loop eval v0 — {today}",
            "",
            f"Gate: **{summary['gate']}**",
            "",
            "Deterministic eval of the real converse harness path (ConverseStore →",
            "TextSession → ClaudeBrainAdapter) with a scripted fake search client.",
            "Every subject flows through the one general brain lane — web-search",
            "citations surface as on-screen sources; there are no per-subject",
            "provider lanes to maintain.",
            "",
            "| Case | Result | Detail |",
            "|---|---|---|",
        ]
        for row in scripted:
            lines.append(
                f"| {row['id']} | {'ok' if row['ok'] else 'FAIL'} | {row['speech'][:80]} |"
            )
        for row in failures:
            lines.append(
                f"| {row['id']} | {'ok' if row['ok'] else 'FAIL'} | {row['detail'][:80]} |"
            )
        lines.append(
            f"| stop-races | {'ok' if races['ok'] else 'FAIL'} | "
            f"{races['rounds']} races, {races['stale_results']} stale |"
        )
        lines += ["", f"Note: {summary['note']}", ""]
        for name in (f"curiosity_loop_eval_{today}.md", "curiosity_loop_eval_latest.md"):
            (reports_dir / name).write_text("\n".join(lines))
        print(f"Report written to {reports_dir / f'curiosity_loop_eval_{today}.json'}")

    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
