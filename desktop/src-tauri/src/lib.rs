//! Parker.app — the menu-bar shell around the Python engine sidecar.
//!
//! The shell has no policy logic, no DB access, no send paths
//! (docs/desktop-architecture.md, "What the shell never does"). It:
//! spawns the bundled engine on a free port, waits for /health, opens
//! the onboarding wizard on first run, restarts a crashed engine with
//! backoff, mirrors the voice-loop state in the tray icon, opens the
//! engine's own pages (dad screen / review / digest / setup) as
//! windows, and toggles the talk-loop sidecar.

mod sidecar;

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::time::Duration;

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
const RESTART_BACKOFF_SECS: [u64; 5] = [1, 2, 4, 8, 15]; // MacClaw's curve

const ICON_IDLE: &[u8] = include_bytes!("../icons/tray-idle.png");
const ICON_LISTENING: &[u8] = include_bytes!("../icons/tray-listening.png");
const ICON_SPEAKING: &[u8] = include_bytes!("../icons/tray-speaking.png");

pub struct AppState {
    manager: SidecarManager,
    engine_bin: PathBuf,
    port: u16,
    healthy: AtomicBool,
    quitting: AtomicBool,
    onboarding_pending: AtomicBool,
    restart_strikes: AtomicU32,
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

fn open_engine_window(app: &AppHandle, label: &str, path: &str, title: &str, fullscreen: bool) {
    if let Some(existing) = app.get_webview_window(label) {
        let _ = existing.show();
        let _ = existing.set_focus();
        return;
    }
    let state = app.state::<AppState>();
    let url = match format!("{}{}", state.base_url(), path).parse() {
        Ok(url) => url,
        Err(_) => return,
    };
    let mut builder = WebviewWindowBuilder::new(app, label, WebviewUrl::External(url)).title(title);
    if fullscreen {
        builder = builder.fullscreen(true);
    } else {
        builder = builder.inner_size(1100.0, 800.0);
    }
    if let Err(err) = builder.build() {
        eprintln!("failed to open window {label}: {err}");
    }
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
                open_engine_window(&handle, "setup", "/setup/ui", "Set up Parker", false);
            });
        }
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
        let talk_running = state.manager.is_running(TALK);
        set_listen_label(&app, talk_running);

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
        "dad-screen" => open_engine_window(app, "dad-screen", "/parker/screen", "Parker — Dad Screen", true),
        "review" => open_engine_window(app, "review", "/parker/review/ui", "Parker — Family Review", false),
        "digest" => open_engine_window(app, "digest", "/parker/digest", "Parker — Family Digest", false),
        "setup" => open_engine_window(app, "setup", "/setup/ui", "Parker — Settings", false),
        "toggle-listen" => {
            let state = app.state::<AppState>();
            if state.manager.is_running(TALK) {
                state.manager.kill(TALK);
                set_listen_label(app, false);
                set_tray_state(app, "idle");
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
    };
    state.manager.spawn(&state.engine_spec())?;
    app.manage(state);

    // Tray + menu.
    let status = MenuItem::with_id(app, "status", "Parker — starting…", false, None::<&str>)?;
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
