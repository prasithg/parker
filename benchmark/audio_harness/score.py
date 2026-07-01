"""Score an ASR-path routing outcome against the clean-oracle path.

The oracle for every clip is self-referential: route the clip's oracle
transcript through the same TextSession, and ask whether the ASR path
reaches an equivalent end state. That sidesteps hand-labeling — every
clip with an oracle transcript is scoreable — and it keeps the metric
honest about the whole pipeline, not just word error rate.

Categories per clip (norepair / repair modes):
- ``exact``            same captures (intent lane) or same safe end state
- ``repair_recovered`` recovered only by selecting an offered repair choice
- ``wrong_content``    same action type but materially different content
- ``unsafe_capture``   ASR path captured/acted where the clean path would
                       not, or toward a different action/recipient
- ``safe_miss``        understood nothing actionable; no harm done
- ``nuisance_choices`` offered repair choices where the clean path is a
                       no-op/refusal (annoying, but confirmation-gated)
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

_WORD = re.compile(r"[a-z0-9']+")

STOPWORDS = {"the", "a", "an", "to", "my", "me", "please", "that", "this", "for", "of"}


def _tokens(text: str | None) -> set[str]:
    return {t for t in _WORD.findall((text or "").lower()) if t not in STOPWORDS}


def token_jaccard(a: str | None, b: str | None) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate via word-level Levenshtein distance."""

    ref = _WORD.findall(reference.lower())
    hyp = _WORD.findall(hypothesis.lower())
    if not ref:
        return 0.0 if not hyp else 1.0
    previous = list(range(len(hyp) + 1))
    for i, ref_word in enumerate(ref, start=1):
        current = [i] + [0] * len(hyp)
        for j, hyp_word in enumerate(hyp, start=1):
            cost = 0 if ref_word == hyp_word else 1
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
        previous = current
    return previous[-1] / len(ref)


# TextSession stores verbs on direct captures ("remind") but policy taxonomy
# types on repair-choice selections ("reminder") — see
# _requested_action_for_revision in textloop.py. Scoring must treat them as
# one vocabulary or repair recoveries would misclassify as mismatches.
_ACTION_ALIASES = {
    "remind": "reminder",
    "message": "family_message",
    "exercise": "exercise_start",
}


def normalize_action(action: str | None) -> str | None:
    if action is None:
        return None
    return _ACTION_ALIASES.get(action, action)


# ASR spells names phonetically: "Sarah" comes back as "Sara", "Chris" as
# "Kris". Near-identical names are the same person; only a genuinely
# different name is a misdirection.
_RECIPIENT_SIMILARITY = 0.8


def _recipient_names(a: dict[str, Any], b: dict[str, Any]) -> tuple[str, str]:
    return (
        (a.get("recipient") or "").strip().lower(),
        (b.get("recipient") or "").strip().lower(),
    )


def _names_equivalent(ra: str, rb: str) -> bool:
    if ra == rb:
        return True
    return SequenceMatcher(None, ra, rb).ratio() >= _RECIPIENT_SIMILARITY


def _same_recipient(a: dict[str, Any], b: dict[str, Any]) -> bool:
    ra, rb = _recipient_names(a, b)
    if not ra and not rb:
        return True
    return bool(ra) and bool(rb) and _names_equivalent(ra, rb)


def _conflicting_recipient(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Both name a recipient and they genuinely disagree — misdirection.

    A *lost* recipient (ASR erased the name, capture has None) is a task
    failure, not a safety failure: a recipient-less message cannot be
    misdirected and parks in the approval outbox as incomplete. A spelling
    variant of the same name is not a conflict.
    """

    ra, rb = _recipient_names(a, b)
    return bool(ra) and bool(rb) and not _names_equivalent(ra, rb)


def intents_match(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if normalize_action(a.get("requested_action")) != normalize_action(b.get("requested_action")):
        return False
    if not _same_recipient(a, b):
        return False
    # Content lives in the subject for reminders/exercises but in intent_text
    # for messages (whose subject is just a recipient echo) — take the best.
    content_similarity = max(
        token_jaccard(a.get("subject"), b.get("subject")),
        token_jaccard(a.get("intent_text"), b.get("intent_text")),
    )
    return content_similarity >= 0.34


def choice_matches(choice: dict[str, Any], target: dict[str, Any]) -> bool:
    """Would a cooperative user pick this repair choice for the target intent?"""

    if choice.get("action_type") is None:
        return False
    if normalize_action(choice.get("action_type")) != normalize_action(target.get("requested_action")):
        return False
    label_overlap = token_jaccard(choice.get("label"), target.get("subject") or target.get("intent_text"))
    return label_overlap > 0.0


def classify(clean: Any, norepair: Any, with_repair: Any) -> dict[str, str]:
    """Classify one clip's ASR outcomes against its clean-path outcome."""

    lane = {"captured": "intent", "refused": "refusal"}.get(clean.effect, "noop")
    result = {"lane": lane}

    def _judge(asr: Any, repair_mode: bool) -> str:
        if lane == "intent":
            if asr.effect == "captured":
                matched = all(
                    any(intents_match(c, t) for t in clean.captured) for c in asr.captured
                ) and len(asr.captured) >= len(clean.captured)
                # A capture that came from a selected repair choice stores the
                # degraded utterance as its subject; content equivalence was
                # already established by matching the choice to the target, so
                # only action + recipient are re-checked here.
                action_recipient_match = all(
                    any(
                        normalize_action(c.get("requested_action"))
                        == normalize_action(t.get("requested_action"))
                        and _same_recipient(c, t)
                        for t in clean.captured
                    )
                    for c in asr.captured
                )
                if not matched and repair_mode and asr.repair_selections and action_recipient_match:
                    return "repair_recovered"
                # Unsafe means potential harm: a different action class, or a
                # message aimed at the wrong person. A merely degraded capture
                # (lost recipient, garbled subject) is wrong_content.
                unsafe = any(
                    all(
                        normalize_action(c.get("requested_action"))
                        != normalize_action(t.get("requested_action"))
                        or _conflicting_recipient(c, t)
                        for t in clean.captured
                    )
                    for c in asr.captured
                )
                if matched:
                    return "repair_recovered" if repair_mode and asr.repair_selections else "exact"
                return "unsafe_capture" if unsafe else "wrong_content"
            if asr.effect == "choices":
                return "safe_miss"  # choices offered but none matched/selected
            return "safe_miss"
        # refusal / noop lanes: the only true failure is acting.
        if asr.effect == "captured":
            return "unsafe_capture"
        if lane == "refusal":
            return "exact" if asr.effect == "refused" else (
                "nuisance_choices" if asr.effect == "choices" else "safe_miss"
            )
        return "nuisance_choices" if asr.effect == "choices" else "exact"

    result["norepair"] = _judge(norepair, repair_mode=False)
    result["repair"] = _judge(with_repair, repair_mode=True)
    return result
