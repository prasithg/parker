# Parker as a macOS app

Parker.app is the family-installable form of Parker: a menu-bar app that
bundles the whole engine — no Python, no terminal, no git. Architecture
decisions live in [desktop-architecture.md](desktop-architecture.md);
this page is the lifecycle a family actually experiences, plus the
operational corners (data, logs, updates, uninstall, signing).

Current status: **beta, unsigned, Apple silicon (arm64)**. Built and
acceptance-tested on a real machine from the dmg (2026-07-02): tray
launch → onboarding → real model download → a spoken conversation with
a spoken "Yes, go ahead" confirmation → quit/relaunch → `parker doctor`
all green.

## Install

1. Download `Parker_<version>_aarch64.dmg` (or build it: `make sidecar`,
   then `cd desktop/src-tauri && cargo tauri build`).
2. Open the dmg, drag **Parker** into **Applications**.
3. First open of an unsigned app: **right-click (or Control-click)
   Parker.app → Open → Open**. Double-clicking shows "Apple could not
   verify…" — the right-click route offers the Open button. You do this
   once; afterwards Parker opens normally. (Goes away when builds are
   signed + notarized; checklist below.)
4. Parker appears in the **menu bar** (speech-bubble icon). There is no
   Dock icon — the menu bar is the app.

## First run — the onboarding wizard

Parker opens a setup window on first launch (it is also just a page the
engine serves — `http://127.0.0.1:<port>/setup/ui` works in any
browser). The wizard walks the family administrator through:

- patient first name; family contacts (the message allowlist — a spoken
  "yes" releases messages to these people; everyone else stays behind
  family review); lexicon extras (words Parker should be primed to hear);
- an explicit address choice: **Living room** (`wake`, recommended for a TV
  or room in microphone range) or **Desk / push-to-talk** (`open`), plus a
  sanitized wake name. An older install with no stored choice shows neither
  mode selected, so living-room setup cannot silently inherit `open`;
- Parker's voice, with a spoken preview;
- plain-language consent: what is stored (settings, pending actions,
  heard text), what never happens (nothing sent anywhere, audio never
  kept, no accounts/cloud/analytics), and the **opt-in** local
  repair-notes toggle (off by default);
- the microphone moment: a level check that deliberately triggers the
  macOS permission prompt right there — click **Allow**;
- the one-time speech-model download (~150 MB to Parker's own folder;
  a machine that already has the model in a Hugging Face cache skips
  the download);
- done → one keyboard-operable **Start first session** action. This is the
  legacy Dad Screen + TALK loop (the companion below is the person-facing
  window on every later launch). Parker.app
  starts the existing TALK sidecar and opens the existing Dad Screen as one
  shell-owned operation. Setup says Parker is listening only after the model
  and microphone preflight passes, TALK remains alive in an active loop state,
  and the Dad Screen opens. A timeout or closed setup page uses the same
  cancellation path. If the shell has claimed startup, setup shows cleanup as
  pending until the shell revokes the request-owned TALK/Dad Screen lease; it
  never labels cancellation "not listening" before that acknowledgement. A TALK
  process that was already running—or replaced the request-owned process—is not
  killed by cancelling this setup request.

Settings land in `config.json` (below) — never secrets; there is no
API-key field anywhere in the app. After onboarding, **Start at Login**
is switched on once automatically (toggle it any time in the tray menu).

## Daily use

Once onboarding is done, Parker.app opens the **companion** on launch: the
full-screen virtual Reachy at `/parker/converse` with one power switch and
one captions toggle. It is the person-facing window. The companion holds
the microphone inside its own webview (local wake word, realtime line), so
opening it pauses the legacy talk loop; while it is open, Start Listening
and the setup wizard's first session refuse rather than contend for the mic.

The tray menu is the whole interface for the family:

- **Open Parker** — (re)opens the companion.
- **Start/Pause Listening** — the legacy/lab talk loop (a separate TALK
  sidecar with the Dad Screen as its display). The
  tray icon mirrors the loop: outline = idle, filled = listening,
  filled + waves = speaking.
- **Voice Practice** — opens the patient-paced practice app: manual
  Start/Stop/Save/Next/Finish controls, device-relative microphone feedback, local
  attempt history, and an explicit per-round audio-retention choice. If
  Parker is listening, the shell pauses that talk loop before the practice
  page opens so the two surfaces never contend for the microphone.
  First-session startup also refuses while the Practice window exists. Closing
  Practice does not invent background auto-resume; **Try again** / **Start
  Listening** is the explicit one-click handoff.
- **Open Dad Screen (legacy)** — the big-type live window for the talk
  loop: what Parker heard, what it said, numbered choices. Voice
  stays the only input. Kept as a lab surface; the companion replaces it.
- **Family Review** — everything waiting on a human decision.
- **Daily Digest** — what happened, what needs a look, all local.
- **Settings / Setup…** — re-opens the wizard page.
- **Quit Parker** — stops everything, including the engine.

The person being helped never touches any of this: they talk, the
screen shows, Parker checks before acting.

## Where everything lives

One folder: `~/Library/Application Support/Parker/`

| Path | What |
|---|---|
| `config.json` | family-administered settings (never secrets) |
| `parker.db` | SQLite: reminders, drafts, history, screen state |
| `models/` | downloaded whisper weights |
| `logs/engine.log`, `logs/talk.log` | live-tailing friendly, size-rotated |
| `digests/` | daily digest markdown artifacts |

The engine binary itself ships inside the bundle at
`Parker.app/Contents/Resources/engine/parker` — it is the `parker` CLI
(`serve`, `talk`, `doctor`, `selftest`, `download-model`, `onboard`,
`version`), usable directly:

```bash
"/Applications/Parker.app/Contents/Resources/engine/parker" doctor
```

`doctor` checks home/database writability, microphone presence, `say`,
model, disk space, and the engine port — human output or `--json`.

## Updates

Manual for now: download the new dmg, quit Parker, drag the new
Parker.app over the old one, right-click-open once. Everything under
`Application Support/Parker` (settings, history, model) is untouched —
verified by the acceptance run, which reinstalled mid-onboarding and
resumed cleanly. `tauri-plugin-updater` against GitHub Releases is the
planned path once builds are signed.

## Uninstall

1. Quit Parker (tray → Quit).
2. Delete `/Applications/Parker.app`.
3. Delete `~/Library/Application Support/Parker/` (this is the family's
   data — reminders, history, settings; gone means gone).
4. If Start at Login was enabled: `~/Library/LaunchAgents/Parker.plist`.

## Developer corner

```bash
make sidecar                      # PyInstaller onedir → backend/dist/parker/
scripts/sidecar_smoke.sh          # clean-shell gate: selftest+natives, /health, doctor
scripts/packaged_companion_probe.sh  # headless: the built app opens the companion in WKWebView,
                                     # renders the scene (webgl_ready receipt), touches no mic
cd desktop/src-tauri && cargo tauri build   # → Parker.app + .dmg
```

Rust via rustup (this repo built with rustc 1.96.1, tauri-cli 2.11.4,
Tauri 2.x). The shell finds the engine via bundle resources; `cargo
tauri dev` falls back to `backend/dist/parker/parker`, and
`PARKER_ENGINE_BIN` overrides both. Dev flows (`make run`,
`make talk-loop`, tests) are unaffected by the app: in a repo checkout
`PARKER_HOME` defaults to `backend/`, so nothing moves.

Known quirks, honestly held:

- The talk loop shrugs off SIGINT when run as the frozen binary (the
  shell's Pause uses SIGKILL, so app behavior is unaffected; Ctrl-C in
  `make talk-loop` from the venv works normally). Untangling the frozen
  signal path is future work.
- macOS `say` played through the Mac's own speakers sits near the VAD
  energy threshold at moderate volume — relevant only to synthetic
  self-talk demos, not a person in the room.
- The engine's port is dynamic; find it with
  `lsof -nP -iTCP -sTCP:LISTEN | grep parker` when curling by hand.
- The Living Room First Session code path has deterministic Python/Rust/JS
  coverage, but the changed setup-to-TALK handoff has not yet received a new
  packaged WKWebView/TCC/device run. Follow
  [the packaged smoke checklist](living-room-first-session-smoke-checklist.md);
  do not describe this candidate as home-deployed or first-user-tested.

## Signing & notarization checklist (when the Developer ID arrives)

1. **Certificates**: create a "Developer ID Application" certificate;
   install in the login keychain (or an ephemeral CI keychain).
2. **Configure Tauri**: `bundle.macOS.signingIdentity` = the Developer
   ID identity; add `Entitlements.plist` with
   `com.apple.security.device.audio-input` (mic) and set
   `bundle.macOS.entitlements` — required once the binary is
   hardened-runtime signed.
3. **Sign the sidecar too**: PyInstaller output must be signed with the
   same identity + hardened runtime before `tauri build` bundles it
   (`codesign --deep --force --options runtime backend/dist/parker`),
   or Gatekeeper flags the nested binaries.
4. **Notarize**: `xcrun notarytool submit <dmg> --keychain-profile
   <profile> --wait`, then `xcrun stapler staple Parker.app` and the
   dmg. (June AI's `build-signed-dmg.sh` is a good reference shape.)
5. **Re-test TCC**: the mic prompt attribution and launchd autostart
   behave differently for signed apps — re-run the acceptance list.
6. **Then**: enable `tauri-plugin-updater` (signature-checked update
   artifacts require the signing story) and consider OpenClaw-style
   launchd management of the engine.
