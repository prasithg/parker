#!/usr/bin/env python3
"""Mock family-agent gateway (the Hermes/OpenClaw box, pretend edition).

Serves the three bridge endpoints Parker probes, so a real dev session can
experience ambient context and gateway-backed skills before the real
harness plugin exists:

- ``GET  /parker/v1/context`` → ambient lines (what Ravi is doing right now)
- ``GET  /parker/v1/skills``  → media_playlist + open_links enabled
- ``POST /parker/v1/skills/invoke`` → pretends success, logs the payload
- ``POST /v1/chat/completions``      → 501, so FallbackBrain degrades to
  the Claude adapter and conversation quality is unaffected

Run it, then point Parker at it:

    ./backend/.venv/bin/python scripts/mock_family_gateway.py &
    PARKER_OPENCLAW_GATEWAY_URL=http://127.0.0.1:18790 make run

(18790, not the real gateway's 18789 — an SSH tunnel to the family's
actual OpenClaw box may hold that port on this machine.)

Change the ambient story live by editing ``/tmp/parker_mock_context.json``
(a JSON list of strings) — the next session opening picks it up. Without
the file, a built-in Ravi scene is served.
"""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PORT = 18790
CONTEXT_FILE = Path("/tmp/parker_mock_context.json")

DEFAULT_CONTEXT = [
    "He just paused a YouTube video called 'How Levodopa Works in the Brain' at the 4 minute mark.",
    "The living-room TV is on and it is quiet in the house.",
    "Sarah's Sunday visit is tomorrow morning.",
]

SKILLS = [
    {"name": "media", "action_types": ["media_playlist"], "enabled": True},
    {"name": "browse", "action_types": ["open_links"], "enabled": True},
]


def context_lines() -> list[str]:
    try:
        data = json.loads(CONTEXT_FILE.read_text())
        if isinstance(data, list):
            return [str(line) for line in data if str(line).strip()]
    except (OSError, ValueError):
        pass
    return DEFAULT_CONTEXT


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == "/parker/v1/context":
            lines = context_lines()
            print(f"[mock-gw] served context: {lines}")
            self._json(200, {"lines": lines})
        elif self.path == "/parker/v1/skills":
            self._json(200, {"skills": SKILLS})
        else:
            self._json(404, {"error": "unknown path"})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        if self.path == "/parker/v1/skills/invoke":
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = {}
            print(f"[mock-gw] skill invoked: {json.dumps(payload)[:300]}")
            self._json(
                200,
                {
                    "status": "ok",
                    "detail": "Queued 12 old Hindi songs on the living-room TV (mock).",
                },
            )
        elif self.path == "/v1/chat/completions":
            # Refuse chat so FallbackBrain hands conversation to Claude.
            self._json(501, {"error": "mock gateway has no brain; use the fallback"})
        else:
            self._json(404, {"error": "unknown path"})

    def log_message(self, fmt, *args):  # quiet the default access log
        return


def main() -> None:
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[mock-gw] family-agent mock on http://127.0.0.1:{PORT}")
    print(f"[mock-gw] ambient story: {CONTEXT_FILE} (JSON list of strings), else built-in Ravi scene")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[mock-gw] bye")
    finally:
        server.server_close()
        time.sleep(0.1)


if __name__ == "__main__":
    main()
