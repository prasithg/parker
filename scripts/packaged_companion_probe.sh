#!/bin/bash
# Headless packaged-companion probe: launch the built Parker.app with a
# scratch PARKER_HOME whose onboarding is already complete, and judge the
# real WKWebView companion window from the ENGINE'S OWN RECORDS —
# the access log (companion page fetched by the shell's webview, the
# page's boot calls) and the receipts file (webgl_ready / webgl_fallback
# posted by the page's scene boot). Power stays OFF in the fresh home, so
# the page never touches the microphone: no TCC prompt, no audio. The
# power/wake click in the packaged window remains the human gate.
#
# Usage: scripts/packaged_companion_probe.sh [path-to-Parker.app]
# Exit 0 = the packaged companion window booted and reported its scene.
set -u

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="${1:-$REPO_ROOT/desktop/src-tauri/target/release/bundle/macos/Parker.app}"
BIN="$APP/Contents/MacOS/parker-desktop"
if [ ! -x "$BIN" ]; then
  echo "FAIL: app binary not found at $BIN (run 'cargo tauri build')"
  exit 1
fi

HOME_DIR="$(mktemp -d /tmp/parker-packaged-probe.XXXXXX)"
# A synthetic, onboarding-complete family config (nothing private): the
# shell opens the companion on boot only once onboarding is done.
cat > "$HOME_DIR/config.json" <<'JSON'
{
  "onboarding_completed": true,
  "parker_address_mode": "wake",
  "parker_wake_name": "parker",
  "patient_name": "Probe"
}
JSON
export PARKER_HOME="$HOME_DIR"
FAILURES=0
ok()   { echo "ok   $1"; }
fail() { echo "FAIL $1"; FAILURES=$((FAILURES + 1)); }

echo "== launching $APP with PARKER_HOME=$HOME_DIR =="
"$BIN" > "$HOME_DIR/shell.stdout" 2>&1 &
SHELL_PID=$!
trap 'kill "$SHELL_PID" 2>/dev/null; sleep 1; pkill -f "$HOME_DIR" 2>/dev/null; true' EXIT

ENGINE_LOG=""
for i in $(seq 1 90); do
  ENGINE_LOG="$(ls "$HOME_DIR"/logs/engine*.log 2>/dev/null | head -1)"
  if [ -n "$ENGINE_LOG" ] && grep -q "GET /health" "$ENGINE_LOG" 2>/dev/null; then break; fi
  sleep 1
done
if [ -z "$ENGINE_LOG" ]; then fail "engine log never appeared under $HOME_DIR/logs"; exit 1; fi
ok "engine up (health polled by the shell): $ENGINE_LOG"

# The shell opens the companion window once /setup/status says onboarding is done.
for i in $(seq 1 60); do
  grep -q "GET /parker/converse " "$ENGINE_LOG" && break
  sleep 1
done
if grep -q "GET /parker/converse " "$ENGINE_LOG"; then ok "companion page fetched by the packaged window"; else fail "the shell never fetched /parker/converse (no companion window)"; fi

for i in $(seq 1 30); do
  grep -q "POST /parker/converse/sessions " "$ENGINE_LOG" && grep -q "GET /parker/converse/companion/settings " "$ENGINE_LOG" && break
  sleep 1
done
grep -q "POST /parker/converse/sessions " "$ENGINE_LOG" && ok "page boot: receipts session opened" || fail "page boot: no receipts session"
grep -q "GET /parker/converse/companion/settings " "$ENGINE_LOG" && ok "page boot: persisted power/CC read" || fail "page boot: settings never read"
grep -q "converse/static/converse/reachy.js" "$ENGINE_LOG" && ok "scene module fetched" || fail "scene module never fetched"
grep -q "vendor/three/three.module.min.js" "$ENGINE_LOG" && ok "vendored three.js fetched" || fail "three.js never fetched"

RECEIPTS="$HOME_DIR/receipts/converse_latency.jsonl"
for i in $(seq 1 30); do
  [ -f "$RECEIPTS" ] && grep -q "webgl_" "$RECEIPTS" && break
  sleep 1
done
if [ -f "$RECEIPTS" ] && grep -q '"outcome": "webgl_ready"' "$RECEIPTS"; then
  ok "WKWebView rendered the Reachy scene (webgl_ready receipt)"
elif [ -f "$RECEIPTS" ] && grep -q '"outcome": "webgl_fallback"' "$RECEIPTS"; then
  fail "WKWebView fell back to the dot (webgl_fallback receipt)"
else
  fail "no scene receipt arrived"
fi
grep -q "/parker/converse/companion/power" "$ENGINE_LOG" && fail "power was touched (must stay OFF in a fresh home)" || ok "power untouched: nothing listened, no microphone prompt"
grep -q "/parker/converse/wake" "$ENGINE_LOG" && fail "a wake socket opened" || ok "no wake socket"

echo "== quitting the app =="
kill "$SHELL_PID" 2>/dev/null
for i in $(seq 1 20); do kill -0 "$SHELL_PID" 2>/dev/null || break; sleep 0.5; done
kill -0 "$SHELL_PID" 2>/dev/null && fail "shell still running after SIGTERM" || ok "shell exited"
sleep 1
if pgrep -f "$HOME_DIR" >/dev/null 2>&1; then fail "engine sidecar outlived the shell"; else ok "engine sidecar gone with the shell"; fi

echo
echo "engine log lines: $(wc -l < "$ENGINE_LOG")  (kept at $HOME_DIR)"
grep -E "GET /parker/converse|POST /parker/converse|receipts|webgl" "$ENGINE_LOG" | sed 's/^/   /' | head -20
[ -f "$RECEIPTS" ] && { echo "receipts:"; sed 's/^/   /' "$RECEIPTS" | head -5; }
trap - EXIT
echo
if [ "$FAILURES" -eq 0 ]; then echo "PACKAGED COMPANION PROBE: PASS"; exit 0; else echo "PACKAGED COMPANION PROBE: FAIL ($FAILURES)"; exit 1; fi
