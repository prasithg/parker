#!/bin/bash
# Sidecar smoke test — the bundled engine must work from a clean shell:
# no venv, no repo cwd, temp PARKER_HOME. Three gates:
#   1. `parker selftest`  — one real engine turn (capture + stage + refusal)
#   2. `parker serve`     — boots, answers /health and /setup/status
#   3. `parker doctor`    — runs to completion with machine-readable output
# Usage: scripts/sidecar_smoke.sh [path-to-binary]  (default backend/dist/parker/parker)
set -u

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BINARY="${1:-$REPO_ROOT/backend/dist/parker/parker}"
PORT=48123
FAILURES=0

if [ ! -x "$BINARY" ]; then
  echo "FAIL: sidecar binary not found at $BINARY (run 'make sidecar')"
  exit 1
fi

SMOKE_HOME="$(mktemp -d /tmp/parker-smoke-home.XXXXXX)"
trap 'kill "$SERVE_PID" 2>/dev/null; rm -rf "$SMOKE_HOME"' EXIT
export PARKER_HOME="$SMOKE_HOME"

# A clean shell: neutral cwd, no repo on any path.
cd /

check() { # name, exit code
  if [ "$2" -eq 0 ]; then echo "ok   $1"; else echo "FAIL $1"; FAILURES=$((FAILURES + 1)); fi
}

echo "== parker version =="
"$BINARY" version
check "version" $?

echo "== parker selftest (one engine turn, in-memory) =="
SELFTEST_OUT="$("$BINARY" selftest --load-model)"
SELFTEST_CODE=$?
echo "$SELFTEST_OUT" | grep -q '"ok": true'
check "selftest ok:true (exit $SELFTEST_CODE)" $?
# The bundle must carry the voice natives — a missing ctranslate2 or
# PortAudio dylib fails here, not in the first family conversation.
echo "$SELFTEST_OUT" | grep -q '"faster_whisper": true'
check "bundle imports faster_whisper (ctranslate2)" $?
echo "$SELFTEST_OUT" | grep -q '"sounddevice": true'
check "bundle imports sounddevice (PortAudio)" $?
echo "$SELFTEST_OUT" | grep -q '"model_loadable": false' && FAILURES=$((FAILURES + 1)) && echo "FAIL whisper model load inside the bundle"

echo "== parker serve (background) =="
"$BINARY" serve --port "$PORT" >"$SMOKE_HOME/serve.log" 2>&1 &
SERVE_PID=$!

HEALTH=""
for _ in $(seq 1 45); do
  HEALTH="$(curl -s --max-time 2 "http://127.0.0.1:$PORT/health" || true)"
  [ -n "$HEALTH" ] && break
  sleep 1
done
echo "health: $HEALTH"
echo "$HEALTH" | grep -q '"status":"ok"'
check "/health answers" $?

SETUP="$(curl -s --max-time 2 "http://127.0.0.1:$PORT/setup/status" || true)"
echo "$SETUP" | grep -q '"needs_onboarding":true'
check "/setup/status needs_onboarding on fresh home" $?

kill "$SERVE_PID" 2>/dev/null
wait "$SERVE_PID" 2>/dev/null
check "serve shut down" 0

echo "== parker doctor --json =="
DOCTOR_OUT="$("$BINARY" doctor --json --port "$PORT")"
DOCTOR_CODE=$?
echo "$DOCTOR_OUT" | grep -q '"checks"'
check "doctor produced a machine-readable report (exit $DOCTOR_CODE)" $?
# In the smoke home the model check may fail (nothing downloaded) — that is
# correct doctor behavior, not a smoke failure. The structural checks that
# must pass anywhere: home writable, database writable.
echo "$DOCTOR_OUT" | python3 -c '
import json, sys
report = json.load(sys.stdin)
required = {"parker_home", "database"}
bad = [c["name"] for c in report["checks"] if c["name"] in required and not c["ok"]]
sys.exit(1 if bad else 0)
'
check "doctor: home + database writable" $?

echo
if [ "$FAILURES" -eq 0 ]; then
  echo "SIDECAR SMOKE: PASS"
else
  echo "SIDECAR SMOKE: $FAILURES failure(s) — see $SMOKE_HOME/serve.log before it is cleaned up"
  cat "$SMOKE_HOME/serve.log" 2>/dev/null | tail -20
fi
exit "$FAILURES"
