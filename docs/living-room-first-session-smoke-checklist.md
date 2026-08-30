# Living Room First Session — packaged smoke checklist

Status: local phase-2 candidate. The automated contract is tested with synthetic/injected inputs. Every item that needs a built Parker.app, WKWebView, macOS microphone/TCC, a real input device, room acoustics, or a person speaking remains **UNVERIFIED ON DEVICE** until an explicitly approved packaged run.

## Automated contract (safe tonight)

- [ ] `backend/.venv/bin/pytest backend/tests/test_first_session.py -q`
  - explicit `open`/`wake` persistence;
  - sanitized wake name survives a fresh `Settings` load;
  - setup cannot start from an implicit historical `open` default or without the local speech model;
  - request/starting/error never reports `listening=true`;
  - timeout/cancel rejects a late shell acknowledgement;
  - TV-shaped ambient text is silent, an addressed reminder reaches ordinary capture/offer, bare invited `yes` executes one local reminder, one directed outcome is recorded, and every synthetic recording path is gone;
  - talk startup loads the local model and proves the microphone can open before publishing an active state.
- [ ] `TAURI_CONFIG='{"bundle":{"resources":[]}}' cargo test --lib` from `desktop/src-tauri`
  - Voice Practice blocks first-session start before TALK spawn;
  - an existing TALK process is reused rather than duplicated;
  - only `listening` / `processing` / `speaking` count as active.
- [ ] Extract the inline setup script and run `node --check`.
- [ ] Run the focused Python gate, canonical UTC gates, `git diff --check`, and the Rust compile check recorded in the phase-2 final report.

## Packaged Parker.app / WKWebView checks — UNVERIFIED ON DEVICE

Use a disposable local Parker home with synthetic/non-private phrases. Do not use patient audio or claim home deployment from this pass.

### 1. First-run choice and persistence

- [ ] **UNVERIFIED ON DEVICE:** Setup shows no preselected address mode for an older/unconfigured profile.
- [ ] Choose **Living room**, enter a punctuated wake name such as `Parker!!!`, finish setup, quit, and relaunch.
- [ ] Reopen Settings / Setup and verify `wake` plus sanitized `parker` are selected from `config.json`; the app did not fall back to `open`.
- [ ] Separately choose **Desk / push-to-talk** in the disposable profile and verify that explicit choice persists. Do not use this mode for the living-room acceptance run.

### 2. Microphone allow and deny

- [ ] **UNVERIFIED TCC:** On a fresh app identity/profile, choose **Allow** at the setup microphone step. Verify the meter reports the selected device and Continue remains blocked until the check succeeds.
- [ ] **UNVERIFIED TCC:** On a separate fresh app identity/profile, deny microphone access. Verify setup stays legibly blocked, retains no audio, and offers only the bounded permission/settings retry. Do not change TCC during the overnight worker run.

### 3. Missing model

- [ ] **UNVERIFIED PACKAGED:** Before the model is ready, `Start first session` must not create TALK or claim listening. The setup/model recovery remains visible.
- [ ] Complete the existing local model download, retry once, and verify startup can proceed without a second download.

### 4. Voice Practice contention

- [ ] **UNVERIFIED WKWEBVIEW:** Start listening, then open Voice Practice. Verify TALK pauses before the Practice page can own the microphone.
- [ ] While Voice Practice remains open, request `Start first session`. Verify no second TALK process appears and setup says Practice owns the microphone.
- [ ] Close Practice. Use the visible **Try again** action once. Verify this explicit action—not hidden background coordination—starts TALK.

### 5. Talk-sidecar or Dad Screen start failure

- [ ] **UNVERIFIED PACKAGED FAILURE INJECTION:** With a deliberately broken local test bundle/process fixture, request the first session. Verify setup remains `listening=false`, names the local failure, and offers **Try again**.
- [ ] If the Dad Screen cannot open, verify the just-started TALK process is stopped so no hidden microphone capture remains.
- [ ] Cancel/close setup during startup and verify any late shell completion is rejected and TALK is stopped.

### 6. Successful wake-gated first interaction

- [ ] **UNVERIFIED REAL MICROPHONE/ROOM:** With Living room mode active and the Dad Screen visible, play or speak a synthetic TV-shaped line without the wake name. Verify no speech, choices, capture, or Dad Screen update.
- [ ] Say `Parker, remind me to water the plants` using a non-private test voice. Verify Dad Screen shows the existing capture/read-back and Parker asks the ordinary yes/no confirmation.
- [ ] Say bare `yes` without the wake name. Verify exactly one local reminder reaches `executed`, `confirmed_by=patient`; no external message, send, purchase, device action, or cloud audio occurs.
- [ ] Verify exactly one non-ambient `understood_first_try` outcome for the addressed request. Ambient audit rows may exist separately as `ambient_noop`.
- [ ] Verify no conversation audio remains after transcription.

### 7. Quit and relaunch

- [ ] **UNVERIFIED PACKAGED:** Quit Parker and verify engine plus TALK stop.
- [ ] Relaunch and verify setup persistence survives, TALK does not falsely appear active, and the tray/Dad Screen agree with the live loop state.
- [ ] Start once from the persisted setup and repeat the synthetic addressed reminder without changing policy or Voice Practice behavior.

## Evidence language

Passing automated checks means **tested local candidate**. Passing this checklist on a packaged test Mac means **packaged device smoke passed**. Neither means installed in the home, used by Dad, validated against Dad's speech, preferred by a beneficiary, clinically effective, or generally reliable in a TV-on living room.
