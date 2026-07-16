"""Fail-closed negative controls for scheduled-reality provenance."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from app.parker.scheduled_reality import (
    MAX_EVIDENCE_BYTES,
    mint_scheduler_envelope,
    verify_scheduled_reality,
)


JOB_ID = "nightly-parker-autodata"
KEY = b"test-only-scheduler-key"


def _run(*args: str, cwd: Path) -> str:
    return subprocess.run(
        args,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _honest_run_inputs(tmp_path: Path) -> dict[str, Any]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run("git", "init", "-q", cwd=repo)
    _run("git", "config", "user.email", "parker-tests@example.invalid", cwd=repo)
    _run("git", "config", "user.name", "Parker tests", cwd=repo)
    (repo / "tracked.txt").write_text("committed\n")
    _run("git", "add", "tracked.txt", cwd=repo)
    _run("git", "commit", "-qm", "test fixture", cwd=repo)
    code_sha = _run("git", "rev-parse", "HEAD", cwd=repo)

    inbox = tmp_path / "operations-inbox"
    inbox.mkdir()
    candidate = inbox / "candidate.json"
    candidate.write_text('{"candidate":"synthetic metadata only"}\n')

    import hashlib

    input_sha = hashlib.sha256(candidate.read_bytes()).hexdigest()
    state_dir = tmp_path / "operations-state"
    state_dir.mkdir()
    consumed_nonce_root = tmp_path / "trusted-consumed-nonces"
    consumed_nonce_root.mkdir()
    pre_state = state_dir / "pre.json"
    post_state = state_dir / "post.json"
    pre_state.write_text(
        json.dumps(
            {
                "run_sequence": 7,
                "last_status": "ok",
                "last_input_sha256": "0" * 64,
                "error": None,
            }
        )
    )
    post_state.write_text(
        json.dumps(
            {
                "run_sequence": 8,
                "last_status": "ok",
                "last_input_sha256": input_sha,
                "error": None,
            }
        )
    )

    fire_epoch = time.time()
    envelope = state_dir / "scheduler-envelope.json"
    envelope.write_text(
        json.dumps(
            mint_scheduler_envelope(
                key=KEY,
                job_id=JOB_ID,
                scheduled_fire_epoch=fire_epoch,
                expected_code_sha=code_sha,
                nonce="test-fire-0001",
            )
        )
    )
    return {
        "repo_root": repo,
        "inbox_root": inbox,
        "input_path": candidate,
        "pre_state_path": pre_state,
        "post_state_path": post_state,
        "scheduler_envelope_path": envelope,
        "consumed_nonce_root": consumed_nonce_root,
        "expected_job_id": JOB_ID,
        "approved_code_sha": code_sha,
        "verification_key": KEY,
        "max_fire_skew_seconds": 60.0,
        "max_input_age_seconds": 60.0,
        "clock_observation_seconds": 0.02,
    }


def _verify(values: dict[str, Any]) -> dict[str, Any]:
    return verify_scheduled_reality(**values)


def _bind_post_state_to_input(values: dict[str, Any]) -> None:
    candidate = values["input_path"]
    post_state = values["post_state_path"]
    assert isinstance(candidate, Path)
    assert isinstance(post_state, Path)
    payload = json.loads(post_state.read_text())
    payload["last_input_sha256"] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    post_state.write_text(json.dumps(payload))


def _install_fake_git(tmp_path: Path, monkeypatch, body: str) -> None:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text(f"#!/bin/sh\nset -eu\n{body}\n")
    fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")


def test_honest_scheduled_run_qualifies(tmp_path):
    values = _honest_run_inputs(tmp_path)
    envelope_path = values["scheduler_envelope_path"]
    assert isinstance(envelope_path, Path)
    scheduler_token = json.loads(envelope_path.read_text())["token"]

    receipt = _verify(values)

    serialized = json.dumps(receipt)
    assert receipt["provenance_complete"] is True
    assert receipt["verdict"] == "verified"
    assert receipt["failed_assertions"] == []
    assert all(receipt["assertions"].values())
    assert scheduler_token not in serialized
    assert KEY.decode() not in serialized
    assert str(tmp_path) not in serialized


def test_old_sha_cannot_qualify(tmp_path):
    values = _honest_run_inputs(tmp_path)
    # The scheduler can validly sign the checkout it fired, but an independently
    # pinned approved SHA must still prevent an old deployment from qualifying.
    values["approved_code_sha"] = "0" * 40

    receipt = _verify(values)

    assert receipt["provenance_complete"] is False
    assert receipt["verdict"] == "unverified"
    assert "code_sha_matches_approved" in receipt["failed_assertions"]
    assert "scheduler_sha_matches_approved" in receipt["failed_assertions"]


def test_manual_trigger_without_scheduler_envelope_cannot_qualify(tmp_path):
    values = _honest_run_inputs(tmp_path)
    values["scheduler_envelope_path"] = tmp_path / "missing-envelope.json"

    receipt = _verify(values)

    assert receipt["provenance_complete"] is False
    assert "scheduler_signature_valid" in receipt["failed_assertions"]


def test_scheduler_envelope_cannot_be_replayed(tmp_path):
    values = _honest_run_inputs(tmp_path)

    first_receipt = _verify(values)
    replayed_receipt = _verify(values)

    assert first_receipt["provenance_complete"] is True
    assert replayed_receipt["provenance_complete"] is False
    assert replayed_receipt["verdict"] == "unverified"
    assert "scheduler_nonce_single_use" in replayed_receipt["failed_assertions"]


def test_nonce_ledger_inside_repository_cannot_qualify(tmp_path):
    values = _honest_run_inputs(tmp_path)
    repo = values["repo_root"]
    assert isinstance(repo, Path)
    values["consumed_nonce_root"] = repo / ".git"

    receipt = _verify(values)

    assert receipt["provenance_complete"] is False
    assert "nonce_ledger_trusted_scope" in receipt["failed_assertions"]
    assert "scheduler_nonce_single_use" in receipt["failed_assertions"]


def test_repo_fixture_input_cannot_qualify(tmp_path):
    values = _honest_run_inputs(tmp_path)
    repo = values["repo_root"]
    assert isinstance(repo, Path)
    fixture = repo / "tests" / "fixtures" / "candidate.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_text('{"candidate":"fixture"}\n')
    values["input_path"] = fixture

    receipt = _verify(values)

    assert receipt["provenance_complete"] is False
    assert "input_is_real_inbound" in receipt["failed_assertions"]


def test_symlinked_inbound_input_cannot_qualify(tmp_path):
    values = _honest_run_inputs(tmp_path)
    candidate = values["input_path"]
    assert isinstance(candidate, Path)
    target = candidate.with_name("actual-candidate.json")
    candidate.rename(target)
    candidate.symlink_to(target.name)

    receipt = _verify(values)

    assert receipt["provenance_complete"] is False
    assert "input_regular_nofollow" in receipt["failed_assertions"]


def test_hardlinked_inbound_input_cannot_qualify(tmp_path):
    values = _honest_run_inputs(tmp_path)
    candidate = values["input_path"]
    assert isinstance(candidate, Path)
    target = candidate.with_name("hardlink-target.json")
    candidate.rename(target)
    candidate.hardlink_to(target)

    receipt = _verify(values)

    assert receipt["provenance_complete"] is False
    assert "input_regular_nofollow" in receipt["failed_assertions"]


def test_symlinked_inbox_parent_cannot_qualify(tmp_path):
    values = _honest_run_inputs(tmp_path)
    inbox = values["inbox_root"]
    candidate = values["input_path"]
    assert isinstance(inbox, Path)
    assert isinstance(candidate, Path)
    inbox_alias = tmp_path / "inbox-alias"
    inbox_alias.symlink_to(inbox.name, target_is_directory=True)
    values["inbox_root"] = inbox_alias
    values["input_path"] = inbox_alias / candidate.name

    receipt = _verify(values)

    assert receipt["provenance_complete"] is False
    assert "input_regular_nofollow" in receipt["failed_assertions"]


def test_symlinked_scheduler_envelope_cannot_qualify(tmp_path):
    values = _honest_run_inputs(tmp_path)
    envelope = values["scheduler_envelope_path"]
    assert isinstance(envelope, Path)
    target = envelope.with_name("scheduler-envelope-real.json")
    envelope.rename(target)
    envelope.symlink_to(target.name)

    receipt = _verify(values)

    assert receipt["provenance_complete"] is False
    assert "scheduler_envelope_regular_nofollow" in receipt["failed_assertions"]


def test_symlinked_state_evidence_cannot_qualify(tmp_path):
    values = _honest_run_inputs(tmp_path)
    post_state = values["post_state_path"]
    assert isinstance(post_state, Path)
    target = post_state.with_name("post-real.json")
    post_state.rename(target)
    post_state.symlink_to(target.name)

    receipt = _verify(values)

    assert receipt["provenance_complete"] is False
    assert "state_evidence_regular_nofollow" in receipt["failed_assertions"]


def test_symlinked_nonce_ledger_cannot_qualify(tmp_path):
    values = _honest_run_inputs(tmp_path)
    nonce_root = values["consumed_nonce_root"]
    assert isinstance(nonce_root, Path)
    target = nonce_root.with_name("actual-trusted-consumed-nonces")
    nonce_root.rename(target)
    nonce_root.symlink_to(target.name, target_is_directory=True)

    receipt = _verify(values)

    assert receipt["provenance_complete"] is False
    assert "nonce_ledger_trusted_scope" in receipt["failed_assertions"]


def test_oversized_inbound_input_cannot_qualify(tmp_path):
    values = _honest_run_inputs(tmp_path)
    candidate = values["input_path"]
    assert isinstance(candidate, Path)
    candidate.write_bytes(b"x" * (MAX_EVIDENCE_BYTES + 1))
    _bind_post_state_to_input(values)

    receipt = _verify(values)

    assert receipt["provenance_complete"] is False
    assert "input_regular_nofollow" in receipt["failed_assertions"]


def test_frozen_wall_clock_cannot_qualify(tmp_path, monkeypatch):
    values = _honest_run_inputs(tmp_path)
    envelope_path = values["scheduler_envelope_path"]
    assert isinstance(envelope_path, Path)
    fire_epoch = json.loads(envelope_path.read_text())["scheduled_fire_epoch"]
    monkeypatch.setattr("app.parker.scheduled_reality.time.time", lambda: fire_epoch)

    receipt = _verify(values)

    assert receipt["provenance_complete"] is False
    assert "wall_monotonic_coherent" in receipt["failed_assertions"]


def test_missing_dedupe_state_delta_cannot_qualify(tmp_path):
    values = _honest_run_inputs(tmp_path)
    pre_state = values["pre_state_path"]
    post_state = values["post_state_path"]
    assert isinstance(pre_state, Path)
    assert isinstance(post_state, Path)
    post_state.write_bytes(pre_state.read_bytes())

    receipt = _verify(values)

    assert receipt["provenance_complete"] is False
    assert "state_delta_matches_input" in receipt["failed_assertions"]


def test_dirty_checkout_cannot_qualify(tmp_path):
    values = _honest_run_inputs(tmp_path)
    repo = values["repo_root"]
    assert isinstance(repo, Path)
    (repo / "tracked.txt").write_text("dirty\n")

    receipt = _verify(values)

    assert receipt["provenance_complete"] is False
    assert "checkout_clean" in receipt["failed_assertions"]


def test_hanging_git_observation_cannot_block_or_qualify(tmp_path, monkeypatch):
    values = _honest_run_inputs(tmp_path)
    monkeypatch.setattr(
        "app.parker.scheduled_reality.GIT_OBSERVATION_TIMEOUT_SECONDS",
        0.05,
        raising=False,
    )
    _install_fake_git(tmp_path, monkeypatch, "sleep 0.4")

    started = time.monotonic()
    receipt = _verify(values)
    elapsed = time.monotonic() - started

    assert elapsed < 0.3
    assert receipt["provenance_complete"] is False
    assert "git_observation_bounded" in receipt["failed_assertions"]
    assert receipt["observations"]["git"]["failure_reason"] == "timeout"


def test_git_stderr_flood_cannot_exhaust_or_qualify(tmp_path, monkeypatch):
    values = _honest_run_inputs(tmp_path)
    code_sha = values["approved_code_sha"]
    assert isinstance(code_sha, str)
    monkeypatch.setattr(
        "app.parker.scheduled_reality.MAX_GIT_OBSERVATION_BYTES",
        1024,
        raising=False,
    )
    _install_fake_git(
        tmp_path,
        monkeypatch,
        f'''if [ "$1" = "rev-parse" ]; then
  printf '%s\\n' '{code_sha}'
else
  i=0
  while [ "$i" -lt 256 ]; do
    printf '%s' 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx' >&2
    i=$((i + 1))
  done
fi''',
    )

    receipt = _verify(values)

    assert receipt["provenance_complete"] is False
    assert "git_observation_bounded" in receipt["failed_assertions"]
    assert receipt["observations"]["git"]["failure_reason"] == "output_limit"


def test_malformed_git_sha_is_named_invalid_output(tmp_path, monkeypatch):
    values = _honest_run_inputs(tmp_path)
    _install_fake_git(
        tmp_path,
        monkeypatch,
        '''if [ "$1" = "rev-parse" ]; then
  printf '%s\\n' 'not-a-valid-git-sha'
fi''',
    )

    receipt = _verify(values)

    assert receipt["provenance_complete"] is False
    assert "git_observation_bounded" in receipt["failed_assertions"]
    assert receipt["observations"]["git"]["failure_reason"] == "invalid_output"
