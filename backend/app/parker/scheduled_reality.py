"""Fail-closed provenance gate for Parker scheduled Operations receipts.

The trusted scheduler mints an HMAC envelope before the agent/report process
starts. The report process must not receive the verification key. After the
report finishes, a narrow trusted wrapper calls :func:`verify_scheduled_reality`
with that key and the scheduler envelope. Missing or malformed evidence never
qualifies by default.

This module validates operational provenance only. It does not run Parker,
read audio, contact anyone, or establish product/clinical performance.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import time
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_EVIDENCE_BYTES = 1024 * 1024
MAX_GIT_OBSERVATION_BYTES = 64 * 1024
GIT_OBSERVATION_TIMEOUT_SECONDS = 2.0
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAX_RUN_SEQUENCE = 2**63 - 1
_MAX_STATE_STATUS_LENGTH = 64
_MAX_STATE_ERROR_LENGTH = 1024


def _canonical_envelope_payload(
    *,
    job_id: str,
    scheduled_fire_epoch: float,
    expected_code_sha: str,
    nonce: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id,
        "scheduled_fire_epoch": scheduled_fire_epoch,
        "expected_code_sha": expected_code_sha,
        "nonce": nonce,
    }


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def mint_scheduler_envelope(
    *,
    key: bytes,
    job_id: str,
    scheduled_fire_epoch: float,
    expected_code_sha: str,
    nonce: str,
) -> dict[str, Any]:
    """Mint a scheduler-owned envelope.

    This belongs in the no-agent scheduler wrapper. The key must never be
    passed to the report/agent process or written into an artifact.
    """

    if not key:
        raise ValueError("scheduler key must not be empty")
    if not _IDENTIFIER_RE.fullmatch(job_id):
        raise ValueError("job_id must be a bounded ASCII identifier")
    if not _SHA_RE.fullmatch(expected_code_sha):
        raise ValueError("expected_code_sha must be a 40-character lowercase git SHA")
    if not _IDENTIFIER_RE.fullmatch(nonce):
        raise ValueError("nonce must be a bounded ASCII identifier")
    payload = _canonical_envelope_payload(
        job_id=job_id,
        scheduled_fire_epoch=float(scheduled_fire_epoch),
        expected_code_sha=expected_code_sha,
        nonce=nonce,
    )
    payload["token"] = hmac.new(key, _canonical_bytes(payload), hashlib.sha256).hexdigest()
    return payload


def verify_scheduled_reality(
    *,
    repo_root: str | Path,
    inbox_root: str | Path,
    input_path: str | Path,
    pre_state_path: str | Path,
    post_state_path: str | Path,
    scheduler_envelope_path: str | Path,
    consumed_nonce_root: str | Path,
    expected_job_id: str,
    approved_code_sha: str,
    verification_key: bytes,
    max_fire_skew_seconds: float = 300.0,
    max_input_age_seconds: float = 86_400.0,
    clock_observation_seconds: float = 0.02,
) -> dict[str, Any]:
    """Observe and verify one scheduled run, failing closed on every gap.

    The returned receipt intentionally excludes absolute paths, the HMAC token,
    and the verification key so it can be sanitized for review. The verifier
    observes git state, file metadata/hashes, and both clocks itself; callers do
    not supply those observations.
    """

    wall_start = time.time()
    monotonic_start = time.monotonic()

    repo = Path(repo_root).resolve()
    inbox = _absolute_path(inbox_root)
    candidate = _absolute_path(input_path)
    pre_state_file = _absolute_path(pre_state_path)
    post_state_file = _absolute_path(post_state_path)
    envelope_file = _absolute_path(scheduler_envelope_path)
    nonce_root = _absolute_path(consumed_nonce_root)

    assertions: dict[str, bool] = {
        "git_observation_bounded": False,
        "checkout_clean": False,
        "code_sha_matches_scheduler": False,
        "code_sha_matches_approved": False,
        "scheduler_sha_matches_approved": False,
        "scheduler_envelope_regular_nofollow": False,
        "scheduler_signature_valid": False,
        "scheduler_job_matches": False,
        "scheduled_fire_in_window": False,
        "nonce_ledger_trusted_scope": False,
        "scheduler_nonce_single_use": False,
        "input_regular_nofollow": False,
        "input_is_real_inbound": False,
        "input_recent": False,
        "state_evidence_regular_nofollow": False,
        "state_evidence_schema_valid": False,
        "wall_monotonic_coherent": False,
        "state_delta_matches_input": False,
        "post_status_ok": False,
    }

    current_code_sha, checkout_clean, git_failure_reason, git_observed_bytes = (
        _git_observations(repo)
    )
    assertions["git_observation_bounded"] = git_failure_reason is None
    assertions["checkout_clean"] = checkout_clean

    envelope_evidence = _read_regular_file_nofollow(envelope_file)
    assertions["scheduler_envelope_regular_nofollow"] = envelope_evidence is not None
    envelope = _decode_object(envelope_evidence[0] if envelope_evidence else None)
    scheduler_payload = _verified_envelope_payload(envelope, verification_key)
    assertions["scheduler_signature_valid"] = scheduler_payload is not None
    if scheduler_payload is None:
        scheduler_payload = {}

    expected_code_sha = scheduler_payload.get("expected_code_sha")
    assertions["code_sha_matches_scheduler"] = bool(
        current_code_sha
        and isinstance(expected_code_sha, str)
        and hmac.compare_digest(current_code_sha, expected_code_sha)
    )
    approved_sha_is_valid = bool(
        isinstance(approved_code_sha, str) and _SHA_RE.fullmatch(approved_code_sha)
    )
    assertions["code_sha_matches_approved"] = bool(
        approved_sha_is_valid
        and current_code_sha
        and hmac.compare_digest(current_code_sha, approved_code_sha)
    )
    assertions["scheduler_sha_matches_approved"] = bool(
        approved_sha_is_valid
        and isinstance(expected_code_sha, str)
        and hmac.compare_digest(expected_code_sha, approved_code_sha)
    )
    scheduler_job_id = scheduler_payload.get("job_id")
    assertions["scheduler_job_matches"] = bool(
        isinstance(scheduler_job_id, str)
        and expected_job_id
        and hmac.compare_digest(scheduler_job_id, expected_job_id)
    )

    scheduled_fire = _finite_number(scheduler_payload.get("scheduled_fire_epoch"))
    assertions["scheduled_fire_in_window"] = bool(
        scheduled_fire is not None
        and max_fire_skew_seconds >= 0
        and abs(wall_start - scheduled_fire) <= max_fire_skew_seconds
    )
    nonce = scheduler_payload.get("nonce")
    nonce_root_descriptor = _open_directory_nofollow(nonce_root)
    assertions["nonce_ledger_trusted_scope"] = bool(
        nonce_root_descriptor is not None
        and not _is_relative_to(nonce_root, repo)
        and not _is_relative_to(nonce_root, inbox)
    )
    try:
        if (
            assertions["scheduler_signature_valid"]
            and assertions["scheduler_job_matches"]
            and assertions["scheduled_fire_in_window"]
            and assertions["nonce_ledger_trusted_scope"]
            and nonce_root_descriptor is not None
            and isinstance(scheduler_job_id, str)
            and isinstance(nonce, str)
        ):
            assertions["scheduler_nonce_single_use"] = _claim_scheduler_nonce_once(
                nonce_root_descriptor,
                job_id=scheduler_job_id,
                nonce=nonce,
            )
    finally:
        if nonce_root_descriptor is not None:
            os.close(nonce_root_descriptor)

    input_evidence = _read_regular_file_nofollow(candidate)
    assertions["input_regular_nofollow"] = input_evidence is not None
    input_sha = hashlib.sha256(input_evidence[0]).hexdigest() if input_evidence else None
    candidate_mtime = input_evidence[1].st_mtime if input_evidence else None
    assertions["input_is_real_inbound"] = bool(
        input_evidence
        and input_sha
        and _is_relative_to(candidate, inbox)
        and not _is_relative_to(candidate, repo)
    )
    assertions["input_recent"] = bool(
        candidate_mtime is not None
        and max_input_age_seconds >= 0
        and 0 <= wall_start - candidate_mtime <= max_input_age_seconds
    )

    pre_state_evidence = _read_regular_file_nofollow(pre_state_file)
    post_state_evidence = _read_regular_file_nofollow(post_state_file)
    assertions["state_evidence_regular_nofollow"] = bool(
        pre_state_evidence is not None and post_state_evidence is not None
    )
    pre_state = _decode_object(pre_state_evidence[0] if pre_state_evidence else None)
    post_state = _decode_object(post_state_evidence[0] if post_state_evidence else None)
    state_schema_valid = bool(
        _state_has_bounded_schema(pre_state) and _state_has_bounded_schema(post_state)
    )
    assertions["state_evidence_schema_valid"] = state_schema_valid
    pre_state_sha = (
        hashlib.sha256(pre_state_evidence[0]).hexdigest() if pre_state_evidence else None
    )
    post_state_sha = (
        hashlib.sha256(post_state_evidence[0]).hexdigest() if post_state_evidence else None
    )
    post_status_ok = bool(
        state_schema_valid
        and post_state.get("last_status") == "ok"
        and post_state.get("error") is None
    )
    assertions["post_status_ok"] = post_status_ok
    assertions["state_delta_matches_input"] = bool(
        input_sha
        and pre_state_sha
        and post_state_sha
        and pre_state_sha != post_state_sha
        and state_schema_valid
        and _is_exact_increment(pre_state.get("run_sequence"), post_state.get("run_sequence"))
        and post_state.get("last_input_sha256") == input_sha
        and post_status_ok
    )

    if clock_observation_seconds > 0:
        time.sleep(clock_observation_seconds)
    wall_end = time.time()
    monotonic_end = time.monotonic()
    wall_elapsed = wall_end - wall_start
    monotonic_elapsed = monotonic_end - monotonic_start
    elapsed_tolerance = max(0.005, monotonic_elapsed * 0.25)
    assertions["wall_monotonic_coherent"] = bool(
        monotonic_elapsed >= max(0.0, clock_observation_seconds * 0.8)
        and wall_elapsed >= 0
        and abs(wall_elapsed - monotonic_elapsed) <= elapsed_tolerance
    )
    if scheduled_fire is not None:
        assertions["scheduled_fire_in_window"] = bool(
            assertions["scheduled_fire_in_window"]
            and abs(wall_end - scheduled_fire) <= max_fire_skew_seconds
        )

    failed = [name for name, passed in assertions.items() if not passed]
    complete = not failed
    return {
        "schema_version": SCHEMA_VERSION,
        "verdict": "verified" if complete else "unverified",
        "provenance_complete": complete,
        "assertions": assertions,
        "failed_assertions": failed,
        "observations": {
            "code_sha": current_code_sha,
            "approved_code_sha": approved_code_sha if approved_sha_is_valid else None,
            "git": {
                "bounded": assertions["git_observation_bounded"],
                "timeout_seconds": GIT_OBSERVATION_TIMEOUT_SECONDS,
                "max_output_bytes": MAX_GIT_OBSERVATION_BYTES,
                "observed_bytes": git_observed_bytes,
                "failure_reason": git_failure_reason,
            },
            "scheduler": {
                "job_id": scheduler_job_id if isinstance(scheduler_job_id, str) else None,
                "scheduled_fire_epoch": scheduled_fire,
                "nonce_fingerprint": (
                    hashlib.sha256(nonce.encode("ascii")).hexdigest()
                    if isinstance(nonce, str) and _IDENTIFIER_RE.fullmatch(nonce)
                    else None
                ),
                "token_verified": assertions["scheduler_signature_valid"],
                "nonce_ledger_scope": (
                    "trusted_external"
                    if assertions["nonce_ledger_trusted_scope"]
                    else "unverified"
                ),
                "nonce_claimed": assertions["scheduler_nonce_single_use"],
            },
            "input": {
                "sha256": input_sha,
                "mtime_epoch": candidate_mtime,
                "scope": "allowlisted_external_inbox"
                if assertions["input_is_real_inbound"]
                else "unverified",
            },
            "clock": {
                "wall_start_epoch": wall_start,
                "wall_end_epoch": wall_end,
                "monotonic_elapsed_seconds": monotonic_elapsed,
                "wall_elapsed_seconds": wall_elapsed,
            },
            "state_delta": {
                "pre_sha256": pre_state_sha,
                "post_sha256": post_state_sha,
                "pre_run_sequence": pre_state.get("run_sequence") if state_schema_valid else None,
                "post_run_sequence": post_state.get("run_sequence") if state_schema_valid else None,
                "last_status": post_state.get("last_status") if state_schema_valid else None,
                "error_present": bool(post_state.get("error")) if state_schema_valid else False,
                "last_input_matches": bool(input_sha and post_state.get("last_input_sha256") == input_sha),
            },
        },
        "claim_boundary": (
            "Operational scheduled-run provenance only; not product, ASR, clinical, "
            "patient, or external-action evidence."
        ),
    }


def _claim_scheduler_nonce_once(root_descriptor: int, *, job_id: str, nonce: str) -> bool:
    """Atomically consume one scheduler nonce in an already-open ledger.

    The report/agent process must not be able to write the ledger. The caller
    opens the directory with descriptor-relative no-follow traversal, then this
    function creates the tombstone relative to that same descriptor. ``O_EXCL``
    makes concurrent or sequential reuse fail closed. A write/fsync failure
    leaves the tombstone in place and returns false rather than making the
    signed envelope reusable.
    """

    claim_id = hashlib.sha256(f"{job_id}\0{nonce}".encode("utf-8")).hexdigest()
    claim_name = f"{claim_id}.used"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(claim_name, flags, 0o600, dir_fd=root_descriptor)
    except OSError:
        return False
    try:
        payload = _canonical_bytes(
            {
                "schema_version": SCHEMA_VERSION,
                "claim_id": claim_id,
                "status": "consumed",
            }
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        return False
    return True


def _verified_envelope_payload(envelope: dict[str, Any], key: bytes) -> dict[str, Any] | None:
    required = {
        "schema_version",
        "job_id",
        "scheduled_fire_epoch",
        "expected_code_sha",
        "nonce",
        "token",
    }
    if set(envelope) != required or envelope.get("schema_version") != SCHEMA_VERSION or not key:
        return None
    job_id = envelope.get("job_id")
    fire_epoch = _finite_number(envelope.get("scheduled_fire_epoch"))
    expected_code_sha = envelope.get("expected_code_sha")
    nonce = envelope.get("nonce")
    token = envelope.get("token")
    if not (
        isinstance(job_id, str)
        and _IDENTIFIER_RE.fullmatch(job_id)
        and fire_epoch is not None
        and isinstance(expected_code_sha, str)
        and _SHA_RE.fullmatch(expected_code_sha)
        and isinstance(nonce, str)
        and _IDENTIFIER_RE.fullmatch(nonce)
        and isinstance(token, str)
        and re.fullmatch(r"[0-9a-f]{64}", token)
    ):
        return None
    payload = _canonical_envelope_payload(
        job_id=job_id,
        scheduled_fire_epoch=fire_epoch,
        expected_code_sha=expected_code_sha,
        nonce=nonce,
    )
    expected_token = hmac.new(key, _canonical_bytes(payload), hashlib.sha256).hexdigest()
    return payload if hmac.compare_digest(token, expected_token) else None


def _git_observations(repo: Path) -> tuple[str | None, bool, str | None, int]:
    """Capture checkout identity/status under one deadline and output budget."""

    if GIT_OBSERVATION_TIMEOUT_SECONDS <= 0 or MAX_GIT_OBSERVATION_BYTES <= 0:
        return None, False, "invalid_limits", 0
    deadline = time.monotonic() + GIT_OBSERVATION_TIMEOUT_SECONDS
    sha_result = _run_bounded_git(
        repo,
        ["rev-parse", "HEAD"],
        deadline=deadline,
        max_bytes=MAX_GIT_OBSERVATION_BYTES,
    )
    sha_payload, observed_bytes, failure_reason = sha_result
    if failure_reason is not None:
        return None, False, failure_reason, observed_bytes

    status_result = _run_bounded_git(
        repo,
        ["status", "--porcelain"],
        deadline=deadline,
        max_bytes=MAX_GIT_OBSERVATION_BYTES - observed_bytes,
    )
    status_payload, status_bytes, failure_reason = status_result
    observed_bytes += status_bytes
    if failure_reason is not None:
        return None, False, failure_reason, observed_bytes

    try:
        sha = sha_payload.decode("ascii").strip()
        status = status_payload.decode("utf-8")
    except UnicodeDecodeError:
        return None, False, "invalid_output", observed_bytes
    if not _SHA_RE.fullmatch(sha):
        return None, False, "invalid_output", observed_bytes
    return sha, not status.strip(), None, observed_bytes


def _run_bounded_git(
    repo: Path,
    arguments: list[str],
    *,
    deadline: float,
    max_bytes: int,
) -> tuple[bytes, int, str | None]:
    """Run one git observation with incremental capture and fail-closed bounds.

    Both stdout and stderr count toward the cap. The command starts in its own
    process group so a timeout/output overflow can kill descendants that still
    hold a capture pipe open. Raw stderr is never returned or emitted.
    """

    if max_bytes < 0 or deadline <= time.monotonic():
        return b"", 0, "timeout" if deadline <= time.monotonic() else "output_limit"
    process: subprocess.Popen[bytes] | None = None
    selector: selectors.BaseSelector | None = None
    observed_bytes = 0
    stdout_chunks: list[bytes] = []
    try:
        process = subprocess.Popen(
            ["git", *arguments],
            cwd=repo,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        if process.stdout is None or process.stderr is None:
            _stop_process_group(process)
            return b"", 0, "process_error"
        selector = selectors.DefaultSelector()
        for stream, stream_name in ((process.stdout, "stdout"), (process.stderr, "stderr")):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream.fileno(), selectors.EVENT_READ, stream_name)

        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _stop_process_group(process)
                return b"", observed_bytes, "timeout"
            events = selector.select(remaining)
            if not events:
                _stop_process_group(process)
                return b"", observed_bytes, "timeout"
            for key, _ in events:
                read_size = min(64 * 1024, max_bytes - observed_bytes + 1)
                if read_size <= 0:
                    _stop_process_group(process)
                    return b"", observed_bytes, "output_limit"
                chunk = os.read(key.fd, read_size)
                if not chunk:
                    selector.unregister(key.fd)
                    continue
                observed_bytes += len(chunk)
                if observed_bytes > max_bytes:
                    _stop_process_group(process)
                    return b"", observed_bytes, "output_limit"
                if key.data == "stdout":
                    stdout_chunks.append(chunk)

        remaining = deadline - time.monotonic()
        if process.poll() is None:
            if remaining <= 0:
                _stop_process_group(process)
                return b"", observed_bytes, "timeout"
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                _stop_process_group(process)
                return b"", observed_bytes, "timeout"
        if process.returncode != 0:
            return b"", observed_bytes, "process_error"
        return b"".join(stdout_chunks), observed_bytes, None
    except (OSError, subprocess.SubprocessError, ValueError):
        if process is not None:
            _stop_process_group(process)
        return b"", observed_bytes, "process_error"
    finally:
        if selector is not None:
            selector.close()
        if process is not None:
            for stream in (process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()


def _stop_process_group(process: subprocess.Popen[bytes]) -> None:
    """Kill and reap the bounded command plus descendants without raising."""

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


def _absolute_path(path: str | Path) -> Path:
    """Normalize an absolute path without resolving or hiding symlinks."""

    return Path(os.path.abspath(os.fspath(path)))


def _open_directory_nofollow(path: Path) -> int | None:
    """Open an absolute directory through a no-symlink descriptor chain."""

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


def _read_regular_file_nofollow(
    path: Path,
    *,
    max_bytes: int = MAX_EVIDENCE_BYTES,
) -> tuple[bytes, os.stat_result] | None:
    """Read bounded evidence from one stable regular-file descriptor.

    Every parent component and the final file are opened with ``O_NOFOLLOW``.
    Metadata is captured with ``fstat`` on that same descriptor before and
    after the bounded read, so path replacement cannot redirect a later hash or
    timestamp check to different evidence.
    """

    if max_bytes < 0 or not path.name or not hasattr(os, "O_NOFOLLOW"):
        return None
    parent_descriptor = _open_directory_nofollow(path.parent)
    if parent_descriptor is None:
        return None
    flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        descriptor = os.open(path.name, flags, dir_fd=parent_descriptor)
    except OSError:
        os.close(parent_descriptor)
        return None
    os.close(parent_descriptor)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size < 0
            or before.st_size > max_bytes
        ):
            return None
        chunks: list[bytes] = []
        total = 0
        while True:
            block = os.read(descriptor, min(64 * 1024, max_bytes - total + 1))
            if not block:
                break
            chunks.append(block)
            total += len(block)
            if total > max_bytes:
                return None
        after = os.fstat(descriptor)
        stable_fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
            return None
        payload = b"".join(chunks)
        if len(payload) != before.st_size:
            return None
        return payload, after
    except OSError:
        return None
    finally:
        os.close(descriptor)


def _decode_object(payload: bytes | None) -> dict[str, Any]:
    if payload is None:
        return {}
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _state_has_bounded_schema(value: dict[str, Any]) -> bool:
    """Accept only receipt fields that are safe to validate and reflect."""

    required = {"run_sequence", "last_status", "last_input_sha256", "error"}
    if not required.issubset(value):
        return False
    sequence = value.get("run_sequence")
    status_value = value.get("last_status")
    input_sha = value.get("last_input_sha256")
    error = value.get("error")
    return bool(
        isinstance(sequence, int)
        and not isinstance(sequence, bool)
        and 0 <= sequence <= _MAX_RUN_SEQUENCE
        and isinstance(status_value, str)
        and 0 < len(status_value) <= _MAX_STATE_STATUS_LENGTH
        and isinstance(input_sha, str)
        and _SHA256_RE.fullmatch(input_sha)
        and (
            error is None
            or (isinstance(error, str) and len(error) <= _MAX_STATE_ERROR_LENGTH)
        )
    )


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if result == result and result not in (float("inf"), float("-inf")) else None


def _is_exact_increment(before: Any, after: Any) -> bool:
    return (
        isinstance(before, int)
        and not isinstance(before, bool)
        and isinstance(after, int)
        and not isinstance(after, bool)
        and after == before + 1
    )
