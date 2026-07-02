"""BrainAdapter: the contract every conversational brain implements.

Parker (the brainstem) owns hearing, repair, safety refusals, and the
staged-action pipeline. A brain only converses: given the utterance, the
bounded session history, and a small context card, it returns spoken text
plus zero or more *proposed* actions. Proposals are exactly that — Parker
converts them into confirmation-gated choices routed through the existing
capture pipeline. A brain cannot capture, stage, execute, or send.

Adapters implementing this contract:

- ``ClaudeBrainAdapter`` (``app.brain.claude``) — v0, direct Anthropic API.
- ``OpenClawBrainAdapter`` (``app.brain.openclaw``) — v1: the family's
  OpenClaw agent converses here; staged+confirmed intents are forwarded to
  family-curated skills at the execution seam (``app.parker.hands``).
- Realtime speech models — a later family opt-in, same contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol

# Action types a brain may propose. Deliberately the capture-able subset of
# the policy taxonomy (backend/app/parker/policy.py): everything here stages
# through capture → resolve → stage → confirm, so a proposal can never skip a
# gate. New types require policy-tier classification first, never ad hoc
# (open_links was classified LOCAL_REVERSIBLE / open-and-read-only before
# being added here).
PROPOSABLE_ACTION_TYPES = frozenset(
    {
        "reminder",
        "family_message",
        "exercise_start",
        "media_playlist",
        "appointment_note",
        "open_links",
    }
)

# The subset whose execution is an OpenClaw skill rather than Parker's own
# local pipeline. These are proposable/executable ONLY while the family's
# gateway advertises an enabled skill behind them (app.parker.hands); with
# no gateway they are invisible to every brain.
OPENCLAW_BACKED_ACTION_TYPES = frozenset({"media_playlist", "open_links"})


@dataclass(frozen=True)
class Message:
    """One prior turn in the brain-lane conversation history."""

    role: str  # "user" | "assistant"
    content: str


@dataclass(frozen=True)
class BrainContext:
    """The small, non-sensitive context card a brain receives.

    Names come from the family-configured personal lexicon — the same list
    that biases ASR — so the brain can resolve "tell Sarah" to a real
    person. Never credentials, medical records, or raw audio.
    """

    patient_name: str = "Dad"
    lexicon_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProposedAction:
    """An action the brain suggests; only ever offered, never taken.

    ``action_type`` must be one of ``PROPOSABLE_ACTION_TYPES``; anything
    else is dropped by the post-response guard before the user hears it.
    """

    action_type: str
    label: str
    subject: str
    intent_text: str
    recipient: Optional[str] = None


@dataclass(frozen=True)
class BrainReply:
    """What a brain returns: speech to say aloud, plus proposals."""

    speech: str
    proposed_actions: tuple[ProposedAction, ...] = field(default_factory=tuple)


class BrainAdapter(Protocol):
    """The one method a brain implements."""

    def respond(
        self,
        history: list[Message],
        utterance: str,
        context: BrainContext,
    ) -> BrainReply:
        """Answer one utterance. Must not perform side effects."""
        ...
