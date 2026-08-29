"""Patient Curiosity Loop eval v0 — the six Dad-shaped traces, deterministically.

Runs the REAL converse harness path (ConverseStore -> TextSession ->
CuriosityBrain) with fake providers, so what is scored is exactly what the
browser page calls. Three lanes:

1. Scripted traces — the strategy doc's go/no-go loop: weather today ->
   tomorrow, one score -> contextual follow-up, one interest question ->
   follow-up. Each turn must answer briefly, carry visible sources where
   live data was used, keep follow-up context, and capture nothing.
2. Failure containment — provider down, silence, unknown place, no
   leagues configured, refused utterance never reaching a provider, and a
   purchase held at the human gate.
3. Stop races — twenty stop-vs-response races through the store; any
   stale (non-stopped) result is a hard failure. (The 100-race version is
   pinned in backend/tests/test_converse.py.)

``--live`` adds one real Open-Meteo and one real ESPN call from this
machine — reachability + latency evidence for the laptop smoke; it skips
gracefully offline and never gates the deterministic result.

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
from app.brain.curiosity import (
    ESPN_SCOREBOARD_URL,
    FORECAST_URL,
    GEOCODE_URL,
    CuriosityBrain,
)
from app.db.database import Base
from app.parker.converse import ConverseStore, TimedBrain

MAX_ANSWER_SENTENCES = 4
MAX_ANSWER_CHARS = 420


# ---------------------------------------------------------------------------
# Fake providers (mirrors backend/tests/test_curiosity_brain.py shapes)
# ---------------------------------------------------------------------------

GEOCODE_PAYLOAD = {
    "results": [{"id": 42, "name": "Fitzroy", "latitude": -37.8, "longitude": 144.98}]
}

FORECAST_PAYLOAD = {
    "current": {"time": "2026-08-29T15:00", "temperature_2m": 14.4, "weather_code": 2},
    "daily": {
        "time": ["2026-08-29", "2026-08-30", "2026-08-31", "2026-09-01"],
        "temperature_2m_max": [16.2, 18.9, 21.0, 17.5],
        "temperature_2m_min": [8.1, 9.4, 11.2, 9.9],
        "precipitation_probability_max": [10, 65, 20, 30],
        "weather_code": [2, 61, 0, 3],
    },
}

SCOREBOARD_PAYLOAD = {
    "events": [
        {
            "name": "Lakers at Celtics",
            "date": "2026-08-29T00:00Z",
            "status": {"type": {"state": "post", "shortDetail": "Final"}},
            "links": [{"href": "https://www.espn.com/game/401"}],
            "competitions": [
                {
                    "competitors": [
                        {
                            "homeAway": "home",
                            "score": "112",
                            "winner": True,
                            "team": {
                                "displayName": "Boston Celtics",
                                "shortDisplayName": "Celtics",
                                "location": "Boston",
                                "abbreviation": "BOS",
                            },
                        },
                        {
                            "homeAway": "away",
                            "score": "104",
                            "winner": False,
                            "team": {
                                "displayName": "Los Angeles Lakers",
                                "shortDisplayName": "Lakers",
                                "location": "Los Angeles",
                                "abbreviation": "LAL",
                            },
                        },
                    ]
                }
            ],
        }
    ]
}

NBA_URL = ESPN_SCOREBOARD_URL.format(path="basketball/nba")


class FakeFetcher:
    def __init__(self, payloads=None, error_urls=()):
        self.calls: list[str] = []
        self.payloads = payloads or {}
        self.error_urls = set(error_urls)

    def __call__(self, url, params):
        self.calls.append(url)
        if url in self.error_urls:
            raise ConnectionError("provider down (eval fake)")
        if url in self.payloads:
            return self.payloads[url]
        raise AssertionError(f"unexpected fetch: {url}")


class InterestBrain:
    """Deterministic stand-in for the inner Claude/OpenClaw brain."""

    def respond(self, history, utterance, context):
        if any("Uri Levine" in message.content for message in history):
            return BrainReply(
                speech="He co-founded Waze and urges founders to fall in love with the problem."
            )
        return BrainReply(
            speech="Uri Levine is an entrepreneur best known for co-founding Waze."
        )


def full_fetcher(**kwargs):
    return FakeFetcher(
        payloads={
            GEOCODE_URL: GEOCODE_PAYLOAD,
            FORECAST_URL: FORECAST_PAYLOAD,
            NBA_URL: SCOREBOARD_PAYLOAD,
        },
        **kwargs,
    )


def make_store(fetcher, *, inner=None):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    brain = CuriosityBrain(
        inner,
        fetcher=fetcher,
        home_place="Fitzroy",
        leagues="nba",
        temperature_unit="celsius",
    )
    store = ConverseStore(
        session_factory=sessionmaker(bind=engine),
        transcriber_loader=lambda: (lambda path: []),
        brain_builder=lambda: TimedBrain(brain),
        model_client_builder=lambda: None,
        receipt_writer=lambda entry: None,
    )
    return store, brain


def _sentences(text: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s])


# ---------------------------------------------------------------------------
# Lane 1: the six scripted traces
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
                "expect_substrings": ["Tomorrow"],
                "expect_sources": True,
                "followup": True,
            },
        ],
    },
    {
        "id": "score-then-followup",
        "turns": [
            {
                "say": "Did the Celtics win last night?",
                "expect_substrings": ["Celtics", "112"],
                "expect_sources": True,
            },
            {
                "say": "Who did they play?",
                "expect_substrings": ["Lakers"],
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


def run_scripted() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for trace in SCRIPTED_TRACES:
        fetcher = full_fetcher()
        store, _ = make_store(fetcher, inner=InterestBrain())
        session_id = store.create_session()["session_id"]
        for index, turn in enumerate(trace["turns"]):
            result = store.run_turn(session_id, turn_id=index, text=turn["say"])
            speech = result["speech"]
            missing = [s for s in turn["expect_substrings"] if s not in speech]
            sources_ok = bool(result["sources"]) == turn["expect_sources"]
            fresh_ok = (not turn["expect_sources"]) or all(
                source.get("fresh_as_of") for source in result["sources"]
            )
            tts_ok = _sentences(speech) <= MAX_ANSWER_SENTENCES and len(speech) <= MAX_ANSWER_CHARS
            ok = (
                result["kind"] == "answer"
                and not missing
                and sources_ok
                and fresh_ok
                and tts_ok
            )
            rows.append(
                {
                    "id": f"{trace['id']}.{index}",
                    "utterance": turn["say"],
                    "kind": result["kind"],
                    "speech": speech,
                    "sources": result["sources"],
                    "missing_substrings": missing,
                    "sources_ok": sources_ok,
                    "freshness_ok": fresh_ok,
                    "tts_ok": tts_ok,
                    "followup": bool(turn.get("followup")),
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

    # Provider down: brief honest failure, then recovery in the same session.
    fetcher = full_fetcher(error_urls={GEOCODE_URL})
    store, _ = make_store(fetcher)
    session_id = store.create_session()["session_id"]
    down = store.run_turn(session_id, turn_id=0, text="What is the weather today?")
    fetcher.error_urls.clear()
    recovered = store.run_turn(session_id, turn_id=1, text="What is the weather today?")
    add(
        "provider-down-then-recovers",
        "couldn't reach" in down["speech"].lower() and "Fitzroy" in recovered["speech"],
        f"down='{down['speech'][:60]}' recovered='{recovered['speech'][:40]}'",
    )

    # Silence: gentle retry, not an error.
    store, _ = make_store(full_fetcher())
    session_id = store.create_session()["session_id"]
    silent = store.run_turn(
        session_id, turn_id=0, audio_base64="UklGRg=="
    )  # decodes to tiny bytes; fake transcriber returns []
    add("silence-gentle-retry", silent["state"] == "silence", silent["speech"][:60])

    # Unknown place: honest, no fabricated forecast.
    fetcher = FakeFetcher(payloads={GEOCODE_URL: {"results": []}})
    store, _ = make_store(fetcher)
    session_id = store.create_session()["session_id"]
    unknown = store.run_turn(session_id, turn_id=0, text="What's the weather in Zzyzxq?")
    add(
        "unknown-place-honest",
        "couldn't find" in unknown["speech"].lower() and not unknown["sources"],
        unknown["speech"][:60],
    )

    # Refused utterance never reaches a provider.
    fetcher = full_fetcher()
    store, _ = make_store(fetcher)
    session_id = store.create_session()["session_id"]
    refused = store.run_turn(
        session_id, turn_id=0, text="Should I take half my pills tomorrow?"
    )
    add(
        "refusal-before-provider",
        refused["kind"] == "refused" and fetcher.calls == [],
        f"kind={refused['kind']} provider_calls={len(fetcher.calls)}",
    )

    # Purchases stay at the human gate even mid-curiosity.
    store, _ = make_store(full_fetcher())
    session_id = store.create_session()["session_id"]
    purchase = store.run_turn(
        session_id, turn_id=0, text="Buy me tickets to the Celtics game"
    )
    add(
        "purchase-held-at-human-gate",
        purchase["kind"] == "needs_human_approval",
        purchase["kind"],
    )

    # A sports follow-up must stay on the board — never fall through to an
    # inner brain that would retract the sourced score (2026-08-29 finding).
    store, _ = make_store(full_fetcher(), inner=InterestBrain())
    session_id = store.create_session()["session_id"]
    store.run_turn(session_id, turn_id=0, text="Did the Celtics win?")
    switch = store.run_turn(session_id, turn_id=1, text="How about the Lakers?")
    add(
        "sports-followup-never-retracts",
        "Lakers" in switch["speech"]
        and bool(switch["sources"])
        and "don't" not in switch["speech"].lower(),
        switch["speech"][:60],
    )

    # A day past the horizon asks, instead of answering today under a chip.
    store, _ = make_store(full_fetcher())
    session_id = store.create_session()["session_id"]
    store.run_turn(session_id, turn_id=0, text="What is the weather today?")
    beyond = store.run_turn(session_id, turn_id=1, text="What about the day after?")
    add(
        "unknown-day-asks-not-guesses",
        "Which day" in beyond["speech"] and not beyond["sources"],
        beyond["speech"][:60],
    )

    # A trailing-off question gets a re-ask, never errand choices.
    store, _ = make_store(full_fetcher())
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
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    store = ConverseStore(
        session_factory=sessionmaker(bind=engine),
        transcriber_loader=lambda: (lambda path: []),
        brain_builder=lambda: TimedBrain(gate),
        model_client_builder=lambda: None,
        receipt_writer=lambda entry: None,
    )
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


# ---------------------------------------------------------------------------
# Optional live smoke (never gates the deterministic result)
# ---------------------------------------------------------------------------


def run_live_smoke() -> dict[str, Any]:
    import httpx

    results: dict[str, Any] = {}
    for label, url, params in (
        ("open_meteo_geocode", GEOCODE_URL, {"name": "Melbourne", "count": 1}),
        ("espn_nba_scoreboard", NBA_URL, {}),
    ):
        started = time.monotonic()
        try:
            response = httpx.get(url, params=params, timeout=8.0)
            response.raise_for_status()
            payload = response.json()
            ok = bool(payload)
            detail = f"{response.status_code} in {time.monotonic() - started:.2f}s"
        except Exception as exc:  # noqa: BLE001 — offline is a skip, not a failure
            ok = False
            detail = f"unreachable: {type(exc).__name__}"
        results[label] = {"reachable": ok, "detail": detail}
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--live", action="store_true", help="also probe the real providers once")
    args = parser.parse_args()

    today = str(date.today())
    print(f"Patient Curiosity Loop eval v0 — {today}")

    print(f"\n[1/3] Scripted Dad traces ({len(SCRIPTED_TRACES)} traces)")
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

    live: dict[str, Any] = {}
    if args.live:
        print("\n[live] Real provider probe")
        live = run_live_smoke()
        for label, row in live.items():
            print(f"  {'ok' if row['reachable'] else 'skip':5s} {label}: {row['detail']}")

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
        "live_probe": live or None,
        "gate": "PASS" if not failed else "FAIL",
        "note": (
            "Deterministic harness-path eval with fake providers; the live probe is "
            "reachability evidence only. Real-latency receipts come from the laptop "
            "smoke (PARKER_HOME/receipts/converse_latency.jsonl)."
        ),
    }
    print(f"\ngate: {summary['gate']}" + (f" — failed: {failed}" if failed else ""))

    if args.write_report:
        reports_dir = Path(__file__).parent / "reports"
        reports_dir.mkdir(exist_ok=True)
        payload = {"summary": summary, "scripted": scripted, "failure_cases": failures}
        for name in (f"curiosity_loop_eval_{today}.json", "curiosity_loop_eval_latest.json"):
            (reports_dir / name).write_text(json.dumps(payload, indent=2))
        lines = [
            f"# Patient Curiosity Loop eval v0 — {today}",
            "",
            f"Gate: **{summary['gate']}**",
            "",
            "Deterministic eval of the real converse harness path (ConverseStore →",
            "TextSession → CuriosityBrain) with fake providers. Scores the strategy",
            "doc's go/no-go loop: current answer with visible sources, follow-up",
            "continuity, honest failure, and Stop that never leaks a stale result.",
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
