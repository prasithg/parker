"""Deterministic post-response guard over every brain reply.

The system prompt asks the brain to hold the medical boundary; this module
*enforces* it in code, adapter-agnostic, after every response. Defense in
depth: the pre-model guards in ``TextSession`` refuse medical utterances
before the brain ever sees them, and this guard catches a brain that
drifts into diagnosis/treatment/medication territory anyway.

Also the choke point for proposals: anything outside
``PROPOSABLE_ACTION_TYPES`` (or malformed) is dropped here, so no adapter
can smuggle a new action class past the policy taxonomy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.brain.adapter import PROPOSABLE_ACTION_TYPES, BrainReply, ProposedAction

MAX_PROPOSALS = 2  # choices surface as 2-3 options incl. none-of-these
MAX_LABEL_LENGTH = 80  # mirrors app.conversation.repair.MAX_LABEL_LENGTH

MEDICAL_BOUNDARY_REDIRECT = (
    "That's a medical question, and I leave those to your doctor. "
    "I can save it as a question for your next appointment, or let the family know you're wondering."
)

# Directive medication/treatment language a spoken answer must never contain.
# Precision matters less than on the input side: a false trip costs one
# redirect sentence, a miss costs the medical boundary.
_DOSAGE_PATTERN = re.compile(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|milligrams?|micrograms?)\b")
_MEDICAL_DIRECTIVE_PHRASES = (
    "you should take",
    "you could take",
    "you can take",
    "try taking",
    "worth taking",
    "increase your",
    "decrease your",
    "reduce your",
    "lower your",
    "double your",
    "halve your",
    "skip your",
    "skip a dose",
    "extra dose",
    "stop taking",
    "start taking",
    "adjust your dose",
    "adjust the dose",
    "your dosage",
    "the recommended dose",
    "recommended treatment",
    "treatment for this",
    "best treatment",
)
_DIAGNOSIS_PHRASES = (
    "you have parkinson",
    "you likely have",
    "you probably have",
    "you may have a",
    "you might have a",
    "sounds like you have",
    "this means you have",
    "consistent with a diagnosis",
    "could be a sign of",
    "is a symptom of",
)


def speech_violates_medical_boundary(speech: str) -> bool:
    """True when spoken text crosses into medical advice territory."""

    lowered = re.sub(r"\s+", " ", speech.lower())
    if _DOSAGE_PATTERN.search(lowered):
        return True
    return any(phrase in lowered for phrase in _MEDICAL_DIRECTIVE_PHRASES) or any(
        phrase in lowered for phrase in _DIAGNOSIS_PHRASES
    )


@dataclass(frozen=True)
class ScreenResult:
    """A screened reply plus what the guard did to it."""

    reply: BrainReply
    medical_boundary_tripped: bool
    dropped_action_count: int


def _valid_proposal(action: ProposedAction, proposable: frozenset[str]) -> bool:
    return (
        action.action_type in proposable
        and bool(action.label.strip())
        and bool(action.subject.strip())
        and bool(action.intent_text.strip())
    )


def screen_reply(reply: BrainReply, *, proposable: frozenset[str] | None = None) -> ScreenResult:
    """Enforce the medical boundary and the proposable-action allowlist.

    A medical-boundary trip replaces the whole reply — speech and
    proposals — with the redirect; a poisoned answer must not keep its
    action suggestions either.

    ``proposable`` narrows the allowlist to what is proposable RIGHT NOW
    (e.g. gateway-backed types only while an enabled skill exists —
    ``app.parker.hands.effective_proposable_action_types``). It can only
    ever be a subset: anything outside ``PROPOSABLE_ACTION_TYPES`` is
    dropped regardless.
    """

    allowed = PROPOSABLE_ACTION_TYPES if proposable is None else (proposable & PROPOSABLE_ACTION_TYPES)

    if speech_violates_medical_boundary(reply.speech):
        return ScreenResult(
            reply=BrainReply(speech=MEDICAL_BOUNDARY_REDIRECT, proposed_actions=()),
            medical_boundary_tripped=True,
            dropped_action_count=len(reply.proposed_actions),
        )

    kept: list[ProposedAction] = []
    for action in reply.proposed_actions:
        if not _valid_proposal(action, allowed):
            continue
        if len(action.label) > MAX_LABEL_LENGTH:
            action = ProposedAction(
                action_type=action.action_type,
                label=action.label[: MAX_LABEL_LENGTH - 1].rstrip() + "…",
                subject=action.subject,
                intent_text=action.intent_text,
                recipient=action.recipient,
            )
        kept.append(action)
    kept = kept[:MAX_PROPOSALS]
    dropped = len(reply.proposed_actions) - len(kept)
    if dropped == 0:
        return ScreenResult(reply=reply, medical_boundary_tripped=False, dropped_action_count=0)
    return ScreenResult(
        reply=BrainReply(speech=reply.speech, proposed_actions=tuple(kept)),
        medical_boundary_tripped=False,
        dropped_action_count=dropped,
    )


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
WANT_MORE_SUFFIX = "Want more detail?"


def trim_for_speech(speech: str, *, max_sentences: int = 3, max_chars: int = 360) -> str:
    """Cap an answer at TTS-listenable length.

    Long monologues are unusable over voice. Keep the first sentences that
    fit, then offer continuation instead of monologuing — the user can ask
    for more.
    """

    text = re.sub(r"\s+", " ", speech).strip()
    if not text:
        return text
    kept: list[str] = []
    truncated = False
    for sentence in _SENTENCE_END.split(text):
        if len(kept) >= max_sentences or (kept and len(" ".join(kept + [sentence])) > max_chars):
            truncated = True
            break
        kept.append(sentence)
    result = " ".join(kept)
    if len(result) > max_chars:  # a single over-long sentence still hard-caps
        result = result[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:.") + "…"
        truncated = True
    if truncated:
        result += f" {WANT_MORE_SUFFIX}"
    return result
