"""The hands: OpenClaw skills executing Parker-approved intents.

Half two of the v1 design (docs/brain-adapters.md): brains propose at the
conversation seam; THIS module acts at the execution seam — and only on
staged intents the patient confirmed (plus caregiver approval where a tier
still requires it). Parker's pipeline stays the source of truth for what
was approved; OpenClaw skills are the hands.

Trust boundaries, enforced here in code:

- **Family curates the skill surface.** Parker discovers the enabled-skill
  list from the gateway at startup. An action type with no enabled skill
  behind it is neither proposable nor executable.
- **Policy classification gates the gateway.** A gateway-advertised action
  type becomes executable only if the policy taxonomy already classifies
  it LOCAL_REVERSIBLE with user confirmation. Unknown types default to the
  irreversible/human-operator policy and are ignored; a gateway cannot
  smuggle purchases or messaging past the taxonomy by advertising a skill.
- **Failure containment.** A skill error after confirmation becomes a
  ``failed`` review row and a spoken failure — never a silent retry with
  side effects. ``invoke`` is called at most once per execution attempt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from app.brain.openclaw import GatewayError, OpenClawGateway, build_openclaw_gateway
from app.parker.policy import CONFIRM_USER, TIER_LOCAL_REVERSIBLE, get_policy

logger = logging.getLogger("parker.hands")


@dataclass(frozen=True)
class SkillResult:
    """Outcome of one skill invocation; ``detail`` is written to be spoken."""

    ok: bool
    detail: str


class OpenClawHands:
    """Enabled OpenClaw skills, discovered once at startup, invoked per intent."""

    def __init__(self, gateway: OpenClawGateway, skills: list[dict[str, Any]]) -> None:
        self._gateway = gateway
        self._skill_by_action_type: dict[str, str] = {}
        for entry in skills:
            if not entry.get("enabled", False):
                continue
            name = str(entry.get("name", "")).strip()
            if not name:
                continue
            for action_type in entry.get("action_types") or []:
                # First enabled skill wins per action type; admins keep the
                # mapping one-to-one on the gateway side.
                self._skill_by_action_type.setdefault(str(action_type), name)

    @classmethod
    def discover(cls, gateway: OpenClawGateway) -> "OpenClawHands":
        """Read the enabled-skill list from the gateway (raises GatewayError)."""

        return cls(gateway, gateway.list_parker_skills())

    def enabled_action_types(self) -> frozenset[str]:
        return frozenset(self._skill_by_action_type)

    def skill_for(self, action_type: str) -> Optional[str]:
        return self._skill_by_action_type.get(action_type)

    def invoke(self, action_type: str, payload: dict[str, Any], *, idempotency_key: str) -> SkillResult:
        """Forward one approved intent to its skill. Never raises, never retries."""

        skill = self.skill_for(action_type)
        if skill is None:
            return SkillResult(ok=False, detail=f"no enabled OpenClaw skill for {action_type}")
        try:
            data = self._gateway.invoke_skill(
                action_type,
                dict(payload, skill=skill),
                idempotency_key=idempotency_key,
            )
        except GatewayError as exc:
            logger.warning("OpenClaw skill invocation failed: %s", exc)
            return SkillResult(ok=False, detail=f"couldn't reach the {skill} skill ({exc})")
        detail = str(data.get("detail") or "").strip() or f"{skill} finished without details"
        return SkillResult(ok=(data.get("status") == "ok"), detail=detail)


# ---------------------------------------------------------------------------
# Process-level registry. Configured once at startup (server lifespan / CLI
# entry points) from settings; tests install fakes via configure_hands and
# an autouse fixture resets it between tests.
# ---------------------------------------------------------------------------

_hands: Optional[Any] = None


def configure_hands(hands: Optional[Any]) -> None:
    global _hands
    _hands = hands


def configured_hands() -> Optional[Any]:
    return _hands


def configure_hands_from_settings() -> Optional[Any]:
    """Discover skills from the configured gateway; degrade to None quietly.

    Zero-config (no ``PARKER_OPENCLAW_GATEWAY_URL``) touches nothing and
    returns None. A configured-but-unreachable gateway logs and returns
    None: gateway-backed action types simply stay invisible until restart.
    """

    gateway = build_openclaw_gateway()
    if gateway is None:
        configure_hands(None)
        return None
    try:
        hands = OpenClawHands.discover(gateway)
    except GatewayError as exc:
        logger.warning("OpenClaw skill discovery failed; hands disabled: %s", exc)
        configure_hands(None)
        return None
    configure_hands(hands)
    logger.info(
        "OpenClaw hands enabled for action types: %s",
        ", ".join(sorted(hands.enabled_action_types())) or "(none)",
    )
    return hands


def gateway_executable_action_types() -> frozenset[str]:
    """Gateway-backed action types Parker may execute right now.

    The double gate: the family enabled a skill for the type on the
    gateway AND the policy taxonomy classifies the type LOCAL_REVERSIBLE
    with user confirmation. Locally executable types never route to the
    gateway; unknown/irreversible/prohibited advertisements are ignored.
    """

    hands = configured_hands()
    if hands is None:
        return frozenset()
    allowed: set[str] = set()
    for action_type in hands.enabled_action_types():
        policy = get_policy(action_type)
        if policy.executable_in_v0:
            continue  # local types execute locally, period
        if policy.tier == TIER_LOCAL_REVERSIBLE and policy.confirmation == CONFIRM_USER:
            allowed.add(action_type)
    return frozenset(allowed)


def effective_proposable_action_types() -> frozenset[str]:
    """What brains may propose right now: base set minus skill-less gateway types."""

    from app.brain.adapter import OPENCLAW_BACKED_ACTION_TYPES, PROPOSABLE_ACTION_TYPES

    hands = configured_hands()
    enabled = hands.enabled_action_types() if hands is not None else frozenset()
    return PROPOSABLE_ACTION_TYPES - (OPENCLAW_BACKED_ACTION_TYPES - enabled)
