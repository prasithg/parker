#!/usr/bin/env python3
"""Laptop smoke for the Patient Curiosity Loop — real server, real ASR.

Run it from the repo root with the backend venv:

    backend/.venv/bin/python scripts/converse_smoke.py

It boots a throwaway server (isolated PARKER_HOME in a temp dir, port
8123), synthesizes a handful of Dad-shaped utterances with macOS ``say``
(including one with a long mid-utterance pause), pushes each through
``POST /parker/converse/sessions/{id}/turns`` as base64 WAV — the exact
path the browser page uses — and prints per-turn heard/answer/timings
plus the budget aggregation at the end.

Live behavior notes:
- Every subject answers through the general brain lane (Claude + web
  search when a key is configured); PARKER_HOME_PLACE grounds local
  questions. Offline or keyless, turns degrade honestly — the smoke
  reports which happened.
- ``--offline`` skips the network-dependent turns entirely.
- Audio artifacts live in the temp dir and are deleted at exit; receipts
  are copied next to nothing — the aggregate table is the artifact.

Pass ``--server http://127.0.0.1:8000`` to smoke an already-running dev
server instead of booting one (its PARKER_HOME/receipts will then collect
the receipt lines).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import signal
import statistics
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

def build_utterances(sports_question: str) -> list[tuple[str, str, bool]]:
    # Deliberately includes the paths a first user wanders into, not just
    # the designed happy path: a subject switch mid-conversation, a
    # long mid-utterance pause, and a trailing-off question. All subjects
    # flow through the one general lane — no per-subject providers.
    return [
        ("weather-today", "What is the weather today?", False),
        ("weather-paused", "What is the [[slnc 1800]] weather today?", False),
        ("weather-tomorrow", "What about tomorrow?", False),
        ("sports-score", sports_question, False),
        ("subject-switch", "And what's in the news today?", False),
        ("vague-question", "What is the... the... you know...", True),
        ("reminder", "Remind me to water the plants this evening", True),
        ("confirm-yes", "yes", True),
    ]


def synthesize(text: str, wav_path: Path, *, rate: int = 165) -> None:
    aiff = wav_path.with_suffix(".aiff")
    subprocess.run(
        ["say", "-r", str(rate), "-o", str(aiff), text],
        check=True, capture_output=True, timeout=60,
    )
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(wav_path)],
        check=True, capture_output=True, timeout=60,
    )
    aiff.unlink(missing_ok=True)


def post_json(url: str, payload: dict, timeout: float = 120.0) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"content-type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for_server(base: str, deadline_seconds: float = 60.0) -> None:
    start = time.monotonic()
    while time.monotonic() - start < deadline_seconds:
        try:
            with urllib.request.urlopen(f"{base}/health", timeout=2) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    raise SystemExit(f"server at {base} never became healthy")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default=None, help="use a running server instead of booting one")
    parser.add_argument("--place", default="Melbourne", help="PARKER_HOME_PLACE for the boot server")
    parser.add_argument("--repeat", type=int, default=3, help="repetitions of the weather turn for distribution")
    parser.add_argument("--offline", action="store_true", help="skip network-dependent turns")
    parser.add_argument(
        "--team-question",
        default="Did Collingwood win on the weekend?",
        help="the spoken sports question (any team — it goes through web search)",
    )
    args = parser.parse_args()
    utterances = build_utterances(args.team_question)

    tmp = tempfile.TemporaryDirectory(prefix="parker-converse-smoke-")
    tmp_path = Path(tmp.name)
    server_proc: subprocess.Popen | None = None
    base = args.server

    try:
        if base is None:
            home = tmp_path / "home"
            home.mkdir()
            env = dict(os.environ)
            env.update(
                PARKER_HOME=str(home),
                PARKER_HOME_PLACE=args.place,
            )
            server_proc = subprocess.Popen(
                [str(REPO / "backend/.venv/bin/uvicorn"), "app.main:app", "--port", "8123"],
                cwd=str(REPO / "backend"),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
            base = "http://127.0.0.1:8123"
            wait_for_server(base)
            if server_proc.poll() is not None:
                # Our uvicorn died (usually the port is taken) and /health is
                # answering from some OLDER process — smoking that would
                # silently test stale code. Refuse.
                raise SystemExit(
                    "port 8123 is already serving something else; stop it or "
                    "pass --server to smoke that instance deliberately"
                )
            print(f"server up at {base} (PARKER_HOME={home})")

        print("synthesizing utterances with macOS say…")
        audio: dict[str, Path] = {}
        for key, text, _ in utterances:
            wav = tmp_path / f"{key}.wav"
            synthesize(text, wav)
            audio[key] = wav

        create_started = time.monotonic()
        created = post_json(f"{base}/parker/converse/sessions", {})
        create_seconds = time.monotonic() - create_started
        session_id = created["session_id"]
        print(f"session {session_id[:8]} created in {create_seconds:.1f}s "
              f"(asr_ready={created['asr_ready']})")
        if not created["asr_ready"]:
            print(f"ASR unavailable: {created.get('asr_hint')}")
            return 1

        plan: list[tuple[str, Path]] = []
        for key, _, always in utterances:
            if args.offline and not always:
                continue
            plan.append((key, audio[key]))
        for _ in range(max(0, args.repeat - 1)):
            if not args.offline:
                plan.append(("weather-today", audio["weather-today"]))

        turn_id = 0
        asr_times: list[float] = []
        totals: list[float] = []
        problems: list[str] = []
        for key, wav in plan:
            turn_id += 1
            encoded = base64.b64encode(wav.read_bytes()).decode("ascii")
            result = post_json(
                f"{base}/parker/converse/sessions/{session_id}/turns",
                {"turn_id": turn_id, "audio_base64": encoded, "audio_mime": "audio/wav"},
            )
            timings = result.get("timings_ms", {})
            asr_times.append(timings.get("asr", 0))
            totals.append(timings.get("total_after_done", 0))
            sources = ", ".join(s["label"] for s in result.get("sources", []))
            print(f"\n[{key}] heard: {result.get('heard') or '(silence)'}")
            print(f"  parker[{result.get('kind')}]: {result.get('speech', '')[:140]}")
            if sources:
                print(f"  sources: {sources}")
            print(f"  timings: asr={timings.get('asr')}ms route={timings.get('route')}ms "
                  f"provider={timings.get('provider')}ms total={timings.get('total_after_done')}ms")
            if result.get("state") not in {"answer", "silence"} and result.get("kind") not in {
                "confirm_offer", "executed", "answer", "choices", "clarify", "retry",
            }:
                problems.append(f"{key}: unexpected kind {result.get('kind')}")
            if key.startswith("weather") and "couldn't reach" in result.get("speech", ""):
                print("  note: weather provider unreachable from this machine (offline?)")

        print("\n--- aggregates (this smoke run) ---")
        if asr_times:
            print(f"ASR after Done: median {statistics.median(asr_times):.0f} ms, "
                  f"max {max(asr_times):.0f} ms over {len(asr_times)} turns "
                  f"(budget: median<1000, p95<1500)")
            print(f"server total after Done: median {statistics.median(totals):.0f} ms, "
                  f"max {max(totals):.0f} ms (live-answer budget: median<5000)")
        if problems:
            print("PROBLEMS: " + "; ".join(problems))
            return 1
        print("smoke: PASS")
        if args.server is None:
            print("(receipts for this run lived in the throwaway PARKER_HOME; "
                  "run against a real server to accumulate them)")
        return 0
    finally:
        if server_proc is not None:
            server_proc.send_signal(signal.SIGINT)
            try:
                server_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server_proc.kill()
        tmp.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
