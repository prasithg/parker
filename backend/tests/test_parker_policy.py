"""Invariants for the Parker action taxonomy and confirmation policy."""

from app.parker import policy
from app.parker.pipeline import REVERSIBLE_ACTION_TYPES


def test_every_policy_has_valid_tier_and_confirmation():
    for action_type, entry in policy.ACTION_POLICIES.items():
        assert entry.action_type == action_type
        assert entry.tier in policy.VALID_TIERS
        assert entry.confirmation in policy.VALID_CONFIRMATION_LEVELS


def test_prohibited_actions_are_never_executable_and_always_refused():
    for entry in policy.ACTION_POLICIES.values():
        if entry.tier == policy.TIER_PROHIBITED:
            assert entry.executable_in_v0 is False
            assert entry.confirmation == policy.CONFIRM_REFUSE
        else:
            assert entry.confirmation != policy.CONFIRM_REFUSE


def test_irreversible_external_actions_are_never_executable_in_v0():
    for entry in policy.ACTION_POLICIES.values():
        if entry.tier == policy.TIER_IRREVERSIBLE_EXTERNAL:
            assert entry.executable_in_v0 is False


def test_irreversible_external_actions_require_human_operator():
    for entry in policy.ACTION_POLICIES.values():
        if entry.tier == policy.TIER_IRREVERSIBLE_EXTERNAL:
            assert entry.confirmation == policy.CONFIRM_HUMAN_OPERATOR


def test_v0_execution_surface_is_reminders_and_local_outbox_messages():
    # family_message graduated to executable on 2026-06-09: its v0 execution
    # artifact is a LOCAL outbox row (cancellable, no send path), so it stays
    # within the local-reversible execution boundary. family_escalation and
    # all irreversible/prohibited types remain non-executable.
    assert policy.executable_v0_action_types() == {"reminder", "family_message"}
    assert REVERSIBLE_ACTION_TYPES == {"reminder", "family_message"}
    assert policy.is_executable_v0("family_escalation") is False
    assert policy.is_executable_v0("smart_home") is False
    assert policy.is_executable_v0("purchase") is False


def test_unknown_action_types_default_to_safe_policy():
    unknown = policy.get_policy("teleport_patient")
    assert unknown.executable_in_v0 is False
    assert unknown.confirmation == policy.CONFIRM_HUMAN_OPERATOR
    assert policy.is_executable_v0("teleport_patient") is False


def test_safety_critical_action_types_are_classified():
    assert policy.confirmation_level("medication_change") == policy.CONFIRM_REFUSE
    assert policy.confirmation_level("medical_advice") == policy.CONFIRM_REFUSE
    assert policy.confirmation_level("purchase") == policy.CONFIRM_HUMAN_OPERATOR
    assert policy.confirmation_level("family_message") == policy.CONFIRM_USER
