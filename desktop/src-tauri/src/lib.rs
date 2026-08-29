//! Parker.app — the menu-bar shell around the Python engine sidecar.
//!
//! The shell has no policy logic, no DB access, no send paths
//! (docs/desktop-architecture.md, "What the shell never does"). It:
//! spawns the bundled engine on a free port, waits for /health, opens
//! the onboarding wizard on first run, restarts a crashed engine with
//! backoff, mirrors the voice-loop state in the tray icon, opens the
//! engine's own pages (voice practice / dad screen / review / digest / setup) as
//! windows, and toggles the talk-loop sidecar.

mod sidecar;

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{mpsc, Mutex, MutexGuard};
use std::time::{Duration, Instant};

use tauri::image::Image;
use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder, Wry};
use tauri_plugin_autostart::ManagerExt;

use sidecar::{SidecarManager, SidecarSpec};

const ENGINE: &str = "engine";
const TALK: &str = "talk";
const HEALTH_WAIT_MS: u64 = 45_000; // June's wait_for_hermes budget
const HEALTH_POLL_MS: u64 = 500;
const TRAY_POLL_MS: u64 = 2_000;
const FIRST_SESSION_WAIT_SECS: u64 = 15;
const RESTART_BACKOFF_SECS: [u64; 5] = [1, 2, 4, 8, 15]; // MacClaw's curve

const ICON_IDLE: &[u8] = include_bytes!("../icons/tray-idle.png");
const ICON_LISTENING: &[u8] = include_bytes!("../icons/tray-listening.png");
const ICON_SPEAKING: &[u8] = include_bytes!("../icons/tray-speaking.png");

#[derive(Default)]
struct MicrophoneTransition {
    gate: Mutex<()>,
}

impl MicrophoneTransition {
    fn enter(&self) -> MutexGuard<'_, ()> {
        self.gate.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
    }
}

pub struct AppState {
    manager: SidecarManager,
    engine_bin: PathBuf,
    port: u16,
    healthy: AtomicBool,
    quitting: AtomicBool,
    onboarding_pending: AtomicBool,
    restart_strikes: AtomicU32,
    microphone_transition: MicrophoneTransition,
}

impl AppState {
    fn base_url(&self) -> String {
        format!("http://127.0.0.1:{}", self.port)
    }

    fn engine_spec(&self) -> SidecarSpec {
        SidecarSpec {
            key: ENGINE,
            program: self.engine_bin.clone(),
            args: vec![
                "serve".into(),
                "--port".into(),
                self.port.to_string(),
                "--parent-pid".into(),
                std::process::id().to_string(),
            ],
            log_name: "engine",
        }
    }

    fn talk_spec(&self) -> SidecarSpec {
        SidecarSpec {
            key: TALK,
            program: self.engine_bin.clone(),
            args: vec!["talk".into(), "--port".into(), self.port.to_string()],
            log_name: "talk",
        }
    }
}

struct TrayHandles {
    tray: tauri::tray::TrayIcon<Wry>,
    status: MenuItem<Wry>,
    listen: MenuItem<Wry>,
    autostart: CheckMenuItem<Wry>,
}

#[derive(Debug, PartialEq, Eq)]
enum FirstSessionStartDecision {
    BlockedByPractice,
    ReuseTalk,
    SpawnTalk,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum FirstSessionLeaseAction {
    Continue,
    Revoke {
        owned_talk_pid: Option<u32>,
        close_dad_screen: bool,
    },
}

fn first_session_start_decision(
    voice_practice_open: bool,
    talk_running: bool,
) -> FirstSessionStartDecision {
    if voice_practice_open {
        FirstSessionStartDecision::BlockedByPractice
    } else if talk_running {
        FirstSessionStartDecision::ReuseTalk
    } else {
        FirstSessionStartDecision::SpawnTalk
    }
}

fn active_talk_state(state: &str) -> bool {
    matches!(state, "listening" | "processing" | "speaking")
}

fn first_session_lease_action(
    status: Option<&serde_json::Value>,
    request_id: u64,
    owned_talk_pid: Option<u32>,
    opened_dad_screen: bool,
) -> FirstSessionLeaseAction {
    let authorized = status
        .and_then(|value| {
            Some((value.get("request_id")?.as_u64()?, value.get("state")?.as_str()?))
        })
        .map(|(current_id, state)| current_id == request_id && state == "starting")
        .unwrap_or(false);
    if authorized {
        FirstSessionLeaseAction::Continue
    } else {
        FirstSessionLeaseAction::Revoke {
            owned_talk_pid,
            close_dad_screen: opened_dad_screen,
        }
    }
}

// Handles are cheap id-wrappers; updates happen via run_on_main_thread.
unsafe impl Send for TrayHandles {}
unsafe impl Sync for TrayHandles {}

fn parker_home() -> PathBuf {
    if let Ok(custom) = std::env::var("PARKER_HOME") {
        if !custom.trim().is_empty() {
            return PathBuf::from(custom.trim());
        }
    }
    dirs::home_dir()
        .expect("no home directory")
        .join("Library/Application Support/Parker")
}

fn resolve_engine_binary(app: &AppHandle) -> Result<PathBuf, String> {
    if let Ok(custom) = std::env::var("PARKER_ENGINE_BIN") {
        let path = PathBuf::from(custom);
        if path.is_file() {
            return Ok(path);
        }
        return Err(format!("PARKER_ENGINE_BIN does not exist: {}", path.display()));
    }
    if let Ok(resources) = app.path().resource_dir() {
        let bundled = resources.join("engine").join("parker");
        if bundled.is_file() {
            return Ok(bundled);
        }
    }
    // Dev fallback (cargo tauri dev): the repo's sidecar build output.
    if let Some(manifest) = option_env!("CARGO_MANIFEST_DIR") {
        let dev = PathBuf::from(manifest).join("../../backend/dist/parker/parker");
        if dev.is_file() {
            return Ok(dev.canonicalize().unwrap_or(dev));
        }
    }
    Err("engine binary not found — build it with `make sidecar`".into())
}

fn pick_port() -> u16 {
    std::net::TcpListener::bind("127.0.0.1:0")
        .ok()
        .and_then(|listener| listener.local_addr().ok())
        .map(|addr| addr.port())
        .unwrap_or(48123)
}

fn health_ok(base_url: &str) -> bool {
    ureq::get(&format!("{base_url}/health"))
        .timeout(Duration::from_secs(2))
        .call()
        .map(|response| response.status() == 200)
        .unwrap_or(false)
}

fn get_json(url: &str) -> Option<serde_json::Value> {
    ureq::get(url)
        .timeout(Duration::from_secs(2))
        .call()
        .ok()?
        .into_json()
        .ok()
}

fn post_json(url: &str, body: serde_json::Value) -> Result<serde_json::Value, String> {
    ureq::post(url)
        .timeout(Duration::from_secs(2))
        .send_json(body)
        .map_err(|error| error.to_string())?
        .into_json()
        .map_err(|error| error.to_string())
}

fn set_status_text(app: &AppHandle, text: String) {
    let handle = app.clone();
    let _ = app.run_on_main_thread(move || {
        if let Some(handles) = handle.try_state::<TrayHandles>() {
            let _ = handles.status.set_text(&text);
        }
    });
}

fn set_tray_state(app: &AppHandle, loop_state: &str) {
    let bytes: &'static [u8] = match loop_state {
        "listening" | "processing" => ICON_LISTENING,
        "speaking" => ICON_SPEAKING,
        _ => ICON_IDLE,
    };
    let handle = app.clone();
    let _ = app.run_on_main_thread(move || {
        if let Some(handles) = handle.try_state::<TrayHandles>() {
            if let Ok(icon) = Image::from_bytes(bytes) {
                let _ = handles.tray.set_icon(Some(icon));
                let _ = handles.tray.set_icon_as_template(true);
            }
        }
    });
}

fn set_listen_label(app: &AppHandle, listening: bool) {
    let handle = app.clone();
    let text = if listening { "Pause Listening" } else { "Start Listening" };
    let _ = app.run_on_main_thread(move || {
        if let Some(handles) = handle.try_state::<TrayHandles>() {
            let _ = handles.listen.set_text(text);
        }
    });
}

fn open_engine_window(
    app: &AppHandle,
    label: &str,
    path: &str,
    title: &str,
    fullscreen: bool,
) -> Result<bool, String> {
    if let Some(existing) = app.get_webview_window(label) {
        existing.show().map_err(|error| error.to_string())?;
        existing.set_focus().map_err(|error| error.to_string())?;
        return Ok(false);
    }
    let state = app.state::<AppState>();
    let url = format!("{}{}", state.base_url(), path)
        .parse()
        .map_err(|error| format!("invalid engine window URL: {error}"))?;
    let mut builder =
        WebviewWindowBuilder::new(app, label, WebviewUrl::External(url)).title(title);
    if fullscreen {
        builder = builder.fullscreen(true);
    } else {
        builder = builder.inner_size(1100.0, 800.0);
    }
    builder
        .build()
        .map(|_| true)
        .map_err(|error| error.to_string())
}

fn open_engine_window_wait(
    app: &AppHandle,
    label: &'static str,
    path: &'static str,
    title: &'static str,
    fullscreen: bool,
) -> Result<bool, String> {
    let (sender, receiver) = mpsc::sync_channel(1);
    let handle = app.clone();
    app.run_on_main_thread(move || {
        let result = open_engine_window(&handle, label, path, title, fullscreen);
        let _ = sender.send(result);
    })
    .map_err(|error| error.to_string())?;
    receiver
        .recv_timeout(Duration::from_secs(5))
        .map_err(|_| "desktop window did not answer within 5 seconds".to_string())?
}

fn close_engine_window_wait(app: &AppHandle, label: &'static str) -> Result<(), String> {
    let (sender, receiver) = mpsc::sync_channel(1);
    let handle = app.clone();
    app.run_on_main_thread(move || {
        let result = handle
            .get_webview_window(label)
            .map(|window| window.close().map_err(|error| error.to_string()))
            .unwrap_or(Ok(()));
        let _ = sender.send(result);
    })
    .map_err(|error| error.to_string())?;
    receiver
        .recv_timeout(Duration::from_secs(5))
        .map_err(|_| "desktop window did not close within 5 seconds".to_string())?
}

fn autostart_marker(state: &AppState) -> PathBuf {
    state.manager.home().join(".autostart-initialized")
}

/// First-boot thread: wait for /health, then decide wizard-or-quiet.
fn boot_thread(app: AppHandle) {
    let state = app.state::<AppState>();
    let base = state.base_url();
    let deadline = std::time::Instant::now() + Duration::from_millis(HEALTH_WAIT_MS);
    let mut up = false;
    while std::time::Instant::now() < deadline {
        if state.quitting.load(Ordering::SeqCst) {
            return;
        }
        if health_ok(&base) {
            up = true;
            break;
        }
        std::thread::sleep(Duration::from_millis(HEALTH_POLL_MS));
    }
    if !up {
        set_status_text(
            &app,
            "Parker — engine failed to start (see Application Support/Parker/logs)".into(),
        );
        return;
    }
    state.healthy.store(true, Ordering::SeqCst);
    set_status_text(&app, "Parker — ready".into());

    if let Some(status) = get_json(&format!("{base}/setup/status")) {
        let needs_onboarding = status
            .get("needs_onboarding")
            .and_then(|value| value.as_bool())
            .unwrap_or(false);
        state
            .onboarding_pending
            .store(needs_onboarding, Ordering::SeqCst);
        if needs_onboarding {
            let handle = app.clone();
            let _ = app.run_on_main_thread(move || {
                let _ = open_engine_window(&handle, "setup", "/setup/ui", "Set up Parker", false);
            });
        }
    }
}

fn acknowledge_first_session(
    base_url: &str,
    request_id: u64,
    state: &str,
    message: &str,
) -> Result<(), String> {
    post_json(
        &format!("{base_url}/setup/first-session/result"),
        serde_json::json!({
            "request_id": request_id,
            "state": state,
            "message": message,
        }),
    )
    .map(|_| ())
}

fn loop_snapshot(base_url: &str) -> Option<(String, Option<String>)> {
    let body = get_json(&format!("{base_url}/parker/loop/state"))?;
    let state = body.get("state")?.as_str()?.to_string();
    let updated_at = body
        .get("updated_at")
        .and_then(|value| value.as_str())
        .map(str::to_string);
    Some((state, updated_at))
}

fn cleanup_first_session(
    app: &AppHandle,
    base_url: &str,
    request_id: u64,
    owned_talk_pid: Option<u32>,
    close_dad_screen: bool,
    message: Option<&str>,
) {
    let app_state = app.state::<AppState>();
    if let Some(pid) = owned_talk_pid {
        app_state.manager.kill_if_pid(TALK, pid);
    }
    let close_error = if close_dad_screen {
        close_engine_window_wait(app, "dad-screen").err()
    } else {
        None
    };

    let talk_running = app_state.manager.is_running(TALK);
    set_listen_label(app, talk_running);
    if talk_running {
        let loop_state = loop_snapshot(base_url)
            .map(|(state, _)| state)
            .unwrap_or_else(|| "listening".into());
        set_tray_state(app, &loop_state);
        set_status_text(app, format!("Parker — {loop_state}"));
    } else {
        set_tray_state(app, "idle");
        set_status_text(app, "Parker — ready (not listening)".into());
    }
    let observed_message = if talk_running {
        "First-session startup was cancelled. The existing listening loop is still running."
    } else {
        "First-session startup was cancelled. Nothing is listening."
    };
    let message = message.unwrap_or(observed_message);
    let cleanup_message = close_error
        .map(|error| format!("{message} The Dad Screen could not close: {error}"))
        .unwrap_or_else(|| message.to_string());
    let _ = acknowledge_first_session(base_url, request_id, "error", &cleanup_message);
}

fn continue_first_session_lease(
    app: &AppHandle,
    base_url: &str,
    request_id: u64,
    owned_talk_pid: Option<u32>,
    opened_dad_screen: bool,
) -> bool {
    let status = get_json(&format!("{base_url}/setup/first-session/status"));
    match first_session_lease_action(
        status.as_ref(),
        request_id,
        owned_talk_pid,
        opened_dad_screen,
    ) {
        FirstSessionLeaseAction::Continue => true,
        FirstSessionLeaseAction::Revoke {
            owned_talk_pid,
            close_dad_screen,
        } => {
            cleanup_first_session(
                app,
                base_url,
                request_id,
                owned_talk_pid,
                close_dad_screen,
                None,
            );
            false
        }
    }
}

fn process_first_session_request(app: &AppHandle, request: &serde_json::Value) {
    if request.get("state").and_then(|value| value.as_str()) != Some("requested") {
        return;
    }
    let Some(request_id) = request.get("request_id").and_then(|value| value.as_u64()) else {
        return;
    };

    let app_state = app.state::<AppState>();
    let base_url = app_state.base_url();
    let (owned_talk_pid, baseline_update) = {
        // One transition gate covers Practice-open/kill and TALK check/spawn.
        // Whichever path enters first completes ownership transfer before the
        // other can inspect or mutate the microphone process.
        let _transition = app_state.microphone_transition.enter();
        let practice_open = app.get_webview_window("voice-practice").is_some();
        let talk_running = app_state.manager.is_running(TALK);
        let decision = first_session_start_decision(practice_open, talk_running);

        if decision == FirstSessionStartDecision::BlockedByPractice {
            let _ = acknowledge_first_session(
                &base_url,
                request_id,
                "error",
                "Voice Practice is using the microphone. Finish or close it, then try again.",
            );
            return;
        }

        if acknowledge_first_session(
            &base_url,
            request_id,
            "starting",
            "Starting the existing local talk loop.",
        )
        .is_err()
        {
            return;
        }

        if !continue_first_session_lease(app, &base_url, request_id, None, false) {
            return;
        }

        let baseline_update = loop_snapshot(&base_url).and_then(|(_, updated)| updated);
        let owned_talk_pid = if decision == FirstSessionStartDecision::SpawnTalk {
            match app_state.manager.spawn(&app_state.talk_spec()) {
                Ok(pid) => Some(pid),
                Err(error) => {
                    let _ = acknowledge_first_session(
                        &base_url,
                        request_id,
                        "error",
                        &format!("Could not start the talk loop: {error}. Try again."),
                    );
                    return;
                }
            }
        } else {
            None
        };
        if !continue_first_session_lease(
            app,
            &base_url,
            request_id,
            owned_talk_pid,
            false,
        ) {
            return;
        }
        (owned_talk_pid, baseline_update)
    };

    let deadline = Instant::now() + Duration::from_secs(FIRST_SESSION_WAIT_SECS);
    let mut ready = false;
    while Instant::now() < deadline {
        if !continue_first_session_lease(app, &base_url, request_id, owned_talk_pid, false) {
            return;
        }
        if !app_state.manager.is_running(TALK) {
            break;
        }
        if let Some((loop_state, updated_at)) = loop_snapshot(&base_url) {
            let fresh = owned_talk_pid.is_none() || updated_at != baseline_update;
            if fresh && active_talk_state(&loop_state) {
                std::thread::sleep(Duration::from_millis(500));
                if !continue_first_session_lease(
                    app,
                    &base_url,
                    request_id,
                    owned_talk_pid,
                    false,
                ) {
                    return;
                }
                ready = app_state.manager.is_running(TALK)
                    && loop_snapshot(&base_url)
                        .map(|(state, _)| active_talk_state(&state))
                        .unwrap_or(false);
                if ready {
                    break;
                }
            }
        }
        std::thread::sleep(Duration::from_millis(100));
    }

    if !ready {
        cleanup_first_session(
            app,
            &base_url,
            request_id,
            owned_talk_pid,
            false,
            Some(
                "The local model or microphone did not become ready. Check the setup steps, then try again.",
            ),
        );
        return;
    }

    if !continue_first_session_lease(app, &base_url, request_id, owned_talk_pid, false) {
        return;
    }
    let opened_dad_screen = match open_engine_window_wait(
        app,
        "dad-screen",
        "/parker/screen",
        "Parker — Dad Screen",
        true,
    ) {
        Ok(opened) => opened,
        Err(error) => {
            cleanup_first_session(
                app,
                &base_url,
                request_id,
                owned_talk_pid,
                false,
                Some(&format!("The Dad Screen did not open: {error}. Try again.")),
            );
            return;
        }
    };

    if !continue_first_session_lease(
        app,
        &base_url,
        request_id,
        owned_talk_pid,
        opened_dad_screen,
    ) {
        return;
    }

    let still_ready = app.get_webview_window("voice-practice").is_none()
        && app_state.manager.is_running(TALK)
        && loop_snapshot(&base_url)
            .map(|(state, _)| active_talk_state(&state))
            .unwrap_or(false);
    if !still_ready {
        cleanup_first_session(
            app,
            &base_url,
            request_id,
            owned_talk_pid,
            opened_dad_screen,
            Some(
                "Listening changed while the Dad Screen opened. Close Voice Practice, then try again.",
            ),
        );
        return;
    }

    if !continue_first_session_lease(
        app,
        &base_url,
        request_id,
        owned_talk_pid,
        opened_dad_screen,
    ) {
        return;
    }
    set_listen_label(app, true);
    set_status_text(app, "Parker — listening".into());
    if acknowledge_first_session(
        &base_url,
        request_id,
        "listening",
        "Parker is listening. The Dad Screen is open.",
    )
    .is_err()
    {
        cleanup_first_session(
            app,
            &base_url,
            request_id,
            owned_talk_pid,
            opened_dad_screen,
            None,
        );
    }
}

fn report_stopped_first_session(base_url: &str) {
    let Some(status) = get_json(&format!("{base_url}/setup/first-session/status")) else {
        return;
    };
    if status.get("state").and_then(|value| value.as_str()) != Some("listening") {
        return;
    }
    if let Some(request_id) = status.get("request_id").and_then(|value| value.as_u64()) {
        let _ = acknowledge_first_session(
            base_url,
            request_id,
            "error",
            "Listening stopped. Close Voice Practice if it is open, then try again.",
        );
    }
}

/// Steady-state thread: crash-restart with backoff, tray state, autostart-once.
fn poll_thread(app: AppHandle) {
    loop {
        std::thread::sleep(Duration::from_millis(TRAY_POLL_MS));
        let state = app.state::<AppState>();
        if state.quitting.load(Ordering::SeqCst) {
            return;
        }

        // Engine crash → restart with backoff (skip while still booting).
        if state.healthy.load(Ordering::SeqCst) && !state.manager.is_running(ENGINE) {
            let strikes = state.restart_strikes.fetch_add(1, Ordering::SeqCst) as usize;
            let delay = RESTART_BACKOFF_SECS[strikes.min(RESTART_BACKOFF_SECS.len() - 1)];
            set_status_text(&app, format!("Parker — engine stopped, restarting in {delay}s"));
            set_tray_state(&app, "idle");
            std::thread::sleep(Duration::from_secs(delay));
            if state.quitting.load(Ordering::SeqCst) {
                return;
            }
            match state.manager.spawn(&state.engine_spec()) {
                Ok(_) => {
                    // Give it a boot window before judging it again.
                    let base = state.base_url();
                    for _ in 0..20 {
                        if health_ok(&base) {
                            state.restart_strikes.store(0, Ordering::SeqCst);
                            set_status_text(&app, "Parker — ready".into());
                            break;
                        }
                        std::thread::sleep(Duration::from_millis(HEALTH_POLL_MS));
                    }
                }
                Err(err) => {
                    set_status_text(&app, format!("Parker — engine restart failed: {err}"));
                }
            }
            continue;
        }

        let base = state.base_url();
        if let Some(request) = get_json(&format!("{base}/setup/first-session/status")) {
            process_first_session_request(&app, &request);
        }

        let talk_running = state.manager.is_running(TALK);
        set_listen_label(&app, talk_running);
        if !talk_running {
            report_stopped_first_session(&base);
        }

        let loop_state = if talk_running {
            get_json(&format!("{base}/parker/loop/state"))
                .and_then(|body| body.get("state").and_then(|v| v.as_str()).map(String::from))
                .unwrap_or_else(|| "idle".into())
        } else {
            "idle".into()
        };
        set_tray_state(&app, &loop_state);
        if state.healthy.load(Ordering::SeqCst) {
            let label = match (talk_running, loop_state.as_str()) {
                (false, _) => "Parker — ready (not listening)".to_string(),
                (true, "idle") => "Parker — listening loop starting…".to_string(),
                (true, other) => format!("Parker — {other}"),
            };
            set_status_text(&app, label);
        }

        // Autostart: on by default once onboarding completes, exactly once.
        if state.onboarding_pending.load(Ordering::SeqCst) {
            if let Some(status) = get_json(&format!("{base}/setup/status")) {
                let needs = status
                    .get("needs_onboarding")
                    .and_then(|value| value.as_bool())
                    .unwrap_or(true);
                if !needs {
                    state.onboarding_pending.store(false, Ordering::SeqCst);
                    let marker = autostart_marker(&state);
                    if !marker.exists() {
                        let _ = app.autolaunch().enable();
                        let _ = std::fs::write(&marker, "enabled by onboarding\n");
                        let handle = app.clone();
                        let _ = app.run_on_main_thread(move || {
                            if let Some(handles) = handle.try_state::<TrayHandles>() {
                                let _ = handles.autostart.set_checked(true);
                            }
                        });
                    }
                }
            }
        }
    }
}

fn on_menu_event(app: &AppHandle, event_id: &str) {
    match event_id {
        "voice-practice" => {
            let state = app.state::<AppState>();
            let _transition = state.microphone_transition.enter();
            if state.manager.is_running(TALK) {
                state.manager.kill(TALK);
                set_listen_label(app, false);
                set_tray_state(app, "idle");
            }
            let _ = open_engine_window(
                app,
                "voice-practice",
                "/parker/practice",
                "Parker — Voice Practice",
                false,
            );
        },
        "dad-screen" => {
            let _ = open_engine_window(app, "dad-screen", "/parker/screen", "Parker — Dad Screen", true);
        },
        "review" => {
            let _ = open_engine_window(app, "review", "/parker/review/ui", "Parker — Family Review", false);
        },
        "digest" => {
            let _ = open_engine_window(app, "digest", "/parker/digest", "Parker — Family Digest", false);
        },
        "setup" => {
            let _ = open_engine_window(app, "setup", "/setup/ui", "Parker — Settings", false);
        },
        "toggle-listen" => {
            let state = app.state::<AppState>();
            let _transition = state.microphone_transition.enter();
            if state.manager.is_running(TALK) {
                state.manager.kill(TALK);
                set_listen_label(app, false);
                set_tray_state(app, "idle");
            } else if app.get_webview_window("voice-practice").is_some() {
                set_status_text(
                    app,
                    "Parker — Voice Practice is using the microphone; close it before listening"
                        .into(),
                );
            } else {
                match state.manager.spawn(&state.talk_spec()) {
                    Ok(_) => set_listen_label(app, true),
                    Err(err) => set_status_text(app, format!("Parker — couldn't start listening: {err}")),
                }
            }
        }
        "autostart" => {
            let checked = app
                .try_state::<TrayHandles>()
                .map(|handles| handles.autostart.is_checked().unwrap_or(false))
                .unwrap_or(false);
            if checked {
                let _ = app.autolaunch().enable();
            } else {
                let _ = app.autolaunch().disable();
            }
        }
        "quit" => {
            let state = app.state::<AppState>();
            state.quitting.store(true, Ordering::SeqCst);
            state.manager.kill_all();
            app.exit(0);
        }
        _ => {}
    }
}

fn setup(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    #[cfg(target_os = "macos")]
    app.set_activation_policy(tauri::ActivationPolicy::Accessory);

    let handle = app.handle().clone();
    let home = parker_home();
    let engine_bin = resolve_engine_binary(&handle)?;
    let port = pick_port();

    let state = AppState {
        manager: SidecarManager::new(home),
        engine_bin,
        port,
        healthy: AtomicBool::new(false),
        quitting: AtomicBool::new(false),
        onboarding_pending: AtomicBool::new(false),
        restart_strikes: AtomicU32::new(0),
        microphone_transition: MicrophoneTransition::default(),
    };
    state.manager.spawn(&state.engine_spec())?;
    app.manage(state);

    // Tray + menu.
    let status = MenuItem::with_id(app, "status", "Parker — starting…", false, None::<&str>)?;
    let practice = MenuItem::with_id(app, "voice-practice", "Voice Practice", true, None::<&str>)?;
    let dad = MenuItem::with_id(app, "dad-screen", "Open Dad Screen", true, None::<&str>)?;
    let review = MenuItem::with_id(app, "review", "Family Review", true, None::<&str>)?;
    let digest = MenuItem::with_id(app, "digest", "Daily Digest", true, None::<&str>)?;
    let listen = MenuItem::with_id(app, "toggle-listen", "Start Listening", true, None::<&str>)?;
    let setup_item = MenuItem::with_id(app, "setup", "Settings / Setup…", true, None::<&str>)?;
    let autostart_checked = app.autolaunch().is_enabled().unwrap_or(false);
    let autostart = CheckMenuItem::with_id(
        app, "autostart", "Start at Login", true, autostart_checked, None::<&str>,
    )?;
    let quit = MenuItem::with_id(app, "quit", "Quit Parker", true, None::<&str>)?;
    let menu = Menu::with_items(
        app,
        &[
            &status,
            &PredefinedMenuItem::separator(app)?,
            &listen,
            &practice,
            &dad,
            &PredefinedMenuItem::separator(app)?,
            &review,
            &digest,
            &PredefinedMenuItem::separator(app)?,
            &setup_item,
            &autostart,
            &PredefinedMenuItem::separator(app)?,
            &quit,
        ],
    )?;

    let tray = TrayIconBuilder::with_id("parker-tray")
        .icon(Image::from_bytes(ICON_IDLE)?)
        .icon_as_template(true)
        .tooltip("Parker")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(|app, event| on_menu_event(app, event.id().as_ref()))
        .build(app)?;

    app.manage(TrayHandles { tray, status, listen, autostart });

    let boot_handle = handle.clone();
    std::thread::spawn(move || boot_thread(boot_handle));
    let poll_handle = handle.clone();
    std::thread::spawn(move || poll_thread(poll_handle));

    Ok(())
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|_app, _args, _cwd| {
            // Second launch: the tray is already up; nothing to do.
        }))
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        .setup(setup)
        .build(tauri::generate_context!())
        .expect("error while building Parker")
        .run(|app, event| match event {
            RunEvent::ExitRequested { code, api, .. } => {
                // A closing window must not take the tray app down.
                let state = app.state::<AppState>();
                if code.is_none() && !state.quitting.load(Ordering::SeqCst) {
                    api.prevent_exit();
                }
            }
            RunEvent::Exit => {
                let state = app.state::<AppState>();
                state.quitting.store(true, Ordering::SeqCst);
                state.manager.kill_all();
            }
            _ => {}
        });
}

#[cfg(test)]
mod tests {
    use std::sync::{mpsc, Arc};
    use std::thread;
    use std::time::Duration;

    use super::{
        active_talk_state, first_session_lease_action, first_session_start_decision,
        FirstSessionLeaseAction, FirstSessionStartDecision, MicrophoneTransition,
    };

    #[test]
    fn voice_practice_blocks_first_session_before_talk_spawn() {
        assert_eq!(
            first_session_start_decision(true, false),
            FirstSessionStartDecision::BlockedByPractice
        );
    }

    #[test]
    fn existing_talk_process_is_reused_instead_of_duplicated() {
        assert_eq!(
            first_session_start_decision(false, true),
            FirstSessionStartDecision::ReuseTalk
        );
        assert_eq!(
            first_session_start_decision(false, false),
            FirstSessionStartDecision::SpawnTalk
        );
    }

    #[test]
    fn only_observable_capture_states_count_as_listening() {
        assert!(!active_talk_state("idle"));
        assert!(!active_talk_state("starting"));
        assert!(!active_talk_state("error"));
        assert!(active_talk_state("listening"));
        assert!(active_talk_state("processing"));
        assert!(active_talk_state("speaking"));
    }

    #[test]
    fn cancelled_lease_before_spawn_never_owns_talk_or_screen() {
        let cancelled = serde_json::json!({"request_id": 7, "state": "cancel_requested"});
        assert_eq!(
            first_session_lease_action(Some(&cancelled), 7, None, false),
            FirstSessionLeaseAction::Revoke {
                owned_talk_pid: None,
                close_dad_screen: false,
            }
        );
    }

    #[test]
    fn cancelled_request_owned_spawn_is_killed_before_screen_open() {
        let cancelled = serde_json::json!({"request_id": 7, "state": "cancel_requested"});
        assert_eq!(
            first_session_lease_action(Some(&cancelled), 7, Some(41), false),
            FirstSessionLeaseAction::Revoke {
                owned_talk_pid: Some(41),
                close_dad_screen: false,
            }
        );
    }

    #[test]
    fn cancelled_request_owned_screen_is_closed_before_listening_ack() {
        let cancelled = serde_json::json!({"request_id": 7, "state": "cancel_requested"});
        assert_eq!(
            first_session_lease_action(Some(&cancelled), 7, Some(41), true),
            FirstSessionLeaseAction::Revoke {
                owned_talk_pid: Some(41),
                close_dad_screen: true,
            }
        );
    }

    #[test]
    fn cancelled_reused_talk_process_is_not_killed() {
        let starting = serde_json::json!({"request_id": 7, "state": "starting"});
        assert_eq!(
            first_session_lease_action(Some(&starting), 7, None, false),
            FirstSessionLeaseAction::Continue
        );

        let cancelled = serde_json::json!({"request_id": 7, "state": "cancel_requested"});
        assert_eq!(
            first_session_lease_action(Some(&cancelled), 7, None, false),
            FirstSessionLeaseAction::Revoke {
                owned_talk_pid: None,
                close_dad_screen: false,
            }
        );
    }

    #[test]
    fn stale_or_unavailable_lease_fails_closed_for_owned_resources() {
        let wrong_request = serde_json::json!({"request_id": 8, "state": "starting"});
        let expected = FirstSessionLeaseAction::Revoke {
            owned_talk_pid: Some(41),
            close_dad_screen: true,
        };
        assert_eq!(
            first_session_lease_action(Some(&wrong_request), 7, Some(41), true),
            expected
        );
        assert_eq!(first_session_lease_action(None, 7, Some(41), true), expected);
    }

    #[test]
    fn microphone_owner_transitions_are_serialized() {
        let transition = Arc::new(MicrophoneTransition::default());
        let (first_entered_tx, first_entered_rx) = mpsc::channel();
        let (release_first_tx, release_first_rx) = mpsc::channel();
        let first_gate = Arc::clone(&transition);
        let first = thread::spawn(move || {
            let _guard = first_gate.enter();
            first_entered_tx.send(()).unwrap();
            release_first_rx.recv().unwrap();
        });
        first_entered_rx.recv_timeout(Duration::from_secs(1)).unwrap();

        let (second_entered_tx, second_entered_rx) = mpsc::channel();
        let second_gate = Arc::clone(&transition);
        let second = thread::spawn(move || {
            let _guard = second_gate.enter();
            second_entered_tx.send(()).unwrap();
        });
        assert!(second_entered_rx
            .recv_timeout(Duration::from_millis(50))
            .is_err());

        release_first_tx.send(()).unwrap();
        second_entered_rx.recv_timeout(Duration::from_secs(1)).unwrap();
        first.join().unwrap();
        second.join().unwrap();
    }
}
