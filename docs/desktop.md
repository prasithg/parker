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
- done → pointer to [pilot-recording-protocol.md](pilot-recording-protocol.md).

Settings land in `config.json` (below) — never secrets; there is no
API-key field anywhere in the app. After onboarding, **Start at Login**
is switched on once automatically (toggle it any time in the tray menu).

## Daily use

The tray menu is the whole interface for the family:

- **Start/Pause Listening** — runs Parker's ears (the talk loop). The
  tray icon mirrors the loop: outline = idle, filled = listening,
  filled + waves = speaking.
- **Open Dad Screen** — the big-type live window for the TV/monitor by
  the chair: what Parker heard, what it said, numbered choices. Voice
  stays the only input.
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
