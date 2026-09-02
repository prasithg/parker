#!/bin/bash
# Headless packaged-companion probe: launch the built Parker.app with a
# scratch PARKER_HOME whose onboarding is already complete, and judge the
# real WKWebView companion window from the ENGINE'S OWN RECORDS — the
# access log (companion page fetched by the shell's webview, the page's
# boot calls) and the receipts file (webgl_ready / webgl_fallback posted
# by the page's scene boot).
#
# What it binds: the .app under test is tied to a source revision. The
# bundled engine's `parker version --json` and the shell's first stdout
# line (`parker-desktop <ver> git=<sha>`, stamped by build.rs) must both
# equal --expect-sha (default: this checkout's HEAD, `-dirty` when the
# tree has changes; a dirty expectation fails unless --allow-dirty). The
# engine sidecar is identified as the shell's child running `serve --port`
# and must be the bundled binary; after the shell quits, the probe waits
# for THAT pid to exit.
#
# What it does not observe: the OS microphone. Power stays OFF in the
# fresh home, and the page only opens the mic after an acknowledged
# power claim (companion_ui.py acquireAudio after claimPower; Node pin
# "boots OFF: nothing listens"), so the engine seeing no power claim and
# no wake socket is evidence the page never asked — TCC/AVFoundation state
# is not read here (--tcc-log prints the unified log as advisory output
# only). Pixels, and the power/wake click in the packaged window, remain
# the human gate.
#
# Usage: scripts/packaged_companion_probe.sh [--expect-sha SHA] [--allow-dirty]
#                                             [--tcc-log] [path-to-Parker.app]
# Exit 0 = the packaged companion window booted, reported its scene, and
# the bundle is the expected source revision.
set -u

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXPECT_SHA=""
ALLOW_DIRTY=0
TCC_LOG=0
APP=""
while [ $# -gt 0 ]; do
  case "$1" in
    --expect-sha) EXPECT_SHA="${2:-}"; shift 2 ;;
    --expect-sha=*) EXPECT_SHA="${1#*=}"; shift ;;
    --allow-dirty) ALLOW_DIRTY=1; shift ;;
    --tcc-log) TCC_LOG=1; shift ;;
    -h|--help) sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "FAIL: unknown option $1"; exit 2 ;;
    *) APP="$1"; shift ;;
  esac
done
APP="${APP:-$REPO_ROOT/desktop/src-tauri/target/release/bundle/macos/Parker.app}"
case "$APP" in /*) ;; *) APP="$(pwd)/$APP" ;; esac
APP="$(cd "$(dirname "$APP")" && pwd -P)/$(basename "$APP")"
BIN="$APP/Contents/MacOS/parker-desktop"
ENGINE_BIN="$APP/Contents/Resources/engine/parker"

FAILURES=0
ok()   { echo "ok   $1"; }
fail() { echo "FAIL $1"; FAILURES=$((FAILURES + 1)); }
real() { /bin/realpath "$1" 2>/dev/null || python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$1"; }

# --- expectation: which source revision must this .app be? ------------------
if [ -z "$EXPECT_SHA" ]; then
  EXPECT_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null)"
  if [ -z "$EXPECT_SHA" ]; then
    echo "FAIL: cannot read HEAD of $REPO_ROOT — pass --expect-sha <sha>"
    exit 1
  fi
  if [ -n "$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null)" ]; then EXPECT_SHA="$EXPECT_SHA-dirty"; fi
fi
case "$EXPECT_SHA" in
  *-dirty)
    if [ "$ALLOW_DIRTY" -eq 1 ]; then
      echo "note: expecting a dirty build ($EXPECT_SHA) — --allow-dirty given; this run cannot vouch for a reviewed revision"
    else
      echo "FAIL: expected revision is dirty ($EXPECT_SHA): a reviewed SHA needs a clean build (commit, or pass --allow-dirty to probe anyway)"
      exit 1
    fi ;;
esac
echo "== expecting source revision $EXPECT_SHA =="

if [ ! -x "$BIN" ]; then
  echo "FAIL: app binary not found at $BIN (run 'cargo tauri build')"
  exit 1
fi
if [ ! -x "$ENGINE_BIN" ]; then
  fail "bundled engine not found at $ENGINE_BIN (run 'make sidecar' before 'cargo tauri build')"
  echo "PACKAGED COMPANION PROBE: FAIL ($FAILURES)"; exit 1
fi

# --- the bundled engine must be the expected source ---------------------------
# The shell would honour PARKER_ENGINE_BIN over the bundle; the probe judges
# the bundle only.
unset PARKER_ENGINE_BIN
ENGINE_VERSION_JSON="$("$ENGINE_BIN" version --json 2>/dev/null)"
ENGINE_SHA="$(printf '%s' "$ENGINE_VERSION_JSON" | python3 -c 'import json, sys
try:
    print(json.load(sys.stdin).get("git_sha", ""))
except Exception:
    print("")' 2>/dev/null)"
if [ "$ENGINE_SHA" = "$EXPECT_SHA" ]; then
  ok "bundled engine reports git_sha $ENGINE_SHA ($ENGINE_VERSION_JSON)"
else
  fail "bundled engine git_sha is '${ENGINE_SHA:-<none>}', expected $EXPECT_SHA (version output: ${ENGINE_VERSION_JSON:-<none>}) — rebuild with 'make sidecar' at that revision"
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
LAUNCHED_AT="$(date '+%Y-%m-%d %H:%M:%S')"

echo "== launching $APP with PARKER_HOME=$HOME_DIR =="
"$BIN" > "$HOME_DIR/shell.stdout" 2>&1 &
SHELL_PID=$!
ENGINE_PID=""
trap 'kill "$SHELL_PID" 2>/dev/null; [ -n "$ENGINE_PID" ] && kill "$ENGINE_PID" 2>/dev/null; true' EXIT

# The shell prints its build identity first thing in setup().
for i in $(seq 1 30); do
  grep -q "^parker-desktop .* git=" "$HOME_DIR/shell.stdout" 2>/dev/null && break
  sleep 0.5
done
SHELL_LINE="$(grep -m1 "^parker-desktop .* git=" "$HOME_DIR/shell.stdout" 2>/dev/null)"
if [ -z "$SHELL_LINE" ]; then
  fail "shell never printed its 'parker-desktop <ver> git=<sha>' line (stdout: $(head -c 200 "$HOME_DIR/shell.stdout" 2>/dev/null))"
elif [ "${SHELL_LINE##* git=}" = "$EXPECT_SHA" ]; then
  ok "shell build identity: $SHELL_LINE"
else
  fail "shell reports '${SHELL_LINE##* git=}', expected $EXPECT_SHA ($SHELL_LINE) — rebuild with 'cargo tauri build' at that revision"
fi

ENGINE_LOG=""
for i in $(seq 1 90); do
  ENGINE_LOG="$(ls "$HOME_DIR"/logs/engine*.log 2>/dev/null | head -1)"
  if [ -n "$ENGINE_LOG" ] && grep -q "GET /health" "$ENGINE_LOG" 2>/dev/null; then break; fi
  sleep 1
done
if [ -z "$ENGINE_LOG" ]; then fail "engine log never appeared under $HOME_DIR/logs"; exit 1; fi
ok "engine up (health polled by the shell): $ENGINE_LOG"

# Identify the sidecar: the shell's child running `serve --port` (PARKER_HOME
# is only in its environment, never on its command line, so a pgrep on the
# home path can never see it).
ENGINE_PID="$(pgrep -P "$SHELL_PID" -f 'serve --port' | head -1)"
if [ -z "$ENGINE_PID" ]; then
  fail "no child of the shell (pid $SHELL_PID) is running 'serve --port'"
else
  ENGINE_CMD="$(ps -o command= -p "$ENGINE_PID" 2>/dev/null)"
  case "$ENGINE_CMD" in
    *"$ENGINE_BIN"*|*"$(real "$ENGINE_BIN")"*) ok "engine sidecar pid $ENGINE_PID is the bundled binary: $ENGINE_CMD" ;;
    *) fail "engine sidecar pid $ENGINE_PID is not the bundled binary: $ENGINE_CMD" ;;
  esac
fi

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
grep -q "/parker/converse/companion/power" "$ENGINE_LOG" && fail "engine saw a power claim (must stay OFF in a fresh home)" || ok "engine saw no power claim (page power stayed OFF; the page opens the mic only after an acknowledged claim)"
grep -q "/parker/converse/wake" "$ENGINE_LOG" && fail "a wake socket opened" || ok "no wake socket"
if [ "$TCC_LOG" -eq 1 ]; then
  echo "-- advisory: unified log for tccd mentioning app.parker.desktop since launch (may be <private>-redacted; never pass/fail) --"
  /usr/bin/log show --start "$LAUNCHED_AT" --predicate 'process == "tccd" AND eventMessage CONTAINS "app.parker.desktop"' 2>/dev/null | tail -20 | sed 's/^/   /'
fi

echo "== quitting the app =="
kill "$SHELL_PID" 2>/dev/null
for i in $(seq 1 20); do kill -0 "$SHELL_PID" 2>/dev/null || break; sleep 0.5; done
kill -0 "$SHELL_PID" 2>/dev/null && fail "shell still running after SIGTERM" || ok "shell exited"
# The engine's --parent-pid watchdog polls every 2 s, then uvicorn shuts
# down gracefully: give that pid up to 10 s.
if [ -n "$ENGINE_PID" ]; then
  ENGINE_GONE=""
  for tenths in $(seq 0 5 100); do
    if ! kill -0 "$ENGINE_PID" 2>/dev/null; then ENGINE_GONE="$tenths"; break; fi
    sleep 0.5
  done
  if [ -n "$ENGINE_GONE" ]; then
    ok "engine pid $ENGINE_PID exited within $((ENGINE_GONE / 10)).$((ENGINE_GONE % 10)) s of the shell"
  else
    fail "engine pid $ENGINE_PID outlived the shell by 10 s — killing it"
    kill -9 "$ENGINE_PID" 2>/dev/null
  fi
fi

echo
echo "engine log lines: $(wc -l < "$ENGINE_LOG")  (kept at $HOME_DIR)"
grep -E "GET /parker/converse|POST /parker/converse|receipts|webgl" "$ENGINE_LOG" | sed 's/^/   /' | head -20
[ -f "$RECEIPTS" ] && { echo "receipts:"; sed 's/^/   /' "$RECEIPTS" | head -5; }
trap - EXIT
echo
echo "This probe proves: the .app at $APP carries engine and shell built from $EXPECT_SHA; the shell spawned the bundled"
echo "  engine as its child, opened the companion window, the WKWebView fetched the page + Three.js and posted a scene receipt;"
echo "  the engine saw no power claim and no wake socket; the engine exited with the shell."
echo "It does not prove: what the window looked like (pixels), that the power/wake click works in the packaged window,"
echo "  OS microphone/TCC state (not observed here), or anything about a real microphone in the room."
echo
if [ "$FAILURES" -eq 0 ]; then echo "PACKAGED COMPANION PROBE: PASS"; exit 0; else echo "PACKAGED COMPANION PROBE: FAIL ($FAILURES)"; exit 1; fi
