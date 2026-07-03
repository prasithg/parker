"""``parker doctor`` — is this machine ready to run Parker?

Seven checks, each isolated so one broken subsystem never hides the
rest: PARKER_HOME writable, database writable, microphone present,
``say`` speaks, whisper model downloaded, disk headroom, engine port.
Output is human lines or ``--json`` for the shell/scripts.

The mic check only *enumerates* input devices — enumeration does not
trigger the macOS TCC permission prompt; the onboarding wizard owns that
moment with an actual recording.
"""

from __future__ import annotations

import json
import shutil
import socket
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Any, Callable

from app import __version__, paths
from app.voice.transcribe import DEFAULT_ASR_MODEL

MIN_FREE_BYTES = 2 * 1024**3  # model + DB + logs headroom


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def check_parker_home() -> Check:
    try:
        home = paths.ensure_parker_home()
        probe = home / ".doctor-write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return Check("parker_home", True, str(home))
    except OSError as exc:
        return Check("parker_home", False, f"{paths.parker_home()}: {exc}")


def check_database() -> Check:
    try:
        paths.ensure_parker_home()
        with sqlite3.connect(paths.db_path(), timeout=5) as conn:
            conn.execute("PRAGMA user_version")
        return Check("database", True, f"writable at {paths.db_path()}")
    except (OSError, sqlite3.Error) as exc:
        return Check("database", False, f"{paths.db_path()}: {exc}")


def check_microphone() -> Check:
    try:
        import sounddevice
    except (ImportError, OSError) as exc:
        return Check("microphone", False, f"sounddevice unavailable: {exc}")
    try:
        device = sounddevice.query_devices(kind="input")
    except Exception as exc:  # noqa: BLE001 — PortAudio raises its own hierarchy
        return Check("microphone", False, f"no input device: {exc}")
    name = device.get("name", "unknown") if isinstance(device, dict) else "unknown"
    return Check("microphone", True, f"input device: {name}")


def check_say() -> Check:
    if sys.platform != "darwin":
        return Check("say", False, "macOS `say` not available on this platform")
    if shutil.which("say") is None:
        return Check("say", False, "`say` not found on PATH")
    try:
        subprocess.run(["say", "-v", "?"], capture_output=True, timeout=10, check=True)
        return Check("say", True, "voice output available")
    except (subprocess.SubprocessError, OSError) as exc:
        return Check("say", False, f"`say` failed: {exc}")


def check_model(model_size: str = DEFAULT_ASR_MODEL) -> Check:
    location = paths.whisper_model_location(model_size)
    if location == "parker_models":
        return Check("model", True, f"{model_size} in {paths.models_dir()}")
    if location == "hf_cache":
        return Check("model", True, f"{model_size} in the Hugging Face cache")
    return Check(
        "model", False, f"{model_size} not downloaded — run `parker download-model`"
    )


def check_disk_space() -> Check:
    try:
        target = paths.parker_home()
        while not target.exists() and target != target.parent:
            target = target.parent
        free = shutil.disk_usage(target).free
    except OSError as exc:
        return Check("disk_space", False, str(exc))
    ok = free >= MIN_FREE_BYTES
    return Check(
        "disk_space",
        ok,
        f"{free / 1024**3:.1f} GB free" + ("" if ok else f" (< {MIN_FREE_BYTES / 1024**3:.0f} GB)"),
    )


def check_port(port: int = 8000, host: str = "127.0.0.1") -> Check:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1)
        busy = probe.connect_ex((host, port)) == 0
    if not busy:
        return Check("port", True, f"{host}:{port} free")
    if _is_parker_health(host, port):
        return Check("port", True, f"a Parker engine is already serving on {host}:{port}")
    return Check("port", False, f"{host}:{port} occupied by something that is not Parker")


def _is_parker_health(host: str, port: int) -> bool:
    try:
        import httpx

        response = httpx.get(f"http://{host}:{port}/health", timeout=2)
        return response.status_code == 200 and "patient" in response.json()
    except Exception:  # noqa: BLE001 — anything not-Parker-shaped is "no"
        return False


def run_checks(
    *, port: int = 8000, model_size: str = DEFAULT_ASR_MODEL,
    checks: list[Callable[[], Check]] | None = None,
) -> dict[str, Any]:
    """Run every check; one crashing check reports itself, never aborts."""

    to_run: list[Callable[[], Check]] = checks or [
        check_parker_home,
        check_database,
        check_microphone,
        check_say,
        lambda: check_model(model_size),
        check_disk_space,
        lambda: check_port(port),
    ]
    results: list[Check] = []
    for check in to_run:
        try:
            results.append(check())
        except Exception as exc:  # noqa: BLE001 — doctor must always finish
            name = getattr(check, "__name__", "check").removeprefix("check_")
            results.append(Check(name, False, f"check crashed: {exc}"))
    return {
        "ok": all(check.ok for check in results),
        "version": __version__,
        "parker_home": str(paths.parker_home()),
        "checks": [asdict(check) for check in results],
    }


def render_human(report: dict[str, Any]) -> str:
    lines = [f"parker doctor — v{report['version']} — {report['parker_home']}"]
    for check in report["checks"]:
        mark = "ok " if check["ok"] else "FAIL"
        lines.append(f"  [{mark}] {check['name']}: {check['detail']}")
    lines.append("all clear" if report["ok"] else "problems found — see FAIL lines above")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="parker doctor")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--model", default=DEFAULT_ASR_MODEL)
    args = parser.parse_args(argv)

    report = run_checks(port=args.port, model_size=args.model)
    print(json.dumps(report, indent=2) if args.json else render_human(report))
    return 0 if report["ok"] else 1
