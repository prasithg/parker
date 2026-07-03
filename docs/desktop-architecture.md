# Parker.app — desktop architecture (ADR)

Status: accepted, 2026-07-02 (Session F+G). Scope: how Parker becomes a
downloadable macOS app a family member installs by dragging a `.dmg` to
Applications — no Python, no terminal, no git.

## Decision

**Tauri v2 shell + the existing Python engine bundled as a PyInstaller
sidecar.** The engine is not rewritten; it is app-ified (paths/config/CLI)
and wrapped. The shell owns process lifecycle, tray, windows, onboarding,
and macOS permissions; the engine keeps owning everything Parker actually
does (ASR, repair, policy gates, pipeline, surfaces).

### Why this and not the alternatives

- **Electron shell (OpenClaw's choice).** Works — OpenClaw's macOS menu-bar
  app proves the shape (own permissions, manage/attach to a local gateway).
  Rejected for Parker v0: ~150 MB+ baseline vs ~10 MB, a second bundled
  runtime (Node) next to the Python sidecar, and Tauri's Rust core gives us
  child-process management, single-instance, tray, and autostart as
  first-class plugins.
- **Rewrite the engine in Rust (June AI's shape — Axum backend + Tauri).**
  June's structure is the closest twin (voice, local agent, privacy-first,
  menu-bar) and we borrow its lifecycle patterns liberally below — but its
  backend is Rust because it started that way. Parker's engine is 530
  tests of Python behavior (policy gates, repair, evals). A rewrite is all
  risk, no user value.
- **py2app instead of PyInstaller.** py2app builds .app bundles directly
  but is setuptools-bound, slower-moving, and worse at native-lib edge
  cases (ctranslate2, PortAudio). PyInstaller has explicit hook
  infrastructure for both of our fiddly natives. Documented fallbacks if
  PyInstaller hits a wall: onedir mode (default plan is onedir — see
  below), Briefcase, vendored wheels.
- **Ship the repo + install script.** That is what we have today; it
  filters out exactly the families Parker exists for.

Attribution: sidecar lifecycle, port, and readiness patterns adapted from
June AI (github.com/open-software-network/os-june, MIT — see
THIRD_PARTY_NOTICES note in `desktop/`); reconnect/backoff and menu-bar
patterns from MacClaw (github.com/itsnex1s/MacClaw, Tauri); gateway
lifecycle framing from OpenClaw's macOS app (docs.openclaw.ai).

## Process model

```
Parker.app (Tauri v2, menu-bar/tray, ActivationPolicy::Accessory)
  ├─ spawns → parker-engine serve  (PyInstaller sidecar: FastAPI/uvicorn,
  │            SQLite, scheduler; localhost only, dynamic port)
  ├─ spawns → parker-engine talk   (the voice loop; started/stopped by the
  │            tray "Start/Pause listening" toggle; same binary, second proc)
  └─ later  → openclaw gateway     (patient-identity instance; same generic
               sidecar manager, second entry — designed for, not built)
```

- **Spawn, don't attach (v0).** OpenClaw's app attaches-first and manages
  the gateway via a launchd LaunchAgent — but its *unsigned dev builds
  deliberately disable launchd* (`~/.openclaw/disable-launchagent`) because
  launchd referencing unsigned binaries misbehaves. Parker ships unsigned
  this milestone, so the shell owns the children directly: spawn on launch,
  kill on quit. launchd (auto-start of the engine without the shell) is a
  post-signing follow-up, and the runbook keeps `make run` for developers
  who want an engine without the app.
- **Port.** The shell picks a free ephemeral port (June's `pick_port`
  pattern: bind `127.0.0.1:0`, take the OS-assigned port, release, pass
  `--port`). No hardcoded app port, no collision with a developer's
  `make run` on 8000. Window URLs are built from the live port.
- **Readiness.** Poll `GET /health` (already exists) every 500 ms, 45 s
  timeout (June's `wait_for_hermes` numbers). The engine additionally
  exposes `GET /setup/status` for first-run state.
- **Crash → restart with backoff.** Child exit detected via `try_wait()`;
  restart delays 1s → 2s → 4s → 8s → 15s cap (MacClaw's reconnect curve),
  reset after a healthy minute. The tray shows the degraded state.
- **Orphan protection.** The engine gets `--parent-pid`; a watchdog thread
  exits the sidecar if the parent dies (crashed shell must not leave a
  headless engine holding the mic). Belt and braces with kill-on-quit.
- **Single instance.** `tauri-plugin-single-instance` on the shell; the
  engine's port-conflict handling (below) is the second fence.
- **Logs.** Engine stdout/stderr → `PARKER_HOME/logs/engine.log` (rotated
  by size, keep last 3). June nulls its sidecar output; Parker deliberately
  does not — `parker doctor` and family debugging need the trail.
- **Two-sidecar future.** The shell's sidecar manager is a list of
  `SidecarSpec { key, program, args, health_url, log_name }` — the engine
  is entry one; the family's patient-identity OpenClaw gateway becomes
  entry two with zero manager changes (June proves the shape with its
  sandboxed/unrestricted Hermes pair keyed in one HashMap).

## Config and data layout

Everything lives under **`PARKER_HOME`**, default
`~/Library/Application Support/Parker/` (non-macOS fallback `~/.parker`,
keeps CI honest):

```
Parker/
  config.json    # family-administered settings; NEVER secrets
  parker.db      # SQLite (pipeline, outbox, screen state, repair events)
  models/        # faster-whisper weights (downloaded on first run, NOT in dmg)
  logs/          # engine.log, talk.log
  digests/       # parker-digest-YYYY-MM-DD.md (local, unsent)
```

- **Precedence: env vars > `.env` (dev) > `config.json` > defaults**, via a
  pydantic-settings custom source. The onboarding wizard writes
  `config.json` through the engine; power users and the Makefile keep env
  vars. Dev/test flows export `PARKER_HOME=$(abspath backend)` so every
  existing path (backend/parker.db, backend/digests) is byte-identical.
- **Secrets are env-or-keychain only.** The JSON source *drops*
  key/token/password fields even if someone hand-edits them into
  `config.json` (pinned by test); the write endpoint refuses them.
- **Models are downloaded, not bundled.** The dmg stays ~app-sized; the
  wizard's download step fetches whisper-base (~145 MB) to
  `PARKER_HOME/models` with progress from the engine. Dev machines with an
  existing HF cache keep using it — no re-download (the loader checks
  PARKER_HOME/models, then the HF cache, then downloads).

## Permission flow (mic / TCC)

- Bundle carries `NSMicrophoneUsageDescription` (Info.plist) and
  `com.apple.security.device.audio-input` (entitlements), June-style.
- The TCC prompt fires when a process in the app's responsibility group
  first opens the mic. Because the engine is a child of Parker.app, macOS
  attributes the request to Parker.app — the same reason June's bundled
  Hermes and OpenClaw's Electron-owned gateway work. **The wizard owns the
  moment**: a dedicated "microphone check" step calls the engine's mic-level
  endpoint (2 s RMS sample) so the system prompt appears in context, not at
  some random first use — and the level meter doubles as proof the right
  input device works.
- Acceptance explicitly verifies the prompt appears attributed to Parker
  (unsigned apps: this is the risk point; if attribution breaks, the
  fallback is triggering the first mic open from the shell process instead).

## Update path

- **v0 (this milestone): manual replace.** Download new dmg, drag to
  Applications, replace. `PARKER_HOME` survives untouched. Documented in
  `docs/desktop.md` with the unsigned-app right-click-open Gatekeeper
  workaround.
- **Next: `tauri-plugin-updater`** against a GitHub Releases `latest.json`
  (June's exact setup) — requires the signing story first: updater
  artifacts are signature-checked, and Gatekeeper-friendly auto-relaunch
  needs a Developer ID. The signing/notarization checklist lives in
  `docs/desktop.md` ready for when the Apple Developer ID arrives.
- DB schema across updates: v0 stays `create_tables()` (additive only);
  a destructive schema change requires an explicit reset story before it
  ships in an app update (the app must never silently eat the family's
  history — that is the digest's trust surface).

## Engine changes (Phase 1 contract)

Minimal, tested, no guard weakened:

- `app/paths.py` — single module resolving every state path through
  `PARKER_HOME`; lazy dir creation; model-dir resolution with HF-cache
  fallback.
- `config.json` layered under env via pydantic-settings custom source;
  allowlisted keys only, secrets excluded both directions.
- `parker` console script: `serve` (port handling: busy port → structured
  error distinguishing "another Parker" from "something else"; graceful
  shutdown; `--parent-pid` watchdog), `talk` (the loop), `onboard`
  (terminal fallback wizard), `doctor` (mic device, `say`, model, disk,
  port, DB — human + `--json`), `version`.
- Setup surface for the shell: `GET /setup/status` (needs_onboarding,
  model state), `POST /setup/config` (allowlisted writes),
  `POST /setup/model/download` + `GET /setup/model/status` (progress),
  `GET /parker/loop/state` (idle/listening/speaking for the tray icon,
  published by the talk process).
- Make targets stay thin wrappers; `make demo`, `make talk-loop`, tests,
  and evals unchanged in behavior.

## Packaging (Phase 2 contract)

- PyInstaller **onedir** (not onefile): ctranslate2 + PortAudio ship as
  real dylibs next to the executable — faster startup (no per-launch
  self-extract; matters for a menu-bar app respawning a crashed engine),
  and June bundles its runtime as a directory under `Resources/` the same
  way. The Tauri side spawns the inner executable by path from the
  bundle's resources. `make sidecar` builds it; the `.spec` is committed;
  a smoke script must pass from a clean shell: `/health`, one
  fake-transcriber text-loop turn, `parker doctor`.
- If PyInstaller genuinely walls (after real iteration, not first error):
  stop and report with options — onefile, Briefcase, vendored wheels.

## What the shell never does

The trust boundary does not move: the shell has no policy logic, no DB
access, no send paths. It renders the engine's existing localhost pages
(`/parker/screen`, `/parker/review/ui`, `/parker/digest`) in webviews and
speaks to documented engine endpoints. Anything Parker *does* still goes
through capture → resolve → stage → confirm → execute in the engine, with
the same guards the test suite pins.
