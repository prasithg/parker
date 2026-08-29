//! Generic local-process babysitter for Parker's sidecars.
//!
//! The engine (`parker serve`) is entry one; the talk loop (`parker
//! talk`) is entry two; a future patient-identity OpenClaw gateway is
//! designed to be entry three with zero manager changes — a sidecar is
//! just a spec: program, args, log name (pattern per June AI's
//! multi-process bridge, MIT).
//!
//! Every child gets PARKER_HOME in its environment (one source of
//! truth: the shell's), stdout/stderr appended to
//! `PARKER_HOME/logs/<name>.log` (size-rotated, keep 3), and a
//! `--parent-pid`-style safety net on the engine side so an orphaned
//! child exits even if this manager never got to kill it.

use std::collections::HashMap;
use std::fs::{self, File, OpenOptions};
use std::io;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

const LOG_ROTATE_BYTES: u64 = 5 * 1024 * 1024;
const LOG_KEEP: u32 = 3;

pub struct SidecarSpec {
    pub key: &'static str,
    pub program: PathBuf,
    pub args: Vec<String>,
    pub log_name: &'static str,
}

pub struct SidecarManager {
    home: PathBuf,
    children: Mutex<HashMap<&'static str, Child>>,
}

impl SidecarManager {
    pub fn new(home: PathBuf) -> Self {
        Self {
            home,
            children: Mutex::new(HashMap::new()),
        }
    }

    pub fn home(&self) -> &PathBuf {
        &self.home
    }

    /// Spawn (or replace a dead entry for) `spec`. Returns the pid.
    pub fn spawn(&self, spec: &SidecarSpec) -> io::Result<u32> {
        let mut children = self.children.lock().unwrap();
        if let Some(child) = children.get_mut(spec.key) {
            if child.try_wait()?.is_none() {
                return Ok(child.id()); // already running
            }
            children.remove(spec.key);
        }

        let log_out = self.log_file(spec.log_name)?;
        let log_err = log_out.try_clone()?;
        let child = Command::new(&spec.program)
            .args(&spec.args)
            .env("PARKER_HOME", &self.home)
            .env("PYTHONUNBUFFERED", "1")
            .stdin(Stdio::null())
            .stdout(Stdio::from(log_out))
            .stderr(Stdio::from(log_err))
            .spawn()?;
        let pid = child.id();
        children.insert(spec.key, child);
        Ok(pid)
    }

    /// True while the child exists and has not exited.
    pub fn is_running(&self, key: &'static str) -> bool {
        let mut children = self.children.lock().unwrap();
        match children.get_mut(key) {
            Some(child) => match child.try_wait() {
                Ok(None) => true,
                _ => {
                    children.remove(key);
                    false
                }
            },
            None => false,
        }
    }

    /// Kill `key` only when the registered live child still has `pid`.
    ///
    /// Identity comparison and removal happen under the same manager lock so a
    /// stale request cannot kill a replacement child registered under the key.
    pub fn kill_if_pid(&self, key: &'static str, pid: u32) -> bool {
        let mut children = self.children.lock().unwrap();
        let Some(child) = children.get_mut(key) else {
            return false;
        };
        if child.id() != pid {
            return false;
        }
        if !matches!(child.try_wait(), Ok(None)) {
            children.remove(key);
            return false;
        }
        let mut child = children.remove(key).expect("matching child disappeared");
        let _ = child.kill();
        let _ = child.wait();
        true
    }

    pub fn kill(&self, key: &'static str) {
        let mut children = self.children.lock().unwrap();
        if let Some(mut child) = children.remove(key) {
            let _ = child.kill();
            let _ = child.wait(); // reap — no zombies
        }
    }

    pub fn kill_all(&self) {
        let keys: Vec<&'static str> = self.children.lock().unwrap().keys().copied().collect();
        for key in keys {
            self.kill(key);
        }
    }

    /// Append-mode log handle under PARKER_HOME/logs, size-rotated.
    fn log_file(&self, name: &str) -> io::Result<File> {
        let logs = self.home.join("logs");
        fs::create_dir_all(&logs)?;
        let current = logs.join(format!("{name}.log"));
        if let Ok(meta) = fs::metadata(&current) {
            if meta.len() > LOG_ROTATE_BYTES {
                for i in (1..LOG_KEEP).rev() {
                    let from = logs.join(format!("{name}.log.{i}"));
                    if from.exists() {
                        let _ = fs::rename(&from, logs.join(format!("{name}.log.{}", i + 1)));
                    }
                }
                let _ = fs::rename(&current, logs.join(format!("{name}.log.1")));
            }
        }
        OpenOptions::new().create(true).append(true).open(&current)
    }
}

impl Drop for SidecarManager {
    fn drop(&mut self) {
        self.kill_all();
    }
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU32, Ordering};
    use std::time::{Duration, Instant};

    use super::{SidecarManager, SidecarSpec};

    const TALK: &str = "talk-test";
    static NEXT_HOME: AtomicU32 = AtomicU32::new(1);

    fn manager() -> SidecarManager {
        let serial = NEXT_HOME.fetch_add(1, Ordering::Relaxed);
        SidecarManager::new(std::env::temp_dir().join(format!(
            "parker-sidecar-pid-test-{}-{serial}",
            std::process::id()
        )))
    }

    fn sleep_spec(seconds: u32) -> SidecarSpec {
        SidecarSpec {
            key: TALK,
            program: PathBuf::from("/bin/sleep"),
            args: vec![seconds.to_string()],
            log_name: TALK,
        }
    }

    #[test]
    fn kill_if_pid_kills_the_request_owned_process() {
        let manager = manager();
        let owned_pid = manager.spawn(&sleep_spec(60)).unwrap();

        assert!(manager.kill_if_pid(TALK, owned_pid));
        assert!(!manager.is_running(TALK));
    }

    #[test]
    fn kill_if_pid_preserves_a_replacement_process() {
        let manager = manager();
        let owned_pid = manager.spawn(&sleep_spec(60)).unwrap();
        manager.kill(TALK);
        let replacement_pid = manager.spawn(&sleep_spec(60)).unwrap();
        assert_ne!(owned_pid, replacement_pid);

        assert!(!manager.kill_if_pid(TALK, owned_pid));
        assert!(manager.is_running(TALK));
    }

    #[test]
    fn reused_preexisting_process_has_no_owned_pid_and_survives_cleanup() {
        let manager = manager();
        manager.spawn(&sleep_spec(60)).unwrap();
        let owned_pid = if manager.is_running(TALK) {
            None
        } else {
            Some(manager.spawn(&sleep_spec(60)).unwrap())
        };

        if let Some(pid) = owned_pid {
            manager.kill_if_pid(TALK, pid);
        }
        assert_eq!(owned_pid, None);
        assert!(manager.is_running(TALK));
    }

    #[test]
    fn kill_if_pid_is_a_safe_noop_for_missing_process() {
        let manager = manager();

        assert!(!manager.kill_if_pid(TALK, u32::MAX));
        assert!(!manager.is_running(TALK));
    }

    #[test]
    fn kill_if_pid_is_a_safe_noop_after_owned_process_exits() {
        let manager = manager();
        let owned_pid = manager.spawn(&sleep_spec(0)).unwrap();
        let deadline = Instant::now() + Duration::from_secs(1);
        while manager.is_running(TALK) && Instant::now() < deadline {
            std::thread::yield_now();
        }
        assert!(!manager.is_running(TALK));

        assert!(!manager.kill_if_pid(TALK, owned_pid));
    }
}
