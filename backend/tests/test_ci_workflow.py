from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "parker-ci.yml"


def test_pr_ci_workflow_runs_backend_tests_and_grant_evals() -> None:
    """PRs should get remote evidence for the same local gates cited in grant docs."""

    assert WORKFLOW.exists(), "Parker PR CI workflow is missing"
    workflow_text = WORKFLOW.read_text()

    required_commands = [
        "make test",
        "make eval-tasks",
        "make eval-interactivity",
        "make eval-demo-interactivity",
        "make eval-degraded-input-replay",
        "make eval-claim-metric-map",
    ]
    for command in required_commands:
        assert command in workflow_text

    required_triggers = ["pull_request:", "push:"]
    for trigger in required_triggers:
        assert trigger in workflow_text

    assert "python-version: '3.11'" in workflow_text
    assert "ANTHROPIC_API_KEY" not in workflow_text
