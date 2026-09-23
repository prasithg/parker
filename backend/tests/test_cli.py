"""The `parker` CLI: version, doctor, serve preflight, watchdog, onboard.

Everything that talks to real hardware (mic, say, network) is faked —
these tests pin the plumbing: exit codes the shell can tell apart, the
orphan watchdog firing, doctor surviving a crashing check, and terminal
onboarding writing the same config.json the wizard does.
"""

import json
import socket
import threading

import pytest

from app import __version__, cli, doctor, paths
from app.config import settings
from app.doctor import Check


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path))
    return tmp_path


@pytest.fixture
def restore_settings():
    snapshot = settings.model_dump()
    yield settings
    for key, value in snapshot.items():
        setattr(settings, key, value)


def test_version_prints_the_engine_version(capsys):
    assert cli.main(["version"]) == 0
    assert __version__ in capsys.readouterr().out


def test_selftest_runs_one_engine_turn_in_memory(capsys):
    """The bundle smoke check: capture + stage + refusal, no real DB touched."""

    assert cli.main(["selftest"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert report["staged_actions"] == 1
    assert [t["kind"] for t in report["turns"]] == ["captured", "refused"]


# --- serve preflight -------------------------------------------------------


def _occupied_port() -> tuple[socket.socket, int]:
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    return holder, holder.getsockname()[1]


def test_preflight_free_port_is_none():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    assert cli._preflight_port("127.0.0.1", port) is None


def test_preflight_busy_port_not_parker(capsys):
    holder, port = _occupied_port()
    try:
        code = cli._preflight_port("127.0.0.1", port)
    finally:
        holder.close()
    assert code == cli.EXIT_PORT_BUSY_OTHER
    machine_line = json.loads(capsys.readouterr().out.strip())
    assert machine_line == {"error": "port_in_use", "occupied_by": "other", "port": port}


def test_preflight_busy_port_is_another_parker(monkeypatch, capsys):
    holder, port = _occupied_port()
    monkeypatch.setattr(doctor, "_is_parker_health", lambda host, p: True)
    try:
        code = cli._preflight_port("127.0.0.1", port)
    finally:
        holder.close()
    assert code == cli.EXIT_PORT_BUSY_PARKER
    assert json.loads(capsys.readouterr().out.strip())["occupied_by"] == "parker"


# --- orphan watchdog --------------------------------------------------------


def test_watchdog_fires_when_parent_disappears():
    orphaned = threading.Event()
    cli.start_parent_watchdog(
        4242,
        poll_seconds=0.01,
        on_orphaned=orphaned.set,
        getppid=lambda: 99999,  # never matches → orphaned immediately
    )
    assert orphaned.wait(timeout=2)


def test_watchdog_stays_quiet_while_parent_lives():
    orphaned = threading.Event()
    cli.start_parent_watchdog(
        4242, poll_seconds=0.01, on_orphaned=orphaned.set, getppid=lambda: 4242
    )
    assert not orphaned.wait(timeout=0.1)


# --- doctor ------------------------------------------------------------------


def test_run_checks_contains_a_crashing_check():
    def fine():
        return Check("fine", True, "all good")

    def check_explodes():
        raise RuntimeError("boom")

    report = doctor.run_checks(checks=[fine, check_explodes])
    assert report["ok"] is False
    names = {c["name"]: c for c in report["checks"]}
    assert names["fine"]["ok"] is True
    assert names["explodes"]["ok"] is False
    assert "boom" in names["explodes"]["detail"]


def test_check_parker_home_and_database(home):
    assert doctor.check_parker_home().ok is True
    db_check = doctor.check_database()
    assert db_check.ok is True
    assert str(home / "parker.db") in db_check.detail


def test_check_model_states(monkeypatch):
    monkeypatch.setattr(paths, "whisper_model_location", lambda size: "missing")
    failing = doctor.check_model()
    assert failing.ok is False
    assert "download-model" in failing.detail
    monkeypatch.setattr(paths, "whisper_model_location", lambda size: "hf_cache")
    assert doctor.check_model().ok is True


def test_check_port_free_and_busy(monkeypatch):
    holder, port = _occupied_port()
    try:
        busy = doctor.check_port(port)
    finally:
        holder.close()
    assert busy.ok is False

    monkeypatch.setattr(doctor, "_is_parker_health", lambda host, p: True)
    holder, port = _occupied_port()
    try:
        parker_running = doctor.check_port(port)
    finally:
        holder.close()
    assert parker_running.ok is True
    assert "already serving" in parker_running.detail


def test_cli_doctor_json_and_exit_codes(monkeypatch, capsys):
    healthy = {"ok": True, "version": __version__, "parker_home": "x", "checks": []}
    monkeypatch.setattr(doctor, "run_checks", lambda **kw: healthy)
    assert cli.main(["doctor", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True

    sick = {**healthy, "ok": False}
    monkeypatch.setattr(doctor, "run_checks", lambda **kw: sick)
    assert cli.main(["doctor"]) == 1


# --- onboard ------------------------------------------------------------------


def test_terminal_onboarding_writes_config(home, restore_settings):
    from app.onboard import run_terminal_onboarding
    from app.parker.family_config import needs_onboarding, read_family_config

    answers = iter(
        [
            "Ravi",            # patient name
            "Sarah, Michael",  # contacts
            "physio, bridge",  # lexicon
            "wake",            # explicit living-room address mode
            "Parker!",         # wake name (sanitized on write)
            "",                # voice (default)
            "y",               # repair-event capture consent
        ]
    )
    printed: list[str] = []
    code = run_terminal_onboarding(
        input_fn=lambda prompt: next(answers), print_fn=printed.append
    )

    assert code == 0
    config = read_family_config()
    assert config["patient_name"] == "Ravi"
    assert config["parker_family_contacts"] == "Sarah, Michael"
    assert config["parker_address_mode"] == "wake"
    assert config["parker_wake_name"] == "parker"
    assert config["repair_event_capture_consented"] is True
    assert config["onboarding_completed"] is True
    assert needs_onboarding() is False
    assert settings.patient_name == "Ravi"


def test_terminal_onboarding_defaults_keep_consent_off(home, restore_settings):
    from app.onboard import run_terminal_onboarding
    from app.parker.family_config import read_family_config

    code = run_terminal_onboarding(input_fn=lambda prompt: "", print_fn=lambda s: None)
    assert code == 0
    config = read_family_config()
    assert config["patient_name"] == "Dad"
    assert config["parker_address_mode"] == "wake"
    assert config["parker_wake_name"] == "parker"
    assert config["repair_event_capture_consented"] is False  # opt-IN stays off


# --- build identity (F8: a packaged binary must say which source built it) ---


def test_version_plain_output_keeps_the_sidecar_smoke_contract(capsys):
    assert cli.main(["version"]) == 0
    first_line = capsys.readouterr().out.splitlines()[0]
    assert first_line.startswith(f"parker {__version__}")


def _is_sha(value: str) -> bool:
    bare = value[: -len("-dirty")] if value.endswith("-dirty") else value
    return bare == "unknown" or (len(bare) == 40 and all(c in "0123456789abcdef" for c in bare))


def test_version_json_carries_git_sha(capsys):
    assert cli.main(["version", "--json"]) == 0
    identity = json.loads(capsys.readouterr().out)
    assert identity["version"] == __version__
    assert _is_sha(identity["git_sha"]), identity
    assert identity["frozen"] is False


def test_health_reports_version_and_git_sha():
    from fastapi.testclient import TestClient

    from app.main import app

    body = TestClient(app).get("/health").json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert _is_sha(body["git_sha"]), body


def test_git_sha_prefers_build_info_when_frozen(monkeypatch):
    import subprocess
    import sys
    import types

    from app import version

    def never(*args, **kwargs):
        raise AssertionError("a frozen binary must not shell out to git")

    monkeypatch.setattr(subprocess, "run", never)
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    stamped = types.ModuleType("app._build_info")
    stamped.GIT_SHA = "abcdef0123456789abcdef0123456789abcdef01-dirty"
    stamped.BUILT_AT = "2026-09-02T00:00:00Z"
    monkeypatch.setitem(sys.modules, "app._build_info", stamped)
    assert version.git_sha() == stamped.GIT_SHA

    monkeypatch.setitem(sys.modules, "app._build_info", None)  # import fails → no build info
    assert version.git_sha() == "unknown"


def test_git_sha_in_a_checkout_comes_from_git_and_marks_dirty_trees(monkeypatch):
    import subprocess
    import sys

    from app import version

    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setitem(sys.modules, "app._build_info", None)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0123456789abcdef0123456789abcdef01234567\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=" M backend/app/cli.py\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert version.git_sha() == "0123456789abcdef0123456789abcdef01234567-dirty"
    assert all(kwargs_free[0] == "git" for kwargs_free in calls)

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("git")))
    assert version.git_sha() == "unknown"


def test_write_build_info_generates_the_module_make_sidecar_bundles(tmp_path, monkeypatch):
    import importlib.util

    from app import version

    monkeypatch.setattr(version, "git_sha", lambda: "0123456789abcdef0123456789abcdef01234567")
    target = tmp_path / "_build_info.py"
    assert version.write_build_info(target) == target
    spec = importlib.util.spec_from_file_location("stamped", target)
    stamped = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stamped)
    assert stamped.GIT_SHA == "0123456789abcdef0123456789abcdef01234567"
    assert stamped.BUILT_AT.endswith("Z") and "T" in stamped.BUILT_AT
