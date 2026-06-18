"""Parker action taxonomy and confirmation policy.

Single source of truth for what Parker may do with a resolved action:
which risk tier it belongs to, what confirmation it requires, and
whether v0 is allowed to execute it at all.

The taxonomy describes the target capability surface; `executable_in_v0`
stays deliberately narrow until each action class gets its own tests/evals.
v0 executes reminders and family messages — and a family message "executes"
into the local outbox only (a reversible, cancellable local artifact);
nothing leaves the machine.
"""

from __future__ import annotations

from dataclasses import dataclass

# Risk tiers, ordered from safest to never-allowed.
TIER_INFORMATIONAL = "informational"  # read-only, no side effects outside the session
TIER_LOCAL_REVERSIBLE = "local_reversible"  # local state only, undoable
TIER_EXTERNAL_MESSAGING = "external_messaging"  # leaves the house: family messages, escalations
TIER_IRREVERSIBLE_EXTERNAL = "irreversible_external"  # purchases, external schedule/device changes
TIER_PROHIBITED = "prohibited"  # never performed, regardless of confirmation

# Confirmation levels.
CONFIRM_NONE = "none"  # answer directly, no confirmation step
CONFIRM_USER = "user"  # patient/caregiver confirms via voice/tap before acting
CONFIRM_POLICY = "policy"  # gated by a configured policy (e.g. escalation severity routing)
CONFIRM_HUMAN_OPERATOR = "human_operator"  # explicit family/operator approval outside the conversation
CONFIRM_REFUSE = "refuse"  # never confirmable; refuse and redirect

VALID_TIERS = {
    TIER_INFORMATIONAL,
    TIER_LOCAL_REVERSIBLE,
    TIER_EXTERNAL_MESSAGING,
    TIER_IRREVERSIBLE_EXTERNAL,
    TIER_PROHIBITED,
}
VALID_CONFIRMATION_LEVELS = {
    CONFIRM_NONE,
    CONFIRM_USER,
    CONFIRM_POLICY,
    CONFIRM_HUMAN_OPERATOR,
    CONFIRM_REFUSE,
}


@dataclass(frozen=True)
class ActionPolicy:
    """Policy for one action type."""

    action_type: str
    tier: str
    confirmation: str
    executable_in_v0: bool
    description: str


ACTION_POLICIES: dict[str, ActionPolicy] = {
    policy.action_type: policy
    for policy in (
        # Informational: read-only, answer after optional clarification.
        ActionPolicy(
            "research_summary", TIER_INFORMATIONAL, CONFIRM_NONE, False,
            "Research a topic and summarize it for the user.",
        ),
        ActionPolicy(
            "item_search", TIER_INFORMATIONAL, CONFIRM_NONE, False,
            "Look up items (e.g. Amazon search) without purchasing.",
        ),
        # Local reversible: change local state only; user confirms first.
        ActionPolicy(
            "reminder", TIER_LOCAL_REVERSIBLE, CONFIRM_USER, True,
            "Create/resurface a reminder. The only executable action in v0.",
        ),
        ActionPolicy(
            "routine_log", TIER_LOCAL_REVERSIBLE, CONFIRM_USER, False,
            "Log a completed routine or follow-up.",
        ),
        ActionPolicy(
            "appointment_note", TIER_LOCAL_REVERSIBLE, CONFIRM_USER, False,
            "Prepare/save notes and questions for an upcoming appointment.",
        ),
        ActionPolicy(
            "exercise_start", TIER_LOCAL_REVERSIBLE, CONFIRM_USER, False,
            "Start a speech/movement/cognitive exercise session.",
        ),
        ActionPolicy(
            "media_playlist", TIER_LOCAL_REVERSIBLE, CONFIRM_USER, False,
            "Build or start a YouTube/music playlist on the user's device.",
        ),
        # External messaging: leaves the household; user confirmation plus policy.
        ActionPolicy(
            "family_message", TIER_EXTERNAL_MESSAGING, CONFIRM_USER, True,
            "Draft a message to a family/caregiver contact. v0 execution after "
            "confirmation writes to the LOCAL outbox only (reversible; cancellable); "
            "no send path exists in v0.",
        ),
        ActionPolicy(
            "family_escalation", TIER_EXTERNAL_MESSAGING, CONFIRM_POLICY, False,
            "Notify family per the configured escalation policy (severity routing, "
            "auto-promotion). System-initiated when policy criteria are met.",
        ),
        # Irreversible/external: human operator approval; blocked in v0.
        ActionPolicy(
            "smart_home", TIER_IRREVERSIBLE_EXTERNAL, CONFIRM_HUMAN_OPERATOR, False,
            "Trigger a pre-approved smart-home action.",
        ),
        ActionPolicy(
            "calendar_change", TIER_IRREVERSIBLE_EXTERNAL, CONFIRM_HUMAN_OPERATOR, False,
            "Create or change an external calendar event.",
        ),
        ActionPolicy(
            "purchase", TIER_IRREVERSIBLE_EXTERNAL, CONFIRM_HUMAN_OPERATOR, False,
            "Buy an item. Never automatic; requires explicit human approval.",
        ),
        # Prohibited: refused regardless of confirmation.
        ActionPolicy(
            "medication_change", TIER_PROHIBITED, CONFIRM_REFUSE, False,
            "Change, skip, or adjust medication. Always refused.",
        ),
        ActionPolicy(
            "medical_advice", TIER_PROHIBITED, CONFIRM_REFUSE, False,
            "Diagnosis or treatment recommendation. Always refused.",
        ),
        ActionPolicy(
            "emergency_response", TIER_PROHIBITED, CONFIRM_REFUSE, False,
            "Acting as a substitute for emergency services. Always refused/redirected.",
        ),
        ActionPolicy(
            "privacy_disclosure", TIER_PROHIBITED, CONFIRM_REFUSE, False,
            "Reveal secrets, credentials, or sensitive private data. Always refused.",
        ),
    )
}

# Unknown action types resolve to the safest non-prohibited handling:
# blocked from execution, requires a human operator to even consider.
UNKNOWN_ACTION_POLICY = ActionPolicy(
    "unknown", TIER_IRREVERSIBLE_EXTERNAL, CONFIRM_HUMAN_OPERATOR, False,
    "Unrecognized action type; treated as irreversible until classified.",
)


def get_policy(action_type: str) -> ActionPolicy:
    """Return the policy for an action type, defaulting to the safe unknown policy."""

    return ACTION_POLICIES.get(action_type, UNKNOWN_ACTION_POLICY)


def is_executable_v0(action_type: str) -> bool:
    """Whether Parker v0 may execute this action type after confirmation."""

    return get_policy(action_type).executable_in_v0


def confirmation_level(action_type: str) -> str:
    """Confirmation required before acting on this action type."""

    return get_policy(action_type).confirmation


def executable_v0_action_types() -> set[str]:
    """Action types Parker v0 may execute after confirmation."""

    return {name for name, policy in ACTION_POLICIES.items() if policy.executable_in_v0}
