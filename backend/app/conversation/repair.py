"""Deterministic repair-choice prompts for ambiguous intents.

When Parker's confidence in an intent is low, it offers 2-3 concrete
choices plus "none of these" instead of forcing the user to repeat
themselves. This module is text-level only: it builds and validates the
choice structure. It never stages, confirms, or executes anything —
committing to a choice flows through the normal capture → resolve →
stage → confirm pipeline gates.
"""

from __future__ import annotations

import re
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
    *,
    min_candidates: int = MIN_CANDIDATES,
) -> RepairPrompt:
    """Build a validated repair prompt from (label, action_type) candidates.

    Raises ValueError when the candidate set could not be offered safely:
    wrong count, blank/over-long/duplicate labels, action types unknown to
    the policy taxonomy, or prohibited action types (which must be refused,
    never offered as a choice).

    ``min_candidates`` may be lowered to 1 for the confirmation-question
    form ("1) send Sarah a message…, or 2) none of these?") used when the
    brain proposes a single action; repair flows keep the 2-choice minimum.
    """

    entries = list(candidates)
    if not min_candidates <= len(entries) <= MAX_CANDIDATES:
        raise ValueError(
            f"repair prompt needs {min_candidates}-{MAX_CANDIDATES} candidates, got {len(entries)}"
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


# ---------------------------------------------------------------------------
# Model-driven candidate generation (opt-in; falls back when key not set)
# ---------------------------------------------------------------------------

_SUGGEST_SYSTEM = """\
You are Parker, a home assistant for people with Parkinson's disease. \
Your job is to suggest 2 clear, specific repair choices for an ambiguous utterance so the user can \
pick the right interpretation with a single spoken number.

Rules:
- Output ONLY a JSON array, no explanation, no markdown.
- Exactly 2 elements. Each element: {"label": "...", "action_type": "..."}
- action_type must be one of "reminder", "family_message", "exercise_start", "media_playlist", or "appointment_note".
- Labels ≤ 80 characters, phrased as Parker confirming what to do (e.g. \
"remind you to call Dr Smith", "send Priya a message about this").
- Labels must be specific to the utterance — never generic \
("set a reminder about this" is not acceptable).
- Do not invent family member names if none are mentioned; use "a family member" instead.
"""

_SUGGEST_USER = "Utterance: {utterance}"

_SUGGEST_USER_WITH_HISTORY = (
    "Utterance: {utterance}\n\n"
    "Previously offered choices that the user rejected (do not repeat any of these): {prior_labels}\n"
    "Offer 2 different, more specific alternatives."
)

_FALLBACK_CANDIDATES: list[tuple[str, str]] = [
    ("set a reminder about this", "reminder"),
    ("send a family message about this", "family_message"),
]


def _specific_fallback_candidates(utterance: str) -> list[tuple[str, str | None]] | None:
    """Return deterministic, audio-failure-aware candidates when no model is available.

    The nightly audio Autodata lane surfaced clipped starts like ``to speech
    exercise...``, ``YouTube stretching video``, and ``down from my appointment``.
    These have enough signal for a useful repair question, but the old no-key
    fallback always offered generic reminder/message choices. Keep this narrow:
    it should improve common local audio failures without pretending to solve all
    unclear speech.
    """

    normalized = re.sub(r"[,.!?]+", " ", utterance).strip().lower()
    normalized = re.sub(r"\s+", " ", normalized)
    if not normalized:
        return None

    if "speech exercise" in normalized or "voice exercise" in normalized:
        detail = _after_marker(normalized, "exercise") or "short practice"
        detail = _tidy_detail(detail)
        return [
            (f"start a speech exercise for {detail}"[:MAX_LABEL_LENGTH], "exercise_start"),
            ("set a reminder to practice the speech exercise", "reminder"),
        ]

    if "youtube" in normalized or "you tube" in normalized or "new tube" in normalized:
        topic = "stretching video" if "stretch" in normalized else "video"
        return [
            (f"play a YouTube {topic}", "media_playlist"),
            (f"set a reminder about the {topic}", "reminder"),
        ]

    music_cues = ("playlist", "music", "song", "songs", "itunes")
    wants_named_track = normalized.startswith(("i want to hear ", "hear ")) and " by " in normalized
    if (
        normalized.startswith("play ")
        or normalized.startswith("open songs")
        or normalized.startswith("turn on my playlist")
        or any(cue in normalized for cue in music_cues)
        or wants_named_track
    ):
        topic = _media_topic(normalized)
        return [
            (f"play {topic}"[:MAX_LABEL_LENGTH], "media_playlist"),
            (f"set a reminder about {topic}"[:MAX_LABEL_LENGTH], "reminder"),
        ]

    if "appointment" in normalized and normalized.startswith(("down ", "write down", "this down")):
        when = "tomorrow" if "tomorrow" in normalized else "appointment"
        return [
            (f"write this down for the appointment {when}", "appointment_note"),
            (f"set a reminder about the appointment {when}", "reminder"),
        ]

    return None


def _after_marker(text: str, marker: str) -> str:
    _, _, tail = text.partition(marker)
    return tail.strip(" :;-.")


def _media_topic(text: str) -> str:
    """Extract a concise media topic from a command-like music utterance."""

    topic = text
    for prefix in (
        "please ",
        "can you ",
        "could you ",
        "would you ",
        "turn on ",
        "open songs from ",
        "open ",
        "play ",
        "i want to hear ",
        "i want to listen to ",
        "listen to ",
        "hear ",
    ):
        if topic.startswith(prefix):
            topic = topic.removeprefix(prefix)
            break
    topic = _tidy_detail(topic)
    if topic.startswith("my ") and "playlist" in topic:
        return topic
    return topic or "music"


def _tidy_detail(text: str) -> str:
    detail = re.sub(r"^(?:for|about|called)\s+", "", text).strip()
    return detail or "short practice"


def suggest_repair_candidates(
    utterance: str,
    *,
    client: "Any | None" = None,
    model: str = "claude-haiku-4-5-20251001",
    prior_choices: "list[str] | None" = None,
) -> list[tuple[str, "str | None"]]:
    """Ask Claude for 2 contextually specific repair candidates.

    Returns a list of (label, action_type) tuples for ``build_repair_prompt``.
    Never raises: falls back to generic hardcoded candidates on any error
    (missing key, network, malformed JSON, validation failure).

    ``client`` must be an ``anthropic.Anthropic`` instance. When *None*, one
    is constructed from ``settings.anthropic_api_key``; if that is also empty
    the fallback candidates are returned immediately (no import attempted).

    ``prior_choices`` is an optional list of labels previously offered to the
    user (and rejected via "none of these"). When set, they are included in
    the prompt so the model can offer genuinely different alternatives.
    """
    import json as _json
    import logging

    from app.config import settings

    log = logging.getLogger("parker.repair")

    def _fallback(reason: str) -> list[tuple[str, str | None]]:
        log.debug("suggest_repair_candidates fallback (%s)", reason)
        specific = _specific_fallback_candidates(utterance)
        if specific is not None:
            return specific
        return list(_FALLBACK_CANDIDATES)

    if client is None:
        if not settings.anthropic_api_key:
            return _fallback("no api key")
        try:
            import anthropic as _anthropic
            client = _anthropic.Anthropic(api_key=settings.anthropic_api_key)
        except Exception as exc:  # noqa: BLE001
            return _fallback(f"client init: {exc}")

    try:
        if prior_choices:
            prior_labels = "; ".join(prior_choices)
            user_content = _SUGGEST_USER_WITH_HISTORY.format(
                utterance=utterance, prior_labels=prior_labels
            )
        else:
            user_content = _SUGGEST_USER.format(utterance=utterance)
        response = client.messages.create(
            model=model,
            max_tokens=256,
            system=_SUGGEST_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
        )
        raw = response.content[0].text.strip()
        data = _json.loads(raw)
        if not isinstance(data, list) or len(data) != 2:
            return _fallback(f"unexpected shape: {raw[:80]}")
        candidates: list[tuple[str, str | None]] = []
        for item in data:
            label = str(item.get("label", "")).strip()
            action_type = item.get("action_type")
            if not label:
                return _fallback("blank label in response")
            candidates.append((label, action_type))
        # validate through build_repair_prompt — catches unsafe action types,
        # over-long labels, etc. before they reach the user
        build_repair_prompt(candidates)
        return candidates
    except Exception as exc:  # noqa: BLE001
        return _fallback(str(exc))
