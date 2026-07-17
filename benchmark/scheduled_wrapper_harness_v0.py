"""Inactive integration harness for Parker's scheduled-wrapper trust boundary.

The harness exercises one real subprocess and real filesystem metadata inside an
OS temporary directory. It never imports scheduler configuration, reads a live
credential, mints an envelope, calls the production verifier, or writes outside
its caller-provided scratch directory.
"""

from __future__ import annotations

import argparse
import json
import os
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

WORKER_TIMEOUT_SECONDS = 1.0
MAX_WORKER_OUTPUT_BYTES = 16 * 1024
MAX_PUBLIC_RECEIPT_BYTES = 16 * 1024
CLAIM_BOUNDARY = (
    "Inactive synthetic subprocess/ownership evidence only; no live scheduler "
    "activation or genuine scheduled event."
)
_EXPECTED_ENVIRONMENT = {
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
    "PYTHONNOUSERSITE": "1",
    # macOS otherwise synthesizes this variable from the launching account.
    # Pinning a neutral value keeps the child environment exact and avoids
    # reflecting an account-derived value into the probe.
    "__CF_USER_TEXT_ENCODING": "0x0:0x0:0x0",
}
_PROTECTED_MODES = {
    "protected_root": 0o700,
    "pending_state": 0o600,
    "nonce_ledger": 0o700,
    "ack_state": 0o600,
}
_DIRECTORY_RESOURCES = {"protected_root", "nonce_ledger"}
_WORKER_PROBE = """import json, os
unexpected = []
for descriptor in range(3, 256):
    try:
        os.fstat(descriptor)
    except OSError:
        continue
    unexpected.append(descriptor)
print(json.dumps({
    "schema_version": 1,
    "status": "completed",
    "effective_uid": os.geteuid(),
    "environment_keys": sorted(os.environ),
    "unexpected_fds": unexpected,
}, sort_keys=True, separators=(",", ":")))
"""


@dataclass(frozen=True)
class WorkerObservation:
    pid: int
    returncode: int | None
    stdout: bytes
    observed_bytes: int
    failure_reason: str | None


@dataclass(frozen=True)
class InactiveHarnessResult:
    assertions: dict[str, bool]
    observations: dict[str, Any]
    failures: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        passed = sum(self.assertions.values())
        payload = {
            "eval": "scheduled_wrapper_inactive_harness_v0",
            "gate": {
                "passed": not self.failures,
                "failure_count": len(self.failures),
            },
            "metrics": {
                "checks": len(self.assertions),
                "passed_checks": passed,
                "worker_runs": 1,
                "live_activations": 0,
            },
            "assertions": self.assertions,
            "observations": self.observations,
            "failures": self.failures,
            "claim_boundary": CLAIM_BOUNDARY,
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(serialized) > MAX_PUBLIC_RECEIPT_BYTES:
            raise ValueError("inactive harness receipt exceeds the public output bound")
        return payload


def scrubbed_worker_environment() -> dict[str, str]:
    """Return the exact non-secret environment delegated to the synthetic worker."""

    return dict(_EXPECTED_ENVIRONMENT)


def create_protected_layout(workspace: Path) -> dict[str, Path]:
    """Create synthetic protected wrapper state with production-shaped modes."""

    root = workspace.resolve()
    root.mkdir(parents=True, exist_ok=True)
    protected_root = root / "protected-wrapper-state"
    worker_sandbox = root / "worker-sandbox"
    protected_root.mkdir(mode=0o700)
    protected_root.chmod(0o700)
    worker_sandbox.mkdir(mode=0o700)
    worker_sandbox.chmod(0o700)

    pending_state = protected_root / "pending-state.json"
    ack_state = protected_root / "ack-state.json"
    nonce_ledger = protected_root / "consumed-ledger"
    _create_exclusive_file(pending_state, b'{"schema_version":1,"state":"pending"}\n', 0o600)
    _create_exclusive_file(ack_state, b'{"schema_version":1,"state":"unacknowledged"}\n', 0o600)
    nonce_ledger.mkdir(mode=0o700)
    nonce_ledger.chmod(0o700)
    return {
        "protected_root": protected_root,
        "pending_state": pending_state,
        "nonce_ledger": nonce_ledger,
        "ack_state": ack_state,
        "worker_sandbox": worker_sandbox,
    }


def inspect_protected_layout(layout: Mapping[str, Path]) -> dict[str, bool]:
    """Validate owner, mode, type, and no-follow opening for protected state."""

    expected_names = set(_PROTECTED_MODES) | {"worker_sandbox"}
    if set(layout) != expected_names:
        return {"regular_nofollow": False, "owned": False, "modes": False}

    regular_nofollow = True
    owned = True
    modes = True
    for name, expected_mode in _PROTECTED_MODES.items():
        path = Path(layout[name])
        descriptor = _open_nofollow(path, directory=name in _DIRECTORY_RESOURCES)
        if descriptor is None:
            regular_nofollow = False
            owned = False
            modes = False
            continue
        try:
            metadata = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        correct_type = (
            stat.S_ISDIR(metadata.st_mode)
            if name in _DIRECTORY_RESOURCES
            else stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1
        )
        regular_nofollow = regular_nofollow and correct_type
        owned = owned and metadata.st_uid == os.geteuid()
        modes = modes and stat.S_IMODE(metadata.st_mode) == expected_mode
    return {
        "regular_nofollow": regular_nofollow,
        "owned": owned,
        "modes": modes,
    }


def run_inactive_harness(workspace: Path | None = None) -> InactiveHarnessResult:
    """Execute the inactive worker/layout harness and emit only bounded metadata."""

    if workspace is None:
        with tempfile.TemporaryDirectory(prefix="parker-wrapper-harness-") as scratch:
            return _run_in_workspace(Path(scratch).resolve())
    return _run_in_workspace(Path(workspace).resolve())


def _run_in_workspace(workspace: Path) -> InactiveHarnessResult:
    layout = create_protected_layout(workspace)
    inspection = inspect_protected_layout(layout)
    environment = scrubbed_worker_environment()
    command = [sys.executable, "-I", "-c", _WORKER_PROBE]
    worker_sandbox = layout["worker_sandbox"]

    protected_values = [os.fspath(layout[name]) for name in _PROTECTED_MODES]
    delegated = json.dumps(
        {
            "arguments": command[1:],
            "environment": environment,
            "cwd": os.fspath(worker_sandbox),
        },
        sort_keys=True,
    )
    protected_not_delegated = not any(value in delegated for value in protected_values)

    worker = run_bounded_worker(
        command,
        cwd=worker_sandbox,
        environment=environment,
        timeout_seconds=WORKER_TIMEOUT_SECONDS,
        max_output_bytes=MAX_WORKER_OUTPUT_BYTES,
    )
    worker_payload = _decode_worker_payload(worker.stdout)
    environment_keys = worker_payload.get("environment_keys")
    unexpected_fds = worker_payload.get("unexpected_fds")
    worker_euid = worker_payload.get("effective_uid")
    environment_scrubbed = bool(
        isinstance(environment_keys, list)
        and environment_keys == sorted(environment)
        and set(environment_keys) == set(_EXPECTED_ENVIRONMENT)
    )
    descriptors_closed = unexpected_fds == []
    identity_unprivileged = bool(
        isinstance(worker_euid, int)
        and not isinstance(worker_euid, bool)
        and worker_euid == os.geteuid()
        and worker_euid != 0
    )
    worker_completed = bool(
        worker.failure_reason is None
        and worker.returncode == 0
        and worker_payload.get("schema_version") == 1
        and worker_payload.get("status") == "completed"
        and worker.observed_bytes <= MAX_WORKER_OUTPUT_BYTES
    )

    assertions = {
        "protected_layout_regular_nofollow": inspection["regular_nofollow"],
        "protected_layout_owned": inspection["owned"],
        "protected_layout_modes": inspection["modes"],
        "worker_environment_scrubbed": environment_scrubbed,
        "worker_descriptors_closed": descriptors_closed,
        "worker_identity_unprivileged": identity_unprivileged,
        "protected_material_not_delegated": protected_not_delegated,
        "worker_completed_bounded": worker_completed,
        "live_activation_absent": True,
    }
    observations = {
        "filesystem": {
            "checked_resources": len(_PROTECTED_MODES),
            "expected_owner": "current_wrapper_identity",
            "nofollow_open": inspection["regular_nofollow"],
        },
        "worker": {
            "environment_keys": environment_keys if isinstance(environment_keys, list) else [],
            "unexpected_fds": unexpected_fds if isinstance(unexpected_fds, list) else [],
            "identity_unprivileged": identity_unprivileged,
            "observed_bytes": worker.observed_bytes,
            "max_output_bytes": MAX_WORKER_OUTPUT_BYTES,
            "timeout_seconds": WORKER_TIMEOUT_SECONDS,
            "failure_reason": worker.failure_reason,
        },
        "activation": {
            "scheduler_configuration_read": False,
            "scheduler_configuration_changed": False,
            "production_verifier_called": False,
            "credential_materialized": False,
            "separate_os_identity_enforced": False,
        },
    }
    failures = [name for name, passed in assertions.items() if not passed]
    return InactiveHarnessResult(assertions=assertions, observations=observations, failures=failures)


def run_bounded_worker(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout_seconds: float,
    max_output_bytes: int,
) -> WorkerObservation:
    """Run one worker with incremental combined-output and deadline bounds."""

    if (
        not command
        or timeout_seconds <= 0
        or max_output_bytes <= 0
        or not Path(cwd).is_dir()
    ):
        return WorkerObservation(0, None, b"", 0, "invalid_launch")

    process: subprocess.Popen[bytes] | None = None
    selector: selectors.BaseSelector | None = None
    observed_bytes = 0
    stdout_chunks: list[bytes] = []
    deadline = time.monotonic() + timeout_seconds
    failure_reason: str | None = None
    try:
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            start_new_session=True,
        )
        if process.stdout is None or process.stderr is None:
            _stop_process_group(process)
            return WorkerObservation(process.pid, process.returncode, b"", 0, "process_error")
        selector = selectors.DefaultSelector()
        for stream, name in ((process.stdout, "stdout"), (process.stderr, "stderr")):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream.fileno(), selectors.EVENT_READ, name)

        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure_reason = "timeout"
                break
            events = selector.select(remaining)
            if not events:
                failure_reason = "timeout"
                break
            for key, _ in events:
                read_size = min(64 * 1024, max_output_bytes - observed_bytes + 1)
                if read_size <= 0:
                    failure_reason = "output_limit"
                    break
                chunk = os.read(key.fd, read_size)
                if not chunk:
                    selector.unregister(key.fd)
                    continue
                remaining_capacity = max(0, max_output_bytes - observed_bytes)
                if key.data == "stdout" and remaining_capacity:
                    stdout_chunks.append(chunk[:remaining_capacity])
                observed_bytes += len(chunk)
                if observed_bytes > max_output_bytes:
                    failure_reason = "output_limit"
                    break
            if failure_reason is not None:
                break

        if failure_reason is not None:
            _stop_process_group(process)
        elif process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure_reason = "timeout"
                _stop_process_group(process)
            else:
                try:
                    process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    failure_reason = "timeout"
                    _stop_process_group(process)
        if failure_reason is None and process.returncode != 0:
            failure_reason = "process_error"
        return WorkerObservation(
            process.pid,
            process.returncode,
            b"".join(stdout_chunks),
            observed_bytes,
            failure_reason,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        if process is not None:
            _stop_process_group(process)
            return WorkerObservation(
                process.pid,
                process.returncode,
                b"".join(stdout_chunks),
                observed_bytes,
                "process_error",
            )
        return WorkerObservation(0, None, b"", observed_bytes, "process_error")
    finally:
        if selector is not None:
            selector.close()
        if process is not None:
            for stream in (process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()


def _create_exclusive_file(path: Path, payload: bytes, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, mode)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def _open_nofollow(path: Path, *, directory: bool) -> int | None:
    if not path.is_absolute() or not hasattr(os, "O_NOFOLLOW"):
        return None
    parent_descriptor = _open_directory_chain(path.parent)
    if parent_descriptor is None:
        return None
    flags = os.O_RDONLY | os.O_NOFOLLOW
    if directory:
        if not hasattr(os, "O_DIRECTORY"):
            os.close(parent_descriptor)
            return None
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        return os.open(path.name, flags, dir_fd=parent_descriptor)
    except OSError:
        return None
    finally:
        os.close(parent_descriptor)


def _open_directory_chain(path: Path) -> int | None:
    if not path.is_absolute() or not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        return None
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor: int | None = None
    try:
        descriptor = os.open(path.anchor, flags)
        for component in path.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except OSError:
        if descriptor is not None:
            os.close(descriptor)
        return None


def _decode_worker_payload(payload: bytes) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "status",
        "effective_uid",
        "environment_keys",
        "unexpected_fds",
    }:
        return {}
    return value


def _stop_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=0.2)
    except (OSError, subprocess.TimeoutExpired):
        pass


def format_summary(result: InactiveHarnessResult) -> str:
    payload = result.as_dict()
    metrics = payload["metrics"]
    return "\n".join(
        [
            "Parker scheduled-wrapper inactive harness v0",
            "",
            f"  checks passed:   {metrics['passed_checks']}/{metrics['checks']}",
            f"  worker runs:     {metrics['worker_runs']}",
            f"  live activations: {metrics['live_activations']}",
            f"  gate passed:     {payload['gate']['passed']}",
            "",
            CLAIM_BOUNDARY,
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = run_inactive_harness()
    if args.json:
        print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    else:
        print(format_summary(result))
    raise SystemExit(0 if result.as_dict()["gate"]["passed"] else 1)


if __name__ == "__main__":
    main()
