"""Repository-level contracts for sustained Fable/agent delivery sessions.

These tests do not grade prose quality. They pin the invocation and evidence
surfaces that prevent a good workflow from existing only in an old chat.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / "docs" / "agent-development-workflow.md"
SESSION = ROOT / ".claude" / "commands" / "parker-session.md"
REVIEW = ROOT / ".claude" / "commands" / "parker-review.md"
PR_TEMPLATE = ROOT / ".github" / "pull_request_template.md"
SESSION_HOOK = ROOT / ".claude" / "hooks" / "session-git-status.sh"


def _text(path: Path) -> str:
    assert path.is_file(), f"missing delivery-contract surface: {path.relative_to(ROOT)}"
    return path.read_text()


def test_delivery_contract_has_evidence_states_and_no_silent_gate_downgrade():
    text = _text(WORKFLOW)
    for state in (
        "specified",
        "planned",
        "implemented",
        "verified",
        "independently_reviewed",
        "human_accepted",
        "merged",
        "compounded",
    ):
        assert state in text
    for required in (
        "Blast tier",
        "Intent/acceptance source",
        "Evidence checked",
        "What remains untested",
        "Deliberate deviations and approving authority",
    ):
        assert required in text
    assert "may not unilaterally downgrade, defer, or reclassify" in text
    assert "same-family/degraded review" in text
    assert "45–50%" in text


def test_parker_session_command_requires_intent_evidence_and_independent_gate():
    text = _text(SESSION)
    for required in (
        "$ARGUMENTS",
        "Goal and acceptance source",
        "T0/T1/T2 blast tier",
        "Verification matrix",
        "Human/reserved gates",
        "What you are least sure about",
        "git diff --check",
        "make test",
        "fresh exact-revision independent review",
        "What remains untested",
    ):
        assert required in text
    assert "may not downgrade/defer/reclassify" in text
    assert "do not merge" in text


def test_review_command_cannot_treat_builder_self_review_as_independent():
    text = _text(REVIEW)
    for required in (
        "same-family builder self-review",
        "blast tier",
        "intent block",
        "Acceptance coverage",
        "Evidence checked",
        "What I did not check",
        "Human/device gates",
        "builder-deferred/reclassified gate",
    ):
        assert required in text
    assert "does not satisfy T1/T2 independent review alone" in _text(WORKFLOW)


def test_pull_request_template_exposes_the_review_contract():
    text = _text(PR_TEMPLATE)
    for heading in (
        "## Intent",
        "## Decisions",
        "## Scope",
        "## Risk",
        "## Verification",
        "## Review and delivery state",
    ):
        assert heading in text
    for required in (
        "Acceptance source",
        "Blast tier",
        "What remains untested",
        "Exact revision reviewed",
        "Deliberate deviations and approving authority",
    ):
        assert required in text


def test_session_start_hook_surfaces_the_workflow_and_remains_valid_shell():
    text = _text(SESSION_HOOK)
    assert "/parker-session" in text
    assert "docs/agent-development-workflow.md" in text
    result = subprocess.run(
        ["bash", "-n", str(SESSION_HOOK)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
