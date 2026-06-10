"""Deterministic repair-choice prompts for ambiguous intents.

When Parker's confidence in an intent is low, it offers 2-3 concrete
choices plus "none of these" instead of forcing the user to repeat
themselves. This module is text-level only: it builds and validates the
choice structure. It never stages, confirms, or executes anything —
committing to a choice flows through the normal capture → resolve →
stage → confirm pipeline gates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from app.parker import policy

MIN_CANDIDATES = 2
MAX_CANDIDATES = 3
MAX_LABEL_LENGTH = 80
NONE_OF_THESE_LABEL = "none of these"
DEFAULT_QUESTION = "I want to make sure I understood. Did you mean:"


@dataclass(frozen=True)
class RepairChoice:
    """One offered interpretation of an ambiguous utterance.

    ``action_type`` is a policy taxonomy type the choice would resolve to,
    or None when the interpretation carries no action (e.g. the user was
    just chatting, or this is the none-of-these choice).
    """

    label: str
    action_type: Optional[str]


@dataclass(frozen=True)
class RepairPrompt:
    """A validated repair question: 2-3 candidates plus a none-of-these choice."""

    question: str
    choices: tuple[RepairChoice, ...]

    @property
    def candidates(self) -> tuple[RepairChoice, ...]:
        """The concrete interpretations, excluding the trailing none-of-these."""

        return self.choices[:-1]

    def as_spoken_text(self) -> str:
        """Render the prompt as one short utterance for voice output."""

        numbered = [f"{index}) {choice.label}" for index, choice in enumerate(self.choices, start=1)]
        return f"{self.question} {', '.join(numbered[:-1])}, or {numbered[-1]}?"


def build_repair_prompt(
    candidates: Iterable[tuple[str, Optional[str]]],
    question: str = DEFAULT_QUESTION,
) -> RepairPrompt:
    """Build a validated repair prompt from (label, action_type) candidates.

    Raises ValueError when the candidate set could not be offered safely:
    wrong count, blank/over-long/duplicate labels, action types unknown to
    the policy taxonomy, or prohibited action types (which must be refused,
    never offered as a choice).
    """

    entries = list(candidates)
    if not MIN_CANDIDATES <= len(entries) <= MAX_CANDIDATES:
        raise ValueError(
            f"repair prompt needs {MIN_CANDIDATES}-{MAX_CANDIDATES} candidates, got {len(entries)}"
        )
    if not question or not question.strip():
        raise ValueError("repair prompt question must not be blank")

    seen_labels: set[str] = set()
    choices: list[RepairChoice] = []
    for raw_label, action_type in entries:
        label = (raw_label or "").strip()
        if not label:
            raise ValueError("repair choice label must not be blank")
        if len(label) > MAX_LABEL_LENGTH:
            raise ValueError(f"repair choice label too long ({len(label)} > {MAX_LABEL_LENGTH}): {label!r}")
        if label.lower() == NONE_OF_THESE_LABEL:
            raise ValueError("none-of-these is appended automatically; do not pass it as a candidate")
        if label.lower() in seen_labels:
            raise ValueError(f"duplicate repair choice label: {label!r}")
        seen_labels.add(label.lower())

        if action_type is not None:
            if action_type not in policy.ACTION_POLICIES:
                raise ValueError(f"repair choice action type unknown to policy taxonomy: {action_type!r}")
            if policy.get_policy(action_type).tier == policy.TIER_PROHIBITED:
                raise ValueError(
                    f"prohibited action type may not be offered as a repair choice: {action_type!r}"
                )
        choices.append(RepairChoice(label=label, action_type=action_type))

    choices.append(RepairChoice(label=NONE_OF_THESE_LABEL, action_type=None))
    return RepairPrompt(question=question.strip(), choices=tuple(choices))
