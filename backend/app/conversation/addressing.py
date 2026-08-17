"""Directed-vs-ambient decision for live voice windows (EXP-001 slice 1).

The talk loop previously treated every transcription window as addressed to
Parker, so ambient conversation and TV audio drew nuisance repair choices —
the known live defect from the first desktop dogfood run. This module decides,
per utterance and *before* ``TextSession`` routing, whether Parker was
addressed at all. ``TextSession`` already honors the decision: an utterance
with ``addressed_to_parker=False`` silently no-ops while preserving pending
repair/confirmation state.

Modes (family-administered via ``PARKER_ADDRESS_MODE``; the user never
configures anything):

- ``open`` (default): every utterance is directed — the historical behavior.
  Right for push-to-talk desk demos, ``make talk``, and typed input.
- ``wake``: an utterance is directed only when the wake name
  (``PARKER_WAKE_NAME``) appears in it, or while Parker is mid-exchange
  (numbered choices or a yes/no offer pending), so replies like "yes" or "1"
  never require the name. Everything else is ambient and silently no-ops.
  Right for an always-on living-room deployment with a TV in mic range.

The asymmetry that shaped this: false-directed errors (TV speech drawing
choices) burn the whole household's trust in week one, while false-ambient
errors make the user repeat himself — costly for effortful speech. ``wake``
therefore holds a mid-exchange grace window: the wake-name burden applies only
to *starting* an exchange, never to continuing one.
"""

from __future__ import annotations

import re

from app.conversation.textloop import UtteranceContext

ADDRESS_MODE_OPEN = "open"
ADDRESS_MODE_WAKE = "wake"
_KNOWN_MODES = {ADDRESS_MODE_OPEN, ADDRESS_MODE_WAKE}

DEFAULT_WAKE_NAME = "parker"

# Context sources, auditable per utterance (the outcome layer counts on these).
SOURCE_OPEN_MODE = "open_mode"
SOURCE_PENDING_REPLY = "pending_reply"
SOURCE_WAKE_NAME = "wake_name"
SOURCE_NO_WAKE_SIGNAL = "no_wake_signal"


def normalized_address_mode(raw: str | None) -> str:
    """Collapse a configured mode to a known value.

    Unknown values fall back to ``open`` — the predictable historical
    behavior — rather than silently discarding the user's speech. The talk
    loop surfaces the fallback at startup via ``describe_address_config`` so
    a typo'd mode is visible, never silent.
    """

    mode = (raw or "").strip().lower()
    return mode if mode in _KNOWN_MODES else ADDRESS_MODE_OPEN


def resolved_wake_name(raw: str | None) -> str:
    """Sanitize the configured wake name into a matchable form.

    A trailing "!" or stray quotes in config would compile into a pattern
    that can never match (a word boundary after punctuation), leaving wake
    mode silently deaf. Strip everything but letters, digits, apostrophes,
    and internal spaces; an empty result falls back to the default.
    """

    name = re.sub(r"[^a-z0-9' ]+", " ", (raw or "").lower())
    name = re.sub(r"\s+", " ", name).strip(" '")
    return name or DEFAULT_WAKE_NAME


def describe_address_config(raw_mode: str | None, raw_name: str | None) -> str:
    """One human-readable startup line — misconfiguration must be visible."""

    mode = normalized_address_mode(raw_mode)
    name = resolved_wake_name(raw_name)
    raw = (raw_mode or "").strip().lower()
    if raw and raw not in _KNOWN_MODES:
        return (
            f"Address mode: open (unrecognized PARKER_ADDRESS_MODE={raw_mode!r} — "
            f'expected "open" or "wake").'
        )
    if mode == ADDRESS_MODE_WAKE:
        return (
            f'Address mode: wake — say "{name}" to get Parker\'s attention; '
            "replies to Parker's own questions don't need it."
        )
    return "Address mode: open — every utterance is treated as directed at Parker."


def _wake_pattern(wake_name: str) -> re.Pattern[str]:
    return re.compile(rf"\b{re.escape(wake_name)}\b", re.IGNORECASE)


# Leading fillers effortful speech commonly puts before the name; stripped
# together with the name so "Uh, Parker, remind me…" parses as a clean command.
_LEAD_FILLERS = r"(?:hey|ok|okay|uh|um|so|hi|please)"


def _strip_leading_wake(utterance: str, wake_name: str) -> str:
    """Remove a leading "hey parker," style prefix so parsers see the command.

    Tolerates repeated fillers and a repeated or possessive-ASR'd name
    ("Parker Parker remind…", "Parker's remind…"). Only a *leading* mention
    is stripped; a mid-utterance mention ("what day is it, Parker?") routes
    verbatim — rewriting the middle of an effortful utterance risks mangling
    it.
    """

    lead = re.compile(
        rf"^\s*(?:{_LEAD_FILLERS}[\s,]+)*(?:{re.escape(wake_name)}(?:'s)?\b[\s,.!?:;-]*)+",
        re.IGNORECASE,
    )
    return lead.sub("", utterance, count=1)


def decide_utterance_context(
    utterance: str,
    *,
    awaiting_reply: bool,
    mode: str | None,
    wake_name: str | None = None,
) -> tuple[UtteranceContext, str]:
    """Decide whether one live utterance was addressed to Parker.

    Returns ``(context, routed_text)``. ``routed_text`` equals the input
    except when a leading wake-name prefix was stripped; a bare wake name
    routes as empty text, which the session answers with "I'm listening."
    """

    resolved_mode = normalized_address_mode(mode)
    name = resolved_wake_name(wake_name)

    if resolved_mode == ADDRESS_MODE_OPEN:
        return UtteranceContext(addressed_to_parker=True, source=SOURCE_OPEN_MODE), utterance

    if awaiting_reply:
        # Mid-exchange grace: Parker asked something; the reply is directed.
        return UtteranceContext(addressed_to_parker=True, source=SOURCE_PENDING_REPLY), utterance

    if _wake_pattern(name).search(utterance):
        return (
            UtteranceContext(addressed_to_parker=True, source=SOURCE_WAKE_NAME),
            _strip_leading_wake(utterance, name),
        )

    return (
        UtteranceContext(
            addressed_to_parker=False,
            source=SOURCE_NO_WAKE_SIGNAL,
            note=f"mode={resolved_mode}",
        ),
        utterance,
    )


def gate_window(
    lines: list[str],
    *,
    awaiting_reply: bool,
    mode: str | None,
    wake_name: str | None = None,
) -> list[tuple[str, UtteranceContext, str]]:
    """Gate one recording window's utterance lines as a unit.

    ASR post-processing splits a window's transcript on sentence/command
    boundaries (``app.voice.transcribe.split_utterances``), so one spoken
    address like "Parker, remind me to stretch" can arrive as two lines —
    ``["Parker", "remind me to stretch"]``. Deciding per line would mark the
    command ambient. The window is therefore the decision unit: in ``wake``
    mode it is directed if a reply is pending or the wake name appears in
    *any* line. People pause mid-address, and a trailing "…, Parker" is a
    real speech pattern; the window was end-pointed by the speaker's own
    voice, so directing the whole window favors not missing him over
    filtering the odd co-captured fragment.

    Returns ``(original_line, context, routed_text)`` triples. A line that is
    only the wake name is dropped when the window carries other directed
    content — routing it would interject "I'm listening." mid-command — and
    kept when it is all the window contains.
    """

    resolved_mode = normalized_address_mode(mode)
    name = resolved_wake_name(wake_name)

    if resolved_mode == ADDRESS_MODE_OPEN:
        context = UtteranceContext(addressed_to_parker=True, source=SOURCE_OPEN_MODE)
        return [(line, context, line) for line in lines]

    pattern = _wake_pattern(name)
    directed = awaiting_reply or any(pattern.search(line) for line in lines)
    if not directed:
        context = UtteranceContext(
            addressed_to_parker=False,
            source=SOURCE_NO_WAKE_SIGNAL,
            note=f"mode={resolved_mode}",
        )
        return [(line, context, line) for line in lines]

    source = SOURCE_PENDING_REPLY if awaiting_reply else SOURCE_WAKE_NAME
    context = UtteranceContext(addressed_to_parker=True, source=source)
    routed = [(line, context, _strip_leading_wake(line, name)) for line in lines]
    keep = [row for row in routed if row[2].strip()]
    # Every line was a bare wake-name fragment: keep one so Parker responds
    # ("I'm listening.") instead of ignoring a direct address.
    return keep if keep else routed[:1]
