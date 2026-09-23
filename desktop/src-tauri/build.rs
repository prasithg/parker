//! Stamp the source revision into the shell as `PARKER_GIT_SHA` (read by
//! `lib.rs` as `GIT_SHA`), so the packaged probe can bind a Parker.app to a
//! commit. `<sha>[-dirty]` from git, or `unknown` without git — a tarball
//! build must still build.

use std::path::{Path, PathBuf};
use std::process::Command;

fn git(repo: &Path, args: &[&str]) -> Option<String> {
    let output = Command::new("git").args(args).current_dir(repo).output().ok()?;
    if !output.status.success() {
        return None;
    }
    Some(String::from_utf8_lossy(&output.stdout).trim().to_string())
}

fn git_sha(repo: &Path) -> String {
    let Some(head) = git(repo, &["rev-parse", "HEAD"]).filter(|sha| !sha.is_empty()) else {
        // Loud in the package log: an "unknown" stamp fails the packaged
        // probe's SHA check by design, so say why at build time.
        println!("cargo:warning=PARKER_GIT_SHA=unknown (no git)");
        return "unknown".into();
    };
    match git(repo, &["status", "--porcelain"]) {
        Some(status) if !status.is_empty() => format!("{head}-dirty"),
        _ => head,
    }
}

fn main() {
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
    println!("cargo:rustc-env=PARKER_GIT_SHA={}", git_sha(&repo));
    // Re-stamp when HEAD or the index moves (worktrees keep these under
    // .git/worktrees/<name>/, so ask git where they are).
    for name in ["HEAD", "index"] {
        if let Some(path) = git(&repo, &["rev-parse", "--git-path", name]) {
            println!("cargo:rerun-if-changed={}", repo.join(path).display());
        }
    }
    tauri_build::build()
}
