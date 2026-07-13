"""Local text loop: a transcript-capture seam over the real tool layer.

Run with ``make repl``. Each typed line is treated as an utterance and
routed deterministically (keyword rules, no model, no audio) through the
same tools a voice agent would call: ``offer_repair_choices`` for
ambiguous intents and ``capture_intent`` for clear ones. Captured intents
flow through the normal resolve → stage → confirm pipeline.

Confirmation is conversational (capability trust model, 2026-07): after a
tick stages an action from this session, ``offer_pending_confirmation``
asks the patient directly, and a spoken "yes" confirms AND executes it
through the same pipeline functions (``confirmed_by="patient"``, recorded)
— within an admin-enabled capability the patient's own confirmation is the
only gate. "No" cancels. Anything else defers: the action stays staged for
the review page, never silently acted on.

Safety mirrors the action policy: medication-change requests are refused,
purchases are routed to human approval, prohibited tiers are never
confirmable by anyone, and messages release only inside the admin's
family-contact allowlist (otherwise they queue for caregiver approval).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.brain.adapter import BrainAdapter, BrainContext, Message
from app.conversation.repair import suggest_repair_candidates
from app.conversation.repair_events import record_repair_event
from app.conversation.tools import execute_tool
from app.parker.screen import (
    AWAITING_CHOICE,
    AWAITING_NOTHING,
    AWAITING_YES_NO,
    publish_screen_state,
)

# Bounded brain-lane conversation memory: enough for follow-ups
# ("what about Saturday?"), small enough to stay cheap and forgetful.
BRAIN_HISTORY_MAX_TURNS = 12


@dataclass(frozen=True)
class UtteranceContext:
    """Routing context supplied by the audio/wake layer for one utterance.

    The default preserves the historical text-loop contract: a typed line or a
    talk-loop transcript is assumed to be addressed to Parker. Audio evals and a
    future wake-word/VAD layer can pass ``addressed_to_parker=False`` for ambient
    room speech. That produces a silent no-op before repair choices, captures, or
    confirmation handling, so Parker does not turn background monologue into
    nuisance actions while still leaving the existing directed-command path
    unchanged.
    """

    addressed_to_parker: bool = True
    source: str = "assumed_addressed"
    note: str | None = None


ANSWER_STUB_SPEECH = (
    "I'd look that up and summarize it for you. "
    "(Research answers are stubbed in the local text loop.)"
)

# Includes the Parkinson's medication vocabulary the pilot user actually
# says, so "double my levodopa" refuses deterministically BEFORE any model.
MED_WORDS = (
    "pill",
    "pills",
    "medication",
    "meds",
    "dose",
    "levodopa",
    "levadopa",
    "carbidopa",
    "sinemet",
    "madopar",
)
MED_CHANGE_PHRASES = ("should i", "take half", "in half", "skip", "double", "stop taking")
MEDICAL_ADVICE_WORDS = (
    "diagnose",
    "diagnosis",
    "treatment",
    "treat",
    "antibiotic",
    "symptom",
    "tremor",
    "shaking",
)
MEDICAL_ADVICE_PHRASES = (
    "what treatment",
    "which treatment",
    "should i try",
    "do you think",
    "is getting worse",
    "does this mean",
)
MEDICAL_INSTRUCTION_MARKERS = (
    "antibiotic",
    "azithromycin",
    "dolo",
    "pantop",
    "thyroxine",
    "thiroxine",
    "thyroxene",
    "tablet",
    "mcg",
    " mg",
    "dengue",
    "antigen test",
)
MEDICAL_INSTRUCTION_PHRASES = (
    "please have",
    "should take",
    "patient should take",
    "recommend to give",
    "recommend him to do",
    "recommend him",
    "i recommend",
    "want to give",
    "prescribe",
    "suspecting",
    "patient has",
    "for the medicine",
    "take proper bed rest",
)
EMERGENCY_WORDS = ("911", "emergency", "ambulance", "can't breathe", "cant breathe", "chest pain", "fell")
EMERGENCY_SUBSTITUTION_PHRASES = (
    "instead of calling",
    "handle it instead",
    "can't get up",
    "cant get up",
    "pretend you're the",
    "pretend to be the",
)
PRIVATE_DISCLOSURE_WORDS = (
    "password",
    "passcode",
    "bank password",
    "credit card",
    "ssn",
    "social security",
    "private key",
    "api key",
    "token",
)
PURCHASE_PHRASES = ("order", "buy", "purchase", "card on file")
TICKET_LOOKUP_PATTERNS = (
    r"\bfind\b",
    r"\blook\s+up\b",
    r"\blookup\b",
    r"\bsearch\b",
    r"\bshow\b",
    r"\boptions?\b",
    r"\bavailable\b",
    r"\bavailability\b",
    r"\bprices?\b",
    r"\bcosts?\b",
    r"\bhow\s+much\b",
    r"\bgoogle\b",
)
TICKET_ACQUISITION_PATTERNS = (
    r"\bwant\s+(?:the\s+)?tickets?\b",
    r"\bneed\s+(?:a\s+|the\s+)?tickets?\b",
    r"\bget\s+(?:me\s+)?(?:a\s+|the\s+)?tickets?\b",
    r"\b(?:book|buy|purchase|order)\s+(?:me\s+)?(?:a\s+|the\s+)?(?:\w+\s+){0,3}tickets?\b",
    # Synthetic audio in the 2026-07-10 ticket-boundary lane produced
    # ``Buy me tickets`` -> ``by me tickets`` in both tiny/base ASR. This is
    # deliberately scoped to an explicit ticket noun, not a broad ``by me``
    # runtime guard.
    r"\bby\s+me\s+tickets?\b",
)
TICKET_NEGATED_ACQUISITION_PATTERNS = (
    r"\b(?:do\s+not|don't|dont)\s+(?:want|need)\s+(?:a\s+|the\s+)?tickets?\b",
    r"\b(?:do\s+not|don't|dont)\s+(?:book|buy|purchase|order|get)\s+"
    r"(?:me\s+)?(?:a\s+|the\s+)?(?:\w+\s+){0,3}tickets?\b",
)
FINANCIAL_ACCOUNT_PHRASES = (
    "account balance",
    "bank balance",
    "bank account",
    "joint account",
    "current account",
    "reconcile my account",
    "reconcile a my account",
)
# MInDS-14 public audio exposed a safety-relevant ASR erasure:
# "joint account" -> "joining town". Parker cannot see the source label at
# runtime, so keep this narrow to the unsupported-finance request frame rather
# than treating every mention of a town as financial.
FINANCIAL_ACCOUNT_ASR_ERASURE_PHRASES = (
    "setting up a joining town",
    "set up a joining town",
    "setup a joining town",
    "join the count",
    "joint to hell with my wife",
)
VAGUE_PHRASES = ("you know", "the thing", "the one with", "no the other")
CHANGED_MIND_PREFIXES = (
    "wait",
    "no",
    "nope",
    "actually",
    "change that",
    "make it",
    "make that",
    "scratch that",
    "cancel",
    "cancel that",
    "stop",
    "hold on",
)
MESSAGE_PATTERN = re.compile(r"^(?:tell|message|text)\s+([A-Za-z]+)\s+(.+)$", re.IGNORECASE)
SEND_PATTERN = re.compile(r"^send\s+([A-Za-z]+)\s+(?:a\s+message\s+)?(?:that\s+|saying\s+)?(.+)$", re.IGNORECASE)
REMIND_PATTERN = re.compile(r"^remind\s+(?:me|us|him|her|dad|mom)?\s*(?:to\s+)?(.+)$", re.IGNORECASE)
EXERCISE_PATTERN = re.compile(
    r"^(?:start|begin|do|practice)\s+(?:a\s+|an\s+|the\s+)?(?:(speech|voice|movement|stretching|cognitive)\s+)?exercise(?:\s+(?:for|about|called)\s+(.+))?$",
    re.IGNORECASE,
)
TRAILING_TIMING_PATTERN = re.compile(
    r"\s+(?:now|today|tomorrow|tonight|this\s+(?:morning|afternoon|evening)|after\s+.+|before\s+.+|in\s+.+|at\s+.+)$",
    re.IGNORECASE,
)
CANCEL_ONLY_REVISION_FRAGMENTS = {
    "",
    "it",
    "that",
    "this",
    "cancel",
    "stop",
    "stop it",
    "stop that",
    "message",
    "the message",
    "that message",
    "this message",
    "draft",
    "the draft",
    "that draft",
    "this draft",
    "the note",
    "that note",
    "this note",
}
CONTENTLESS_MESSAGE_BODIES = {
    "",
    "it",
    "that",
    "this",
    "yet",
    "not yet",
    "later",
    "now",
    "today",
    "tomorrow",
    "tonight",
    "please",
}
# Spoken yes/no vocabulary for the conversational confirmation seam. Only
# consulted while a staged action is awaiting the patient's confirmation;
# bare control words with no pending context keep their no-op handling.
CONFIRM_YES_PHRASES = {
    "yes",
    "yeah",
    "yep",
    "yes please",
    "go",
    "go ahead",
    "okay",
    "ok",
    "do it",
    "sure",
    "please do",
}
CONFIRM_NO_PHRASES = {
    "no",
    "nope",
    "nah",
    "not now",
    "cancel",
    "cancel that",
    "stop",
    "don't",
    "dont",
    "no thanks",
    "no thank you",
}

# Natural confirmations compound ("Yes, go ahead", "Okay, do it") — heard
# verbatim from the first desktop-app install, where "Yes, go ahead."
# fell through to repair choices. A short utterance made ONLY of
# affirmative tokens, led by an affirmative, is a yes; the mirrored rule
# holds for no. Negation tokens are absent from the yes vocabulary (and
# vice versa), so the two can never cross-match; anything mixed
# ("yes but change it") stays a deferral, which is the safe default.
_CONFIRM_YES_TOKENS = {
    "yes", "yeah", "yep", "okay", "ok", "sure", "please",
    "go", "ahead", "do", "it", "fine", "alright",
}
_CONFIRM_YES_LEADS = {"yes", "yeah", "yep", "okay", "ok", "sure", "go", "do", "alright", "fine"}
_CONFIRM_NO_TOKENS = {
    "no", "nope", "nah", "not", "now", "cancel", "stop", "don't", "dont",
    "thanks", "thank", "you", "never", "mind", "please", "that",
}
_CONFIRM_NO_LEADS = {"no", "nope", "nah", "cancel", "stop", "don't", "dont", "not", "never"}


def _confirmation_reply_kind(normalized: str) -> str | None:
    """'yes' | 'no' | None for a normalized utterance during confirmation."""

    if normalized in CONFIRM_YES_PHRASES:
        return "yes"
    if normalized in CONFIRM_NO_PHRASES:
        return "no"
    tokens = normalized.split()
    if not 1 <= len(tokens) <= 4:
        return None
    if tokens[0] in _CONFIRM_NO_LEADS and all(t in _CONFIRM_NO_TOKENS for t in tokens):
        return "no"
    if tokens[0] in _CONFIRM_YES_LEADS and all(t in _CONFIRM_YES_TOKENS for t in tokens):
        return "yes"
    return None

# Spoken dismissal while repair choices are pending: equivalent to picking
# "none of these" without knowing its number. Kept small and exact-match —
# anything else while choices are pending is a digit selection, a
# clearly-new utterance (which escapes to normal routing), or a garbled
# selection attempt (re-prompt).
DISMISS_CHOICE_PHRASES = CONFIRM_NO_PHRASES | {
    "none",
    "none of these",
    "none of those",
    "never mind",
    "nevermind",
    "cancel that message",
    "cancel this message",
}

NO_CONTEXT_CONTROL_RESPONSES = {
    "yes": "I heard yes, but there isn't anything waiting for confirmation.",
    "yeah": "I heard yes, but there isn't anything waiting for confirmation.",
    "yep": "I heard yes, but there isn't anything waiting for confirmation.",
    "no": "Okay — I won't do anything.",
    "nope": "Okay — I won't do anything.",
    "nah": "Okay — I won't do anything.",
    "go": "I heard go, but there isn't anything waiting to start.",
    "stop": "Okay — stopping here. Nothing will run unless you ask again.",
    "wait": "Okay — waiting. Nothing will run unless you ask again.",
    "hold on": "Okay — waiting. Nothing will run unless you ask again.",
    "cancel": "There isn't a current local draft to cancel.",
    "cancel that": "There isn't a current local draft or message to cancel.",
    "cancel that message": "There isn't a current local message to cancel.",
    "cancel this message": "There isn't a current local message to cancel.",
    "that message": "I heard a reference to a message, but there isn't a local message waiting here.",
    "this message": "I heard a reference to a message, but there isn't a local message waiting here.",
    "zero": "I heard zero, but there isn't a numbered choice waiting.",
    "0": "I heard zero, but there isn't a numbered choice waiting.",
    "up": "I heard up, but there isn't a device, choice, or local action waiting.",
    "down": "I heard down, but there isn't a device, choice, or local action waiting.",
    "left": "I heard left, but there isn't a device, choice, or local action waiting.",
    "right": "I heard right, but there isn't a device, choice, or local action waiting.",
    "on": "I heard on, but there isn't a device or local action waiting.",
    "off": "I heard off, but there isn't a device or local action waiting.",
    "of": "I heard something like off, but there isn't a device or local action waiting.",
    "oh no": "I heard concern, but I won't start or stop anything without a clearer request.",
    "oh my god": "I heard concern, but I won't start or stop anything without a clearer request.",
}


def _build_model_client() -> "Any | None":
    """Instantiate an Anthropic client from settings if a key is configured.

    Returns None when the key is empty so callers fall through to the
    hardcoded repair-choice fallback without any import cost.
    """
    from app.config import settings

    if not settings.anthropic_api_key:
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=settings.anthropic_api_key)
    except Exception:  # noqa: BLE001
        return None


def _looks_like_changed_mind(lowered: str) -> bool:
    stripped = re.sub(r"[,.!?]+", " ", lowered).strip()
    stripped = re.sub(r"\s+", " ", stripped)
    return any(stripped == prefix or stripped.startswith(f"{prefix} ") for prefix in CHANGED_MIND_PREFIXES)


def _looks_like_emergency_substitution(lowered: str) -> bool:
    return any(word in lowered for word in EMERGENCY_WORDS) and any(
        phrase in lowered for phrase in EMERGENCY_SUBSTITUTION_PHRASES
    )


def _looks_like_sensitive_private_disclosure(lowered: str) -> bool:
    return any(word in lowered for word in PRIVATE_DISCLOSURE_WORDS)


def _looks_like_financial_account_request(lowered: str) -> bool:
    return any(phrase in lowered for phrase in FINANCIAL_ACCOUNT_PHRASES) or any(
        phrase in lowered for phrase in FINANCIAL_ACCOUNT_ASR_ERASURE_PHRASES
    )


def _looks_like_ticket_acquisition(normalized: str) -> bool:
    return any(re.search(pattern, normalized) for pattern in TICKET_ACQUISITION_PATTERNS)


def _without_negated_ticket_acquisition(normalized: str) -> tuple[str, bool]:
    """Remove explicit ticket negations before looking for positive acquisition.

    Clearly synthetic audio preserved ``don't buy tickets`` and ``don't want
    tickets`` verbatim. Those clauses must not be reinterpreted as affirmative
    purchase intent. Removing only the narrow verb+ticket span keeps a later
    positive clause (``don't buy these tickets; buy the Sunday tickets``)
    visible to the conservative human-approval gate.
    """

    remaining = normalized
    for pattern in TICKET_NEGATED_ACQUISITION_PATTERNS:
        remaining = re.sub(pattern, " ", remaining)
    remaining = re.sub(r"\s+", " ", remaining).strip()
    return remaining, remaining != normalized


def _looks_like_purchase_after_ticket_negation(normalized: str) -> bool:
    remaining, _ = _without_negated_ticket_acquisition(normalized)
    return any(phrase in remaining for phrase in PURCHASE_PHRASES)


def _ticket_request_response(utterance: str) -> dict[str, Any] | None:
    """Separate read-only ticket lookup from ticket acquisition/purchase.

    The public SLURP clip ``I want tickets to the sold out concert`` survived
    ASR as ``I want tickets ... consequences of the night`` and used to fall
    into generic reminder/message choices. Ticket lookup is informational and
    read-only; acquisition is held at the explicit human-approval boundary.
    Neither route captures an intent or enters checkout.
    """

    normalized = re.sub(r"[,.!?]+", " ", utterance).strip().lower()
    normalized = re.sub(r"\s+", " ", normalized)
    if not re.search(r"\btickets?\b", normalized):
        return None

    # Preserve ordinary local reminders/messages *about* ticket information.
    # Acquisition wording ("remind me to buy tickets") remains held below.
    direct_local_intent = (
        MESSAGE_PATTERN.match(utterance)
        or SEND_PATTERN.match(utterance)
        or REMIND_PATTERN.match(utterance)
        or EXERCISE_PATTERN.match(utterance)
    )
    positive_text, has_negated_acquisition = _without_negated_ticket_acquisition(normalized)
    acquisition = _looks_like_ticket_acquisition(positive_text)
    if direct_local_intent and not acquisition:
        return None
    if acquisition:
        return {
            "kind": "needs_human_approval",
            "action_type": "purchase",
            "purchase_permitted": False,
            "speech": (
                "I can look up ticket options, but I won't book or purchase tickets. "
                "A family member must review and approve any purchase outside Parker."
            ),
        }
    if any(re.search(pattern, positive_text) for pattern in TICKET_LOOKUP_PATTERNS):
        return {
            "kind": "answer",
            "action_type": "item_search",
            "purchase_permitted": False,
            "speech": (
                "I can help prepare a read-only ticket search. The local loop does not fetch live results, "
                "and I won't book or purchase anything."
            ),
        }
    if has_negated_acquisition:
        return {
            "kind": "noop",
            "action_type": None,
            "purchase_permitted": False,
            "speech": "I heard that you don't want tickets. I won't create or purchase anything.",
        }
    return None


def _control_negation_response(utterance: str) -> dict[str, Any] | None:
    """Preserve no-action control phrases such as "don't go yet".

    The audio Autodata lane produced a synthetic control phrase where Parker's
    weak path offered generic reminder/message choices for "No, don't go yet".
    In a no-context voice loop this is a stop/hold control, not a request to
    create a new action.
    """

    normalized = re.sub(r"[,.!?]+", " ", utterance).strip().lower()
    normalized = re.sub(r"\s+", " ", normalized)
    if re.search(r"\b(?:no\s+)?(?:do\s+not|don't|dont)\s+go(?:\s+yet)?\b", normalized):
        return {
            "kind": "noop",
            "speech": "I heard not to go yet. I won't start or continue anything from that.",
        }
    return None


def _looks_like_media_request_question(lowered: str) -> bool:
    """Detect question-shaped ASR for a clipped local media request.

    Low-volume synthetic Parker audio turned "Play a YouTube stretching video"
    into "Why you YouTube stretching video?". That should get a specific repair
    prompt, not the generic research-answer stub.
    """

    normalized = re.sub(r"[,.!?]+", " ", lowered).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    has_media = "youtube" in normalized or "you tube" in normalized or "new tube" in normalized
    return has_media and normalized.startswith(("why you ", "why youtube", "why you youtube"))


def _looks_like_answer_or_conversation_request(lowered: str) -> bool:
    """No-side-effect answer/conversation cues after Parker is addressed.

    SLURP wake-context audio surfaced utterances such as "let's have a chat" and
    "tell me more about my events". In a wake-confirmed interaction these are
    informational/conversational, not reminder/message repair candidates. Keep
    this intentionally narrow so clipped commands still fall through to repair.
    """

    normalized = lowered.replace("let's", "lets")
    normalized = re.sub(r"[,.!?]+", " ", normalized).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.startswith(
        (
            "lets have a chat",
            "have a chat",
            "chat with me",
            "talk with me",
            "tell me about ",
            "tell me more",
            "can you tell me ",
            "could you tell me ",
            "would you tell me ",
            "please give me information on ",
            "give me information on ",
            "please give me info on ",
            "give me info on ",
            "find me info on ",
            "i want to know more about ",
            "i want to know about ",
            "i would like to know ",
            "describe ",
            "explain ",
        )
    )


def _repetitive_asr_hallucination_response(utterance: str) -> dict[str, Any] | None:
    """No-op long repetitive ASR from no-transcript dysarthria stress audio.

    Public no-transcript dysarthria rows can make Whisper emit fluent-looking
    loops such as "I'll be happy" dozens of times. Those are useful stress
    signals, but Parker should not turn them into reminder/message choices.
    """

    normalized = re.sub(r"[,.!?]+", " ", utterance).strip().lower()
    normalized = re.sub(r"\s+", " ", normalized)
    words = normalized.split()
    if len(words) < 18:
        return None
    if len(set(words)) / max(len(words), 1) <= 0.25:
        return {
            "kind": "noop",
            "speech": (
                "I heard unclear repeated audio, so I won't turn it into a reminder or message. "
                "Please try again or use the screen choices."
            ),
        }
    for ngram_size in (1, 2, 3, 4):
        grams = [tuple(words[i : i + ngram_size]) for i in range(0, len(words) - ngram_size + 1)]
        if not grams:
            continue
        most_common_count = max(grams.count(gram) for gram in set(grams))
        if most_common_count >= 5 and (most_common_count * ngram_size) / len(words) >= 0.45:
            return {
                "kind": "noop",
                "speech": (
                    "I heard unclear repeated audio, so I won't turn it into a reminder or message. "
                    "Please try again or use the screen choices."
                ),
            }
    return None


_NUMBER_WORDS = {
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen", "twenty", "thirty", "forty",
    "fifty", "sixty", "seventy", "eighty", "ninety", "hundred",
}


def _counting_sequence_response(utterance: str) -> dict[str, Any] | None:
    """No-op number/counting sequences — exercise audio, not commands.

    Counting is everyday speech-therapy content ("81, 82, …" warm-ups,
    "one, two, three, four" pacing) and local ASR renders spoken numbers as
    digits. Observed in the web-private local validation lane. A counting
    line is never a command and never a repair-choice selection — without
    this guard, a digit-rendered counting fragment spoken while repair
    choices are pending is one ASR segmentation quirk away from selecting
    one (see benchmark/data/private_audio_pattern_notes_v0.json).
    """

    words = re.sub(r"[,.!?\-]+", " ", utterance).lower().split()
    if len(words) < 3:
        return None
    pure_numbers = sum(1 for w in words if w.isdigit() or w in _NUMBER_WORDS)
    if pure_numbers < 3:
        return None
    if any(not (w.isdigit() or w in _NUMBER_WORDS or w == "and") for w in words):
        return None
    return {
        "kind": "noop",
        "speech": "Sounds like counting practice — I'll stay out of the way.",
    }


def _looks_like_new_directed_utterance(utterance: str) -> bool:
    """A clearly-new command or question spoken while repair choices pend.

    The web-private validation lane showed ambient speech constantly draws
    generic repair choices; selection mode then swallowed every following
    utterance — including the user's actual next command — behind "Just
    say the number". For a speaker whose retries are effortful, eating the
    retry is the worst failure shape. Clear command/exercise/question
    forms escape selection mode and route normally; anything else is still
    treated as a garbled selection attempt.
    """

    if MESSAGE_PATTERN.match(utterance) or SEND_PATTERN.match(utterance):
        return True
    if REMIND_PATTERN.match(utterance) or EXERCISE_PATTERN.match(utterance):
        return True
    return utterance.lower().startswith(("what", "when", "where", "who", "how", "why"))


def _message_body_needs_clarification(body: str) -> bool:
    """Return true when ASR likely preserved a message cue but lost the body.

    This guards a real audio failure mode from the Autodata lane: a clipped
    negated utterance like "Do not message Sarah yet" can become
    "message Sarah yet". That must not create even a local draft; Parker should
    ask for the actual message body instead.
    """

    normalized = re.sub(r"[,.!?]+", " ", body).strip().lower()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized in CONTENTLESS_MESSAGE_BODIES or normalized.startswith(("not yet ", "later "))


def _no_context_control_response(utterance: str) -> dict[str, Any] | None:
    """Acknowledge standalone control words without inventing an action.

    Public command corpora and real voice sessions produce short one-word
    hypotheses such as "no", "go", or "stop". When Parker has no pending repair
    choice, draft, or local outbox item, those words are context controls rather
    than reminder/message intents. Do not fall through to generic repair choices.
    """

    normalized = re.sub(r"[,.!?]+", " ", utterance).strip().lower()
    normalized = re.sub(r"\s+", " ", normalized)
    speech = NO_CONTEXT_CONTROL_RESPONSES.get(normalized)
    if speech is None:
        return None
    return {"kind": "noop", "speech": speech}


def _device_control_without_context_response(utterance: str) -> dict[str, Any] | None:
    """Clarify multi-word device/media/app controls when no approved context exists.

    Public Fluent Speech Commands audio surfaced settings controls such as
    ``set the language`` that were falling through to generic reminder/message
    choices. In v0, those are real action requests but Parker has no approved
    active room, TV, app, or device context, so the safest response is explicit
    no-action/context-required instead of a generic repair prompt.
    """

    normalized = re.sub(r"[,.!?]+", " ", utterance).strip().lower()
    normalized = re.sub(r"\s+", " ", normalized)
    if not normalized:
        return None
    control_words = (
        "turn",
        "switch",
        "set",
        "change",
        "close",
        "open",
        "increase",
        "decrease",
        "raise",
        "lower",
        "volume",
    )
    device_words = (
        "volume",
        "temperature",
        "heating",
        "heat",
        "lights",
        "light",
        "tv",
        "television",
        "language",
        "settings",
        "setting",
        "app",
        "application",
        "phone",
        "speakerphone",
        "speaker phone",
        "bedroom",
        "bathroom",
        "washroom",
    )
    if any(word in normalized for word in control_words) and any(word in normalized for word in device_words):
        return {
            "kind": "context_required",
            "speech": (
                "I heard a device or media control, but there isn't an approved TV, room, "
                "app, or device context waiting here. I won't change anything without that context."
            ),
        }
    return None


def _looks_like_medical_advice(lowered: str) -> bool:
    return any(word in lowered for word in MEDICAL_ADVICE_WORDS) and any(
        phrase in lowered for phrase in MEDICAL_ADVICE_PHRASES
    )


def _looks_like_medical_instruction_dictation(lowered: str) -> bool:
    """Detect no-context medical dictation/instruction ASR as a safety boundary.

    The audio Autodata lane sampled public medical-ASR audio where source
    transcripts and Whisper output contained dosage, drug, diagnosis-like, and
    treatment-instruction language. In a generic Parker voice loop, those should
    not fall through to reminder/message repair choices. Keep the rule anchored
    to medical markers plus directive/context phrases so ordinary appointment
    notes can still be requested explicitly.
    """

    normalized = re.sub(r"[,.!?$]+", " ", lowered).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    has_marker = any(marker in normalized for marker in MEDICAL_INSTRUCTION_MARKERS)
    has_directive = any(phrase in normalized for phrase in MEDICAL_INSTRUCTION_PHRASES)
    has_dosage = bool(re.search(r"\b\d+\s*(?:mg|mcg|times?\s+in\s+a\s+day|days?)\b", normalized))
    return has_marker and (has_directive or has_dosage)


def _extract_revision_fragment(utterance: str) -> str:
    fragment = utterance.strip().strip(" .!?")
    fragment = re.sub(r"^(?:wait|hold on)[\s,]+", "", fragment, flags=re.IGNORECASE)
    fragment = re.sub(r"^(?:no|nope)[\s,]+", "", fragment, flags=re.IGNORECASE)
    fragment = re.sub(r"^(?:actually|change that|make it|make that|scratch that|cancel that|cancel|stop)[\s,]*", "", fragment, flags=re.IGNORECASE)
    fragment = re.sub(r"\s+instead$", "", fragment, flags=re.IGNORECASE)
    fragment = fragment.strip(" .!?,")
    return fragment


def _is_cancel_only_revision(fragment: str) -> bool:
    normalized = re.sub(r"[,.!?]+", " ", fragment).strip().lower()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized in CANCEL_ONLY_REVISION_FRAGMENTS


def _revised_subject(prior_subject: str, utterance: str) -> str:
    fragment = _extract_revision_fragment(utterance)
    if not fragment:
        return prior_subject
    if _looks_like_timing_fragment(fragment):
        base = TRAILING_TIMING_PATTERN.sub("", prior_subject).strip(" .!?,")
        return f"{base} {fragment}".strip()
    return fragment


def _looks_like_timing_fragment(fragment: str) -> bool:
    return fragment.lower().startswith((
        "after ",
        "before ",
        "at ",
        "around ",
        "in ",
        "tomorrow",
        "tonight",
        "today",
        "this ",
        "next ",
        "later",
    ))


def _requested_action_for_revision(requested_action: str) -> str:
    if requested_action in {"reminder", "remind"}:
        return "remind"
    if requested_action in {"family_message", "message"}:
        return "message"
    return requested_action


def _intent_text_for_revision(requested_action: str, subject: str) -> str:
    if requested_action in {"remind", "reminder"}:
        return f"Remind me to {subject}"
    return subject


_MEDIA_PROBE_PREFIXES = (
    "please play ",
    "can you play ",
    "could you play ",
    "would you play ",
    "play ",
    "i want to hear ",
    "i want to listen to ",
    "listen to ",
    "hear ",
)


def _media_probe_intent(candidate: str) -> dict[str, Any] | None:
    """Parse direct media alternates into confirmation-gated choices.

    SLURP music audio showed a useful n-best shape: the primary tiny-ASR
    transcript can corrupt a named track (``hear us now``), while a second model
    preserves the clean slot (``hear snow``). Alternates are never executed or
    routed directly, but a clean media alternate can become the first repair
    choice and carry the corrected playlist/song subject through selection.
    """

    stripped = candidate.strip().strip(" .!?")
    if not stripped:
        return None
    lowered = re.sub(r"\s+", " ", stripped.lower())
    topic: str | None = None
    for prefix in _MEDIA_PROBE_PREFIXES:
        if lowered.startswith(prefix):
            topic = stripped[len(prefix):].strip(" .!?")
            break
    if topic is None:
        return None
    topic_lower = topic.lower()
    has_direct_media_slot = (
        "playlist" in topic_lower
        or "song" in topic_lower
        or "music" in topic_lower
        or " by " in topic_lower
    )
    if not has_direct_media_slot or len(topic.split()) < 2:
        return None
    label = f"play {topic}"[:80]
    return {
        "label": label,
        "action_type": "media_playlist",
        "recipient": None,
        "subject": topic,
        "intent_text": candidate.strip(),
    }


# Safety lists an alternate ASR hypothesis must clear before it may be
# offered as a repair choice. Over-blocking is fine here: a blocked probe
# only means one fewer suggested interpretation, never a lost guard.
_PROBE_BLOCKED_PHRASES: tuple[tuple[str, ...], ...] = (
    MED_WORDS,
    MEDICAL_ADVICE_WORDS,
    MEDICAL_ADVICE_PHRASES,
    MEDICAL_INSTRUCTION_MARKERS,
    MEDICAL_INSTRUCTION_PHRASES,
    EMERGENCY_WORDS,
    EMERGENCY_SUBSTITUTION_PHRASES,
    PRIVATE_DISCLOSURE_WORDS,
    PURCHASE_PHRASES,
    FINANCIAL_ACCOUNT_PHRASES,
)


def probe_direct_intent(utterance: str) -> dict[str, Any] | None:
    """Parse an alternate ASR hypothesis against the direct-capture patterns.

    Pure and side-effect free: used to turn n-best/alternate transcripts
    into concrete repair choices ("send Sarah a message: ...") that carry
    their parsed recipient/subject, so a selection captures a complete
    intent instead of the degraded primary transcript. Any utterance that
    trips a safety phrase is never probed — alternates must not become a
    side door around the refusal guards.
    """

    candidate = utterance.strip()
    if not candidate:
        return None
    lowered = candidate.lower()
    if _looks_like_ticket_acquisition(lowered):
        return None
    if any(phrase in lowered for group in _PROBE_BLOCKED_PHRASES for phrase in group):
        return None
    match = MESSAGE_PATTERN.match(candidate) or SEND_PATTERN.match(candidate)
    if match:
        recipient, body = match.group(1), match.group(2).strip().rstrip(".")
        if recipient.lower() in ("me", "us", "myself", "him", "her", "them"):
            return None  # pronouns are never proposable message recipients
        if not body or _message_body_needs_clarification(body):
            return None
        recipient, known = canonicalize_recipient(recipient)
        if not known:
            return None  # never offer a choice toward an unrecognized name
        return {
            "label": f"send {recipient} a message: “{body[:60]}”",
            "action_type": "family_message",
            "recipient": recipient,
            "subject": f"message {recipient}",
            "intent_text": body,
        }
    match = REMIND_PATTERN.match(candidate)
    if match:
        subject = match.group(1).strip().rstrip(".")
        if not subject:
            return None
        return {
            "label": f"a reminder to {subject[:60]}",
            "action_type": "reminder",
            "recipient": None,
            "subject": subject,
            "intent_text": candidate,
        }
    match = EXERCISE_PATTERN.match(candidate)
    if match:
        exercise_type = (match.group(1) or "speech").lower()
        details = (match.group(2) or "short practice").strip().rstrip(".")
        subject = f"{exercise_type} exercise: {details}"
        return {
            "label": f"start a {subject}",
            "action_type": "exercise_start",
            "recipient": None,
            "subject": subject,
            "intent_text": candidate,
        }
    media = _media_probe_intent(candidate)
    if media is not None:
        return media
    return None


def _lexicon_names() -> list[str]:
    """Names Parker recognizes as people.

    Derived in ``app.parker.contacts`` from the admin-configured family
    contacts plus single capitalized-word ``PERSONAL_LEXICON`` entries
    ("Sarah", not "physio" or "tomato plants"). The configured spelling is
    canonical — ASR variants resolve back to it.
    """

    from app.parker.contacts import lexicon_names

    return list(lexicon_names())


# Clipped-start fragments with one high-precision reading. ASR loses the
# first word(s) of effortful speech constantly; these shapes survive it.
_REMINDER_FRAGMENT = re.compile(r"^(?:me\s+)?to\s+(\w.+)$", re.IGNORECASE)
_EXERCISE_FRAGMENT = re.compile(
    r"^(?:a|an|the)?\s*(speech|voice|movement|stretching|cognitive)\s+exercise"
    r"(?:\s+(?:for|about|called)\s+(.+))?[.]?$",
    re.IGNORECASE,
)


def fragment_candidates(utterance: str) -> list[dict[str, Any]]:
    """Reconstruct clipped-start fragments into concrete repair choices.

    "me to water the tomato plants this evening" is what's left of
    "Remind me to..." after ASR clips the start; "a speech exercise for
    the morning cards" is a clipped "Start...". Only shapes with one
    high-precision reading are reconstructed, they are safety-screened
    like every probe, and they are only ever *offered* — the user
    confirms the reconstruction by picking it.
    """

    candidate = utterance.strip().rstrip(".")
    lowered = candidate.lower()
    if _looks_like_ticket_acquisition(lowered):
        return []
    if any(phrase in lowered for group in _PROBE_BLOCKED_PHRASES for phrase in group):
        return []
    results: list[dict[str, Any]] = []
    # >= 3 content words keeps conversational filler ("to be honest") out;
    # precision over recall — a wrong offer is a nuisance question.
    match = _REMINDER_FRAGMENT.match(candidate)
    if match and len(match.group(1).split()) >= 3:
        subject = match.group(1).strip()
        results.append(
            {
                "label": f"a reminder to {subject[:60]}",
                "action_type": "reminder",
                "recipient": None,
                "subject": subject,
                "intent_text": f"remind me to {subject}",
            }
        )
    match = _EXERCISE_FRAGMENT.match(candidate)
    if match:
        exercise_type = match.group(1).lower()
        details = (match.group(2) or "short practice").strip().rstrip(".")
        subject = f"{exercise_type} exercise: {details}"
        results.append(
            {
                "label": f"start a {subject}",
                "action_type": "exercise_start",
                "recipient": None,
                "subject": subject,
                "intent_text": candidate,
            }
        )
    return results


def canonicalize_recipient(name: str) -> tuple[str, bool]:
    """Resolve an ASR-heard recipient against the personal lexicon.

    ASR mangles names ("Priya" -> "pre", "Anna" -> "an"), and a message
    captured toward a nonexistent person is the misdirection failure the
    eval flags as unsafe. With a lexicon configured, a close match snaps
    to the canonical spelling and anything unrecognized is (known=False)
    — the caller must clarify instead of capturing. Without a lexicon,
    v0 behavior is unchanged: (name, True).
    """

    names = _lexicon_names()
    if not names:
        return name, True
    lowered = name.strip().lower()
    for candidate in names:
        if candidate.lower() == lowered:
            return candidate, True
    best = max(names, key=lambda c: SequenceMatcher(None, c.lower(), lowered).ratio())
    if SequenceMatcher(None, best.lower(), lowered).ratio() >= 0.6:
        return best, True
    return name, False


def name_prefix_candidate(utterance: str) -> dict[str, Any] | None:
    """Interpret "<Name> <content>" as a possible message to that person.

    ASR on effortful speech routinely erases the leading verb: "Tell Sarah
    physio went well" arrives as "Sarah physio went well" — which is also
    exactly how people dictate messages. Gated on the personal lexicon so
    arbitrary capitalized words never trigger it, safety-screened like all
    probes, and offered as a repair choice (never captured directly): the
    person may simply be talking *about* Sarah, so one confirmation
    question is the right cost.
    """

    candidate = utterance.strip()
    lowered = candidate.lower()
    if any(phrase in lowered for group in _PROBE_BLOCKED_PHRASES for phrase in group):
        return None
    for name in _lexicon_names():
        prefix = name.lower()
        if not lowered.startswith(prefix):
            continue
        rest = candidate[len(name):].lstrip(" ,—-").rstrip(".")
        if len(rest.split()) < 2:
            continue
        return {
            "label": f"send {name} a message: “{rest[:60]}”",
            "action_type": "family_message",
            "recipient": name,  # canonical lexicon spelling
            "subject": f"message {name}",
            "intent_text": rest,
        }
    return None


class TextSession:
    """One conversational text session bound to a call log."""

    def __init__(
        self,
        db: Session,
        call_log_id: int,
        *,
        model_client: "Any | None" = None,
        brain: "BrainAdapter | None" = None,
        brain_context: "BrainContext | None" = None,
    ):
        self.db = db
        self.call_log_id = call_log_id
        self._model_client = model_client  # anthropic.Anthropic or None; None → hardcoded fallback
        # The pluggable conversational brain (docs/brain-adapters.md). None →
        # the answer lane keeps the deterministic stub. The brain is only ever
        # the fallthrough: guards, captures, and repair stay deterministic and
        # run first, and a refused utterance never reaches it.
        self._brain = brain
        self._brain_context = brain_context
        # Brain-lane history only — guarded/refused utterances are never
        # recorded, so they can't leak into a later model call.
        self._brain_history: list[Message] = []
        self._pending_choices: Optional[list[dict[str, Any]]] = None
        self._pending_utterance: Optional[str] = None
        # Staged-action id awaiting the patient's spoken yes/no, and ids
        # already offered once this session (a deferred offer is not nagged
        # again — the action stays staged for the review page instead).
        self._pending_confirmation: Optional[int] = None
        self._offered_confirmation_ids: set[int] = set()
        # Labels from the most recently offered (and user-rejected) repair choices.
        # Passed to suggest_repair_candidates on the next offer so the model can
        # generate genuinely different alternatives instead of repeating itself.
        self._prior_offered_labels: Optional[list[str]] = None
        # Alternate ASR hypotheses for the utterance currently being handled,
        # and for the utterance whose repair choices are pending selection.
        self._current_alternates: list[str] = []
        self._pending_alternates: list[str] = []

    def handle(
        self,
        text: str,
        *,
        alternates: Optional[list[str]] = None,
        context: UtteranceContext | None = None,
    ) -> dict[str, Any]:
        """Route one utterance and return {kind, speech, ...}.

        ``alternates`` are additional ASR hypotheses for the same audio
        (n-best or a second model's transcript). They are never routed
        directly — they only enrich repair choices via safe probing.

        ``context`` lets an audio/wake layer say whether this utterance was
        actually addressed to Parker. When explicitly not addressed, Parker
        stays silent and preserves any pending repair/confirmation state.

        Every exchange also updates the single-row screen state behind
        ``/parker/screen`` (the live patient screen): what was heard, what
        Parker said, and any numbered choices still waiting for a spoken
        selection. The screen is output-only; publishing must never break
        the conversation itself.
        """

        response = self._route(text, alternates=alternates, context=context)
        self._publish_screen(heard=text.strip(), response=response)
        return response

    def _publish_screen(self, *, heard: str, response: dict[str, Any]) -> None:
        if self._pending_choices is not None:
            awaiting = AWAITING_CHOICE
        elif self._pending_confirmation is not None:
            awaiting = AWAITING_YES_NO
        else:
            awaiting = AWAITING_NOTHING
        try:
            publish_screen_state(
                self.db,
                heard=heard,
                speech=response.get("speech", ""),
                kind=response.get("kind", ""),
                choices=self._pending_choices,
                awaiting=awaiting,
            )
        except Exception:  # noqa: BLE001 — a broken mirror must not kill the voice loop
            self.db.rollback()
            logging.getLogger("parker.screen").debug(
                "screen-state publish skipped", exc_info=True
            )

    def _route(
        self,
        text: str,
        *,
        alternates: Optional[list[str]] = None,
        context: UtteranceContext | None = None,
    ) -> dict[str, Any]:
        utterance = text.strip()
        self._current_alternates = [a for a in (alternates or []) if a.strip() and a.strip() != utterance]
        if not utterance:
            return {"kind": "noop", "speech": "I'm listening."}
        input_context = context or UtteranceContext()
        if input_context.addressed_to_parker is False:
            return {
                "kind": "ambient_noop",
                "speech": "",
                "addressed_to_parker": False,
                "context_source": input_context.source,
            }
        if self._pending_confirmation is not None:
            handled = self._handle_confirmation_reply(utterance)
            if handled is not None:
                return handled
            # Not a yes/no: the offer is deferred (action stays staged for
            # review) and the utterance routes normally below.
        if self._pending_choices is not None:
            handled = self._handle_selection(utterance)
            if handled is not None:
                return handled
            # A clearly-new utterance set the pending choices aside; it
            # routes normally below (mirrors the confirmation-offer seam).

        lowered = utterance.lower()
        revision = self._handle_changed_mind(utterance, lowered)
        if revision is not None:
            return revision
        control_response = _no_context_control_response(utterance)
        if control_response is not None:
            return control_response
        control_negation = _control_negation_response(utterance)
        if control_negation is not None:
            return control_negation
        counting = _counting_sequence_response(utterance)
        if counting is not None:
            return counting

        if _looks_like_emergency_substitution(lowered):
            return {
                "kind": "emergency_redirect",
                "speech": (
                    "I can't replace emergency services. If this may be urgent, "
                    "call emergency services now or ask a nearby caregiver for help. "
                    "I can flag this for family follow-up here, but I won't pretend to dispatch help."
                ),
                "flag_for_family": True,
            }
        if _looks_like_sensitive_private_disclosure(lowered):
            return {
                "kind": "refused",
                "speech": (
                    "I can't read or share private credentials or sensitive notes. "
                    "I can help write a safe message that leaves private details out."
                ),
            }
        if _looks_like_financial_account_request(lowered):
            return {
                "kind": "refused",
                "speech": (
                    "I can't access, reveal, or change bank/account information. "
                    "I can help draft a non-sensitive question for family or remind you to check the official app."
                ),
            }
        if _looks_like_medical_instruction_dictation(lowered):
            return {
                "kind": "refused",
                "speech": (
                    "I heard medical or medication instructions. I won't diagnose, recommend treatment, "
                    "change medication, or turn that into a reminder/message from unclear audio. "
                    "If you want, ask me to write an appointment note for family or your doctor."
                ),
                "flag_for_family": True,
            }
        if _looks_like_medical_advice(lowered):
            return {
                "kind": "refused",
                "speech": (
                    "I can't diagnose or recommend treatment — that's one for your doctor. "
                    "I can note what you're noticing so the family can follow up."
                ),
                "flag_for_family": True,
            }
        if any(w in lowered for w in MED_WORDS) and any(p in lowered for p in MED_CHANGE_PHRASES):
            return {
                "kind": "refused",
                "speech": (
                    "I can't help change medication — that's one for your doctor. "
                    "I can note how you're feeling so the family can follow up."
                ),
                "flag_for_family": True,
            }
        ticket_response = _ticket_request_response(utterance)
        if ticket_response is not None:
            return ticket_response
        if _looks_like_purchase_after_ticket_negation(lowered):
            return {
                "kind": "needs_human_approval",
                "action_type": "purchase",
                "purchase_permitted": False,
                "speech": (
                    "I don't buy things myself. I can look options up and ask the "
                    "family to approve a purchase."
                ),
            }
        device_control = _device_control_without_context_response(utterance)
        if device_control is not None:
            return device_control
        repeated_hallucination = _repetitive_asr_hallucination_response(utterance)
        if repeated_hallucination is not None:
            return repeated_hallucination
        if lowered.count("...") >= 2 or any(p in lowered for p in VAGUE_PHRASES):
            return self._offer_choices(utterance)

        match = MESSAGE_PATTERN.match(utterance) or SEND_PATTERN.match(utterance)
        if match and match.group(1).lower() in ("me", "us", "myself"):
            # "Tell me about the trains" asks FOR information — first-person
            # pronouns are never message recipients. Fall through to the
            # question/answer/brain lane instead of capturing a message to "me".
            match = None
        if match and match.group(1).lower() in ("him", "her", "them"):
            # A real message, but to an unresolved pronoun — ask who.
            return {
                "kind": "clarify",
                "speech": "I heard a message, but not who it's for. Who should it go to?",
            }
        if match:
            recipient, body = match.group(1), match.group(2).strip()
            recipient, known = canonicalize_recipient(recipient)
            if not known:
                return {
                    "kind": "clarify",
                    "speech": (
                        f"I heard a message, but I don't recognize the name “{recipient}”. "
                        "Who should it go to?"
                    ),
                }
            if _message_body_needs_clarification(body):
                return {
                    "kind": "clarify",
                    "speech": (
                        f"I heard a message to {recipient}, but not what to say. "
                        "I won't draft or send anything unless you tell me the message."
                    ),
                }
            return self._capture(
                intent_text=body,
                requested_action="message",
                subject=f"message {recipient}",
                recipient=recipient,
                speech=(
                    f"Got it — a message to {recipient}: “{body}”. "
                    "It will need a confirmation before it goes anywhere."
                ),
            )
        match = REMIND_PATTERN.match(utterance)
        if match:
            subject = match.group(1).strip().rstrip(".")
            return self._capture(
                intent_text=utterance,
                requested_action="remind",
                subject=subject,
                recipient=None,
                speech=f"Okay — I'll bring up “{subject}” and check with you before anything runs.",
            )
        match = EXERCISE_PATTERN.match(utterance)
        if match:
            exercise_type = (match.group(1) or "speech").lower()
            details = (match.group(2) or "short practice").strip().rstrip(".")
            subject = f"{exercise_type} exercise: {details}"
            return self._capture(
                intent_text=utterance,
                requested_action="exercise",
                subject=subject,
                recipient=None,
                speech=(
                    f"Okay — I can start “{subject}” locally. "
                    "I'll confirm before it starts."
                ),
            )
        if _looks_like_media_request_question(lowered):
            return self._offer_choices(utterance)
        if _looks_like_answer_or_conversation_request(lowered):
            return self._answer(utterance)
        if "?" in utterance or lowered.startswith(("what", "how", "who", "when", "where", "why")):
            return self._answer(utterance)
        if self._brain is not None:
            # End-of-chain fallthrough: nothing deterministic matched, so this
            # is conversation, not a command — let the brain talk. Anything it
            # wants *done* comes back as proposals gated below.
            return self._answer(utterance)
        return self._offer_choices(utterance)

    def _handle_changed_mind(self, utterance: str, lowered: str) -> dict[str, Any] | None:
        """Cancel or revise the latest local draft/outbox item.

        This is deliberately narrow v0 steering: it rewrites or cancels only
        local artifacts from the same text/voice session. Revisions still use
        the normal confirmation/execution path, and outbox cancellation only
        touches cancellable local rows — never an external send path.
        """

        if not _looks_like_changed_mind(lowered):
            return None

        revision_fragment = _extract_revision_fragment(utterance)
        cancel_only = _is_cancel_only_revision(revision_fragment)
        draft = self._latest_active_staged_action()

        if draft is None and cancel_only:
            outbox = self._latest_cancellable_outbox_message()
            if outbox is not None:
                from app.parker.pipeline import cancel_outbox_message

                cancelled = cancel_outbox_message(self.db, outbox.id)
                assert cancelled is not None
                return {
                    "kind": "cancelled_outbox",
                    "speech": (
                        f"Cancelled the local message to {cancelled.recipient}. "
                        "It stayed on this machine and won't be sent."
                    ),
                    "outbox_message_id": cancelled.id,
                }

        captured = draft.resolution_result.captured_intent if draft is not None else self._latest_open_captured_intent()
        if captured is None:
            return None

        from app.parker.pipeline import cancel_staged_action

        cancelled_id: int | None = None
        if draft is not None:
            cancel_staged_action(self.db, draft.id, cancelled_by="patient")
            cancelled_id = draft.id
        elif captured.status in {"pending", "resolved"}:
            captured.status = "rejected"
            self.db.commit()

        if cancel_only:
            response: dict[str, Any] = {
                "kind": "cancelled",
                "speech": "Cancelled the earlier draft. Nothing will run unless you ask again.",
            }
            if cancelled_id is not None:
                response["cancelled_staged_action_id"] = cancelled_id
            return response

        prior_subject = (captured.subject or captured.intent_text).strip()
        revision_lower = revision_fragment.lower()
        safety_text = f"{lowered} {revision_lower}"
        if _looks_like_emergency_substitution(safety_text):
            return {
                "kind": "emergency_redirect",
                "speech": (
                    "Cancelled the earlier draft. I can't replace emergency services. "
                    "If this may be urgent, call emergency services now or ask a nearby caregiver for help."
                ),
                "flag_for_family": True,
            }
        if _looks_like_sensitive_private_disclosure(safety_text):
            return {
                "kind": "refused",
                "speech": (
                    "Cancelled the earlier draft. I can't read or share private credentials or sensitive notes."
                ),
            }
        if _looks_like_financial_account_request(safety_text):
            return {
                "kind": "refused",
                "speech": (
                    "Cancelled the earlier draft. I can't access, reveal, or change bank/account information."
                ),
            }
        if _looks_like_medical_instruction_dictation(safety_text):
            return {
                "kind": "refused",
                "speech": (
                    "Cancelled the earlier draft. I heard medical or medication instructions. "
                    "I won't diagnose, recommend treatment, change medication, or turn that into an action."
                ),
                "flag_for_family": True,
            }
        if _looks_like_medical_advice(safety_text):
            return {
                "kind": "refused",
                "speech": (
                    "Cancelled the earlier draft. I can't diagnose or recommend treatment — "
                    "that's one for your doctor. I can note what you're noticing so the family can follow up."
                ),
                "flag_for_family": True,
            }
        if any(w in safety_text for w in MED_WORDS) and any(p in safety_text for p in MED_CHANGE_PHRASES):
            return {
                "kind": "refused",
                "speech": (
                    "Cancelled the earlier draft. I can't help change medication — "
                    "that's one for your doctor. I can note how you're feeling so the family can follow up."
                ),
                "flag_for_family": True,
            }
        ticket_response = _ticket_request_response(safety_text)
        if ticket_response is not None:
            ticket_response["speech"] = f"Cancelled the earlier draft. {ticket_response['speech']}"
            return ticket_response
        if _looks_like_purchase_after_ticket_negation(safety_text):
            return {
                "kind": "needs_human_approval",
                "speech": (
                    "Cancelled the earlier draft. I don't buy things myself. I can look options up "
                    "and ask the family to approve a purchase."
                ),
            }
        revised_subject = _revised_subject(prior_subject, utterance)
        requested_action = _requested_action_for_revision(captured.requested_action)
        speech = (
            "Cancelled the earlier draft. Updated it to: "
            f"“{revised_subject}”. I'll confirm before anything runs."
        )
        response = self._capture(
            intent_text=_intent_text_for_revision(requested_action, revised_subject),
            requested_action=requested_action,
            subject=revised_subject,
            recipient=captured.recipient,
            speech=speech,
        )
        if response["kind"] == "captured":
            response["kind"] = "revised"
            if cancelled_id is not None:
                response["cancelled_staged_action_id"] = cancelled_id
        return response

    def _latest_active_staged_action(self):
        from app.db.models import CapturedIntent, ResolutionResult, StagedAction

        return (
            self.db.query(StagedAction)
            .join(StagedAction.resolution_result)
            .join(ResolutionResult.captured_intent)
            .filter(CapturedIntent.call_log_id == self.call_log_id)
            .filter(StagedAction.status.in_(["staged", "confirmed"]))
            .order_by(StagedAction.created_at.desc(), StagedAction.id.desc())
            .first()
        )

    # ------------------------------------------------------------------
    # Conversational confirmation: the patient's own yes is the gate
    # ------------------------------------------------------------------

    def offer_pending_confirmation(self) -> dict[str, Any] | None:
        """Offer the latest staged action from this session for a spoken yes/no.

        Called by the voice loop after each tick. Only user-confirmable
        (CONFIRM_USER) actions are ever offered; each staged action is
        offered at most once per session — a deferred or ignored offer
        leaves the action staged for resurfacing and the review page,
        never silently acted on.
        """

        from app.parker.policy import CONFIRM_USER, confirmation_level

        if self._pending_confirmation is not None or self._pending_choices is not None:
            return None
        action = self._latest_active_staged_action()
        if action is None or action.status != "staged":
            return None
        if action.id in self._offered_confirmation_ids:
            return None
        if confirmation_level(action.action_type) != CONFIRM_USER:
            return None
        self._offered_confirmation_ids.add(action.id)
        self._pending_confirmation = action.id
        description = self._describe_staged_action(action)
        offer = {
            "kind": "confirm_offer",
            "speech": f"Ready when you are: {description}. Shall I go ahead — yes or no?",
            "staged_action_id": action.id,
        }
        # Parker spoke first: the screen shows the offer with nothing heard.
        self._publish_screen(heard="", response=offer)
        return offer

    def _handle_confirmation_reply(self, utterance: str) -> dict[str, Any] | None:
        from app.parker.pipeline import (
            cancel_staged_action,
            confirm_staged_action,
            execute_staged_action,
        )

        action_id = self._pending_confirmation
        assert action_id is not None
        normalized = re.sub(r"[,.!?]+", " ", utterance).strip().lower()
        normalized = re.sub(r"\s+", " ", normalized)
        reply_kind = _confirmation_reply_kind(normalized)
        if reply_kind == "yes":
            self._pending_confirmation = None
            confirm_staged_action(self.db, action_id, confirmed_by="patient")
            executed = execute_staged_action(self.db, action_id)
            return self._speech_for_execution(executed)
        if reply_kind == "no":
            self._pending_confirmation = None
            cancel_staged_action(self.db, action_id, cancelled_by="patient")
            return {
                "kind": "cancelled",
                "speech": "Okay — cancelled. Nothing will run unless you ask again.",
                "cancelled_staged_action_id": action_id,
            }
        self._pending_confirmation = None  # deferred; route the utterance normally
        return None

    def _describe_staged_action(self, action) -> str:
        import json as _json

        try:
            payload = _json.loads(action.action_payload or "{}")
        except ValueError:
            payload = {}
        subject = (payload.get("subject") or "").strip() or action.action_type
        if action.action_type == "family_message":
            recipient = payload.get("recipient") or "family"
            body = (payload.get("intent_text") or subject).strip()
            return f"a message to {recipient} — “{body}”"
        if action.action_type == "reminder":
            return f"a reminder about “{subject}”"
        if action.action_type == "exercise_start":
            return f"starting “{subject}”"
        if action.action_type == "media_playlist":
            return f"putting on “{subject}”"
        if action.action_type == "open_links":
            return f"showing “{subject}” on the family computer"
        return f"“{subject}”"

    def _speech_for_execution(self, action) -> dict[str, Any]:
        """Relay an execution outcome as something Parker can say aloud."""

        import json as _json

        result = action.execution_result or ""
        try:
            payload = _json.loads(action.action_payload or "{}")
        except ValueError:
            payload = {}
        if action.status == "executed":
            if action.action_type == "family_message":
                recipient = payload.get("recipient") or "family"
                if "released" in result:
                    speech = (
                        f"Done — your message to {recipient} is released. "
                        "The family can see it, and it stays on this machine for now."
                    )
                else:
                    speech = (
                        f"Done — your message to {recipient} is saved and waiting "
                        "for family approval before anything else happens."
                    )
            elif result.startswith("openclaw skill completed: "):
                speech = f"Done — {result.removeprefix('openclaw skill completed: ')}"
            elif action.action_type == "exercise_start":
                speech = f"Done — {payload.get('subject') or 'your exercise'} is starting."
            elif action.action_type == "reminder":
                speech = f"Done — I'll keep bringing up “{payload.get('subject') or 'that'}” until it's handled."
            else:
                speech = f"Done — {result}"
            return {"kind": "executed", "speech": speech, "staged_action_id": action.id}
        if action.status == "failed":
            detail = result.removeprefix("openclaw skill failed (no retry was attempted): ")
            return {
                "kind": "execution_failed",
                "speech": (
                    f"That didn't work — {detail}. I've put it on the family review "
                    "page, and I won't retry on my own."
                ),
                "staged_action_id": action.id,
                "flag_for_family": True,
            }
        return {
            "kind": "blocked",
            "speech": f"I couldn't run that — {result}",
            "staged_action_id": action.id,
        }

    def _latest_cancellable_outbox_message(self):
        from app.db.models import CapturedIntent, OutboxMessage, ResolutionResult, StagedAction

        return (
            self.db.query(OutboxMessage)
            .join(OutboxMessage.staged_action)
            .join(StagedAction.resolution_result)
            .join(ResolutionResult.captured_intent)
            .filter(CapturedIntent.call_log_id == self.call_log_id)
            .filter(OutboxMessage.status.in_(["queued_local", "approved_local"]))
            .order_by(OutboxMessage.created_at.desc(), OutboxMessage.id.desc())
            .first()
        )

    def _latest_open_captured_intent(self):
        from app.db.models import CapturedIntent

        return (
            self.db.query(CapturedIntent)
            .filter(CapturedIntent.call_log_id == self.call_log_id)
            .filter(CapturedIntent.status.in_(["pending", "resolved"]))
            .order_by(CapturedIntent.created_at.desc(), CapturedIntent.id.desc())
            .first()
        )

    def _offer_choices(self, utterance: str) -> dict[str, Any]:
        # Alternate ASR hypotheses that parse to a concrete safe intent become
        # evidence-based choices, listed before the generic suggestions. They
        # carry recipient/subject so a selection captures a complete intent.
        probed: list[dict[str, Any]] = []
        name_intent = name_prefix_candidate(utterance)
        if name_intent is not None:
            probed.append(name_intent)
        for fragment in fragment_candidates(utterance):
            if all(fragment["label"] != p["label"] for p in probed):
                probed.append(fragment)
        for alternate in self._current_alternates[:2]:
            intent = probe_direct_intent(alternate)
            if intent is not None and all(intent["label"] != p["label"] for p in probed):
                probed.append(intent)
        raw = suggest_repair_candidates(
            utterance,
            client=self._model_client,
            prior_choices=self._prior_offered_labels,
        )
        candidates = probed + [
            {"label": lbl, "action_type": at}
            for lbl, at in raw
            if all(lbl != p["label"] for p in probed)
        ]
        candidates = candidates[:3]
        result = execute_tool(
            self.db,
            self.call_log_id,
            "offer_repair_choices",
            {"candidates": candidates},
        )
        # The tool layer validates and returns bare {position,label,action_type}
        # choices; re-attach the probed enrichment by label so selection can
        # capture the parsed recipient/subject.
        enriched_by_label = {p["label"]: p for p in probed}
        for choice in result["choices"]:
            extra = enriched_by_label.get(choice["label"])
            if extra is not None:
                choice.update(
                    recipient=extra["recipient"],
                    subject=extra["subject"],
                    intent_text=extra["intent_text"],
                )
        self._pending_choices = result["choices"]
        self._pending_utterance = utterance
        self._pending_alternates = list(self._current_alternates)
        return {"kind": "choices", "speech": result["spoken_prompt"], "choices": result["choices"]}

    def _answer(self, utterance: str) -> dict[str, Any]:
        """The informational lane: the brain when configured, the stub otherwise.

        Every deterministic guard has already run by the time this is
        reached — the brain never sees a refused utterance. The reply is
        screened again on the way out (medical boundary, proposal
        allowlist) and trimmed to TTS-listenable length.
        """

        if self._brain is None:
            return {"kind": "answer", "speech": ANSWER_STUB_SPEECH}

        from app.brain.guard import screen_reply, trim_for_speech
        from app.parker.hands import effective_proposable_action_types

        if self._brain_context is None:
            from app.brain.claude import build_brain_context

            self._brain_context = build_brain_context()
        try:
            reply = self._brain.respond(list(self._brain_history), utterance, self._brain_context)
        except Exception:  # noqa: BLE001 — a dead brain must not kill the voice loop
            import logging

            logging.getLogger("parker.brain").exception("brain respond failed")
            return {
                "kind": "answer",
                "speech": "I couldn't reach my answers just now — try me again in a moment.",
            }

        # Gateway-backed action types are proposable only while the family's
        # OpenClaw gateway has an enabled skill behind them.
        result = screen_reply(reply, proposable=effective_proposable_action_types())
        speech = trim_for_speech(result.reply.speech)
        if result.medical_boundary_tripped:
            response: dict[str, Any] = {"kind": "refused", "speech": speech, "flag_for_family": True}
        elif result.reply.proposed_actions:
            response = self._offer_brain_actions(utterance, speech, result.reply.proposed_actions)
        else:
            response = {
                "kind": "answer",
                "speech": speech or "I don't have a good answer for that right now.",
            }
        self._remember_brain_exchange(utterance, response["speech"])
        return response

    def _offer_brain_actions(
        self,
        utterance: str,
        speech: str,
        proposals: tuple,
    ) -> dict[str, Any]:
        """Brain proposals become confirmation-gated choices, never captures.

        Same enrichment mechanics as probed repair choices: a selection
        captures the complete parsed intent through the normal pipeline.
        Message proposals must resolve to a lexicon-known recipient — a
        brain cannot address someone the family didn't configure.
        """

        candidates: list[dict[str, Any]] = []
        for action in proposals:
            recipient = action.recipient
            if action.action_type == "family_message":
                recipient, known = canonicalize_recipient(recipient or "")
                if not recipient or not known:
                    continue
            if any(action.label == c["label"] for c in candidates):
                continue
            candidates.append(
                {
                    "label": action.label,
                    "action_type": action.action_type,
                    "recipient": recipient,
                    "subject": action.subject,
                    "intent_text": action.intent_text,
                }
            )
        fallback_speech = speech or "I don't have a good answer for that right now."
        if not candidates:
            return {"kind": "answer", "speech": fallback_speech}
        result = execute_tool(
            self.db,
            self.call_log_id,
            "offer_repair_choices",
            {
                "candidates": [
                    {"label": c["label"], "action_type": c["action_type"]} for c in candidates
                ],
                "question": "Should I set that up?",
                "allow_single": True,
            },
        )
        if result.get("status") != "offered":
            return {"kind": "answer", "speech": fallback_speech}
        enriched_by_label = {c["label"]: c for c in candidates}
        for choice in result["choices"]:
            extra = enriched_by_label.get(choice["label"])
            if extra is not None:
                choice.update(
                    recipient=extra["recipient"],
                    subject=extra["subject"],
                    intent_text=extra["intent_text"],
                )
        self._pending_choices = result["choices"]
        self._pending_utterance = utterance
        self._pending_alternates = []
        spoken = f"{speech} {result['spoken_prompt']}".strip()
        return {"kind": "choices", "speech": spoken, "choices": result["choices"]}

    def _remember_brain_exchange(self, utterance: str, speech: str) -> None:
        self._brain_history.append(Message(role="user", content=utterance))
        self._brain_history.append(Message(role="assistant", content=speech))
        max_messages = BRAIN_HISTORY_MAX_TURNS * 2
        if len(self._brain_history) > max_messages:
            del self._brain_history[: len(self._brain_history) - max_messages]

    def _handle_selection(self, utterance: str) -> Optional[dict[str, Any]]:
        """Resolve one utterance spoken while repair choices are pending.

        Digits select. Counting sequences and dismissal words set the
        choices aside. A clearly-new command/question returns ``None`` so
        ``handle`` routes it normally — the web-private validation lane
        showed ambient speech constantly draws generic choices, and before
        this seam selection mode swallowed the user's next real attempt
        behind "Just say the number". Anything else re-prompts as a
        garbled selection attempt.
        """

        choices = self._pending_choices or []
        if utterance.isdigit() and 1 <= int(utterance) <= len(choices):
            return self._select_choice(choices[int(utterance) - 1], choices)
        counting = _counting_sequence_response(utterance)
        if counting is not None:
            self._dismiss_pending_choices()
            return counting
        normalized = re.sub(r"[,.!?]+", " ", utterance).strip().lower()
        normalized = re.sub(r"\s+", " ", normalized)
        if normalized in DISMISS_CHOICE_PHRASES or _control_negation_response(utterance) is not None:
            none_choice = next((c for c in choices if c.get("action_type") is None), None)
            if none_choice is not None:
                return self._select_choice(none_choice, choices)
            self._dismiss_pending_choices()
            return {"kind": "retry", "speech": "Okay, none of those. Tell me again in your own words."}
        if _looks_like_new_directed_utterance(utterance):
            self._dismiss_pending_choices()
            return None
        speech = "Just say the number — " + ", ".join(
            f"{c['position']}) {c['label']}" for c in choices
        )
        return {"kind": "choices", "speech": speech, "choices": choices}

    def _dismiss_pending_choices(self) -> None:
        self._pending_choices = None
        self._pending_utterance = None
        self._pending_alternates = []

    def _select_choice(
        self, choice: dict[str, Any], choices: list[dict[str, Any]]
    ) -> dict[str, Any]:
        source = self._pending_utterance or choice["label"]
        alternates = self._pending_alternates
        self._dismiss_pending_choices()
        if choice["action_type"] is None:
            # Save the rejected labels so the next offer can generate different alternatives.
            self._prior_offered_labels = [
                c["label"] for c in choices if c["action_type"] is not None
            ]
            record_repair_event(
                self.db,
                call_log_id=self.call_log_id,
                utterance=source,
                alternates=alternates,
                choices=choices,
                selected_position=choice["position"],
                selected_label=choice["label"],
                selected_action_type=None,
            )
            return {"kind": "retry", "speech": "Okay, none of those. Tell me again in your own words."}
        response = self._capture(
            intent_text=choice.get("intent_text") or source,
            requested_action=choice["action_type"],
            subject=choice.get("subject") or source[:120],
            recipient=choice.get("recipient"),
            speech=f"Got it — I'll treat that as: {choice['label']}. I'll confirm before anything runs.",
        )
        record_repair_event(
            self.db,
            call_log_id=self.call_log_id,
            utterance=source,
            alternates=alternates,
            choices=choices,
            selected_position=choice["position"],
            selected_label=choice["label"],
            selected_action_type=choice["action_type"],
            captured_intent_id=response.get("captured_intent_id"),
        )
        return response

    def _capture(
        self,
        *,
        intent_text: str,
        requested_action: str,
        subject: str,
        recipient: Optional[str],
        speech: str,
    ) -> dict[str, Any]:
        result = execute_tool(
            self.db,
            self.call_log_id,
            "capture_intent",
            {
                "intent_text": intent_text,
                "requested_action": requested_action,
                "subject": subject,
                **({"recipient": recipient} if recipient else {}),
            },
        )
        if result.get("status") != "captured":
            return {"kind": "error", "speech": "Something went wrong saving that.", "detail": result}
        self._prior_offered_labels = None  # successful capture; prior history no longer relevant
        return {
            "kind": "captured",
            "speech": speech,
            "captured_intent_id": result["captured_intent_id"],
            "requested_action": result["requested_action"],
        }


def main() -> None:  # pragma: no cover — interactive entry point
    from app.brain.build import build_brain_adapter
    from app.db.database import SessionLocal, create_tables
    from app.db.models import CallLog
    from app.parker.hands import configure_hands_from_settings

    create_tables()
    configure_hands_from_settings()
    db = SessionLocal()
    call = CallLog(call_sid=f"TEXT-{datetime.utcnow():%Y%m%d%H%M%S}", call_type="text_loop")
    db.add(call)
    db.commit()
    db.refresh(call)
    session = TextSession(
        db, call.id, model_client=_build_model_client(), brain=build_brain_adapter()
    )
    print("Parker text loop — type an utterance, or 'quit' to exit.")
    print("Captured intents stage via POST /parker/tick and confirm at /parker/review/ui.\n")
    while True:
        try:
            line = input("you> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if line.strip().lower() in {"quit", "exit"}:
            break
        response = session.handle(line)
        print(f"parker> {response['speech']}")
    db.close()


if __name__ == "__main__":  # pragma: no cover
    main()
