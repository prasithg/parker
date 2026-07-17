"""Inactive subprocess/ownership controls for the scheduled-wrapper contract."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from benchmark.scheduled_wrapper_harness_v0 import (
    CLAIM_BOUNDARY,
    MAX_WORKER_OUTPUT_BYTES,
    WORKER_TIMEOUT_SECONDS,
    create_protected_layout,
    inspect_protected_layout,
    run_bounded_worker,
    run_inactive_harness,
    scrubbed_worker_environment,
)

REPO = Path(__file__).resolve().parents[2]
HARNESS = REPO / "benchmark/scheduled_wrapper_harness_v0.py"


def test_inactive_harness_executes_scrubbed_worker_and_checks_real_layout(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNTHETIC_SHOULD_NOT_REACH_WORKER", "not-delegated")

    result = run_inactive_harness(tmp_path).as_dict()

    assert result["gate"] == {"passed": True, "failure_count": 0}
    assert result["metrics"] == {
        "checks": 9,
        "passed_checks": 9,
        "worker_runs": 1,
        "live_activations": 0,
    }
    assert result["assertions"] == {
        "protected_layout_regular_nofollow": True,
        "protected_layout_owned": True,
        "protected_layout_modes": True,
        "worker_environment_scrubbed": True,
        "worker_descriptors_closed": True,
        "worker_identity_unprivileged": True,
        "protected_material_not_delegated": True,
        "worker_completed_bounded": True,
        "live_activation_absent": True,
    }
    assert result["observations"]["worker"]["environment_keys"] == sorted(
        scrubbed_worker_environment()
    )
    assert result["observations"]["worker"]["unexpected_fds"] == []
    assert result["observations"]["worker"]["identity_unprivileged"] is True
    assert result["observations"]["worker"]["failure_reason"] is None
    assert result["observations"]["worker"]["max_output_bytes"] == MAX_WORKER_OUTPUT_BYTES
    assert result["observations"]["worker"]["timeout_seconds"] == WORKER_TIMEOUT_SECONDS
    assert result["claim_boundary"] == CLAIM_BOUNDARY


def test_protected_layout_rejects_symlinked_pending_state(tmp_path):
    layout = create_protected_layout(tmp_path)
    pending = layout["pending_state"]
    target = pending.with_name("pending-target.json")
    pending.rename(target)
    pending.symlink_to(target.name)

    inspection = inspect_protected_layout(layout)

    assert inspection["regular_nofollow"] is False
    assert inspection["owned"] is False
    assert inspection["modes"] is False


def test_protected_layout_rejects_group_readable_ack_state(tmp_path):
    layout = create_protected_layout(tmp_path)
    layout["ack_state"].chmod(0o640)

    inspection = inspect_protected_layout(layout)

    assert inspection["regular_nofollow"] is True
    assert inspection["owned"] is True
    assert inspection["modes"] is False


def test_worker_launch_environment_is_exact_and_has_no_home_or_secret(monkeypatch):
    monkeypatch.setenv("HOME", "/synthetic/private-home")
    monkeypatch.setenv("SYNTHETIC_SECRET", "must-not-cross")

    environment = scrubbed_worker_environment()

    assert environment == {
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "PYTHONNOUSERSITE": "1",
        "__CF_USER_TEXT_ENCODING": "0x0:0x0:0x0",
    }
    assert "HOME" not in environment
    assert "SYNTHETIC_SECRET" not in environment


def test_hanging_worker_is_killed_reaped_and_bounded(tmp_path):
    started = time.monotonic()
    observation = run_bounded_worker(
        [sys.executable, "-I", "-c", "import time; time.sleep(60)"],
        cwd=tmp_path,
        environment=scrubbed_worker_environment(),
        timeout_seconds=0.05,
        max_output_bytes=1024,
    )
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    assert observation.failure_reason == "timeout"
    assert observation.returncode is not None
    with pytest.raises(ProcessLookupError):
        os.kill(observation.pid, 0)


def test_worker_output_flood_fails_at_incremental_cap(tmp_path):
    observation = run_bounded_worker(
        [sys.executable, "-I", "-c", "import os; os.write(1, b'x' * 65536)"],
        cwd=tmp_path,
        environment=scrubbed_worker_environment(),
        timeout_seconds=1.0,
        max_output_bytes=1024,
    )

    assert observation.failure_reason == "output_limit"
    assert observation.observed_bytes == 1025
    assert len(observation.stdout) <= 1024


def test_harness_receipt_is_bounded_and_contains_no_operational_material(tmp_path):
    payload = run_inactive_harness(tmp_path).as_dict()
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))

    assert len(serialized.encode("utf-8")) < 16 * 1024
    assert str(tmp_path) not in serialized
    assert "http://" not in serialized
    assert "https://" not in serialized
    assert ".env" not in serialized
    assert "token" not in serialized.lower()
    assert "nonce" not in serialized.lower()
    assert "live_activations" in payload["metrics"]
    assert payload["metrics"]["live_activations"] == 0


def test_harness_cli_reports_machine_gate_without_live_activation():
    completed = subprocess.run(
        [sys.executable, str(HARNESS), "--json"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["gate"] == {"passed": True, "failure_count": 0}
    assert payload["metrics"]["passed_checks"] == 9
    assert payload["metrics"]["live_activations"] == 0
    assert payload["observations"]["activation"] == {
        "scheduler_configuration_read": False,
        "scheduler_configuration_changed": False,
        "production_verifier_called": False,
        "credential_materialized": False,
        "separate_os_identity_enforced": False,
    }
