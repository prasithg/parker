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
import subprocess
import time
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


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
    if not job_id.strip():
        raise ValueError("job_id must not be empty")
    if not _SHA_RE.fullmatch(expected_code_sha):
        raise ValueError("expected_code_sha must be a 40-character lowercase git SHA")
    if not nonce.strip():
        raise ValueError("nonce must not be empty")
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
    inbox = Path(inbox_root).resolve()
    candidate = Path(input_path).resolve()
    pre_state_file = Path(pre_state_path).resolve()
    post_state_file = Path(post_state_path).resolve()
    envelope_file = Path(scheduler_envelope_path).resolve()
    nonce_root = Path(consumed_nonce_root).resolve()

    assertions: dict[str, bool] = {
        "checkout_clean": False,
        "code_sha_matches_scheduler": False,
        "code_sha_matches_approved": False,
        "scheduler_sha_matches_approved": False,
        "scheduler_signature_valid": False,
        "scheduler_job_matches": False,
        "scheduled_fire_in_window": False,
        "nonce_ledger_trusted_scope": False,
        "scheduler_nonce_single_use": False,
        "input_is_real_inbound": False,
        "input_recent": False,
        "wall_monotonic_coherent": False,
        "state_delta_matches_input": False,
        "post_status_ok": False,
    }

    current_code_sha, checkout_clean = _git_observations(repo)
    assertions["checkout_clean"] = checkout_clean

    envelope = _load_object(envelope_file)
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
    assertions["nonce_ledger_trusted_scope"] = bool(
        nonce_root.is_dir()
        and not _is_relative_to(nonce_root, repo)
        and not _is_relative_to(nonce_root, inbox)
    )
    if (
        assertions["scheduler_signature_valid"]
        and assertions["scheduler_job_matches"]
        and assertions["scheduled_fire_in_window"]
        and assertions["nonce_ledger_trusted_scope"]
        and isinstance(scheduler_job_id, str)
        and isinstance(nonce, str)
    ):
        assertions["scheduler_nonce_single_use"] = _claim_scheduler_nonce_once(
            nonce_root,
            job_id=scheduler_job_id,
            nonce=nonce,
        )

    input_sha = _sha256_file(candidate)
    candidate_mtime = _mtime(candidate)
    assertions["input_is_real_inbound"] = bool(
        input_sha
        and _is_relative_to(candidate, inbox)
        and not _is_relative_to(candidate, repo)
        and candidate.is_file()
    )
    assertions["input_recent"] = bool(
        candidate_mtime is not None
        and max_input_age_seconds >= 0
        and 0 <= wall_start - candidate_mtime <= max_input_age_seconds
    )

    pre_state = _load_object(pre_state_file)
    post_state = _load_object(post_state_file)
    pre_state_sha = _sha256_file(pre_state_file)
    post_state_sha = _sha256_file(post_state_file)
    post_status_ok = bool(post_state.get("last_status") == "ok" and not post_state.get("error"))
    assertions["post_status_ok"] = post_status_ok
    assertions["state_delta_matches_input"] = bool(
        input_sha
        and pre_state_sha
        and post_state_sha
        and pre_state_sha != post_state_sha
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
            "scheduler": {
                "job_id": scheduler_job_id if isinstance(scheduler_job_id, str) else None,
                "scheduled_fire_epoch": scheduled_fire,
                "nonce": nonce,
                "token_verified": assertions["scheduler_signature_valid"],
                "nonce_ledger_scope": (
                    "trusted_external"
                    if assertions["nonce_ledger_trusted_scope"]
                    else "unverified"
                ),
                "nonce_claimed": assertions["scheduler_nonce_single_use"],
            },
            "input": {
                "name": candidate.name,
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
                "pre_run_sequence": pre_state.get("run_sequence"),
                "post_run_sequence": post_state.get("run_sequence"),
                "last_status": post_state.get("last_status"),
                "error_present": bool(post_state.get("error")),
                "last_input_matches": bool(input_sha and post_state.get("last_input_sha256") == input_sha),
            },
        },
        "claim_boundary": (
            "Operational scheduled-run provenance only; not product, ASR, clinical, "
            "patient, or external-action evidence."
        ),
    }


def _claim_scheduler_nonce_once(root: Path, *, job_id: str, nonce: str) -> bool:
    """Atomically consume one scheduler nonce in the trusted wrapper ledger.

    The report/agent process must not be able to write ``root``. The verifier
    only accepts an existing directory outside both the repository and inbound
    evidence tree; deployment ACL/ownership remains a wrapper prerequisite.
    ``O_EXCL`` makes concurrent or sequential reuse fail closed. A write/fsync
    failure leaves the tombstone in place and returns false rather than making
    the signed envelope reusable.
    """

    claim_id = hashlib.sha256(f"{job_id}\0{nonce}".encode("utf-8")).hexdigest()
    claim_path = root / f"{claim_id}.used"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        descriptor = os.open(claim_path, flags, 0o600)
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
        and job_id
        and fire_epoch is not None
        and isinstance(expected_code_sha, str)
        and _SHA_RE.fullmatch(expected_code_sha)
        and isinstance(nonce, str)
        and nonce
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


def _git_observations(repo: Path) -> tuple[str | None, bool]:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None, False
    return (sha if _SHA_RE.fullmatch(sha) else None), not status.strip()


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _sha256_file(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(64 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return None


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


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
