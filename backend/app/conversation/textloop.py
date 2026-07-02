"""Local text loop: a transcript-capture seam over the real tool layer.

Run with ``make repl``. Each typed line is treated as an utterance and
routed deterministically (keyword rules, no model, no audio) through the
same tools a voice agent would call: ``offer_repair_choices`` for
ambiguous intents and ``capture_intent`` for clear ones. Captured intents
then flow through the normal resolve → stage → confirm pipeline — this
loop never confirms or executes anything itself.

Safety mirrors the action policy: medication-change requests are refused,
purchases are routed to human approval, and nothing external happens.
"""

from __future__ import annotations

import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.brain.adapter import BrainAdapter, BrainContext, Message
from app.conversation.repair import suggest_repair_candidates
from app.conversation.repair_events import record_repair_event
from app.conversation.tools import execute_tool

# Bounded brain-lane conversation memory: enough for follow-ups
# ("what about Saturday?"), small enough to stay cheap and forgetful.
BRAIN_HISTORY_MAX_TURNS = 12

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
    """Clarify multi-word device/media controls when no approved context exists."""

    normalized = re.sub(r"[,.!?]+", " ", utterance).strip().lower()
    normalized = re.sub(r"\s+", " ", normalized)
    if not normalized:
        return None
    control_words = ("turn", "switch", "increase", "decrease", "raise", "lower", "volume")
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
        "bedroom",
        "bathroom",
        "washroom",
    )
    if any(word in normalized for word in control_words) and any(word in normalized for word in device_words):
        return {
            "kind": "context_required",
            "speech": (
                "I heard a device or media control, but there isn't an approved TV, room, "
                "or device context waiting here. I won't change anything without that context."
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
        # Labels from the most recently offered (and user-rejected) repair choices.
        # Passed to suggest_repair_candidates on the next offer so the model can
        # generate genuinely different alternatives instead of repeating itself.
        self._prior_offered_labels: Optional[list[str]] = None
        # Alternate ASR hypotheses for the utterance currently being handled,
        # and for the utterance whose repair choices are pending selection.
        self._current_alternates: list[str] = []
        self._pending_alternates: list[str] = []

    def handle(self, text: str, *, alternates: Optional[list[str]] = None) -> dict[str, Any]:
        """Route one utterance and return {kind, speech, ...}.

        ``alternates`` are additional ASR hypotheses for the same audio
        (n-best or a second model's transcript). They are never routed
        directly — they only enrich repair choices via safe probing.
        """

        utterance = text.strip()
        self._current_alternates = [a for a in (alternates or []) if a.strip() and a.strip() != utterance]
        if not utterance:
            return {"kind": "noop", "speech": "I'm listening."}
        if self._pending_choices is not None:
            return self._handle_selection(utterance)

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
        if any(p in lowered for p in PURCHASE_PHRASES):
            return {
                "kind": "needs_human_approval",
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
        if "?" in utterance or lowered.startswith(("what", "how", "who", "when", "where")):
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
        if any(p in safety_text for p in PURCHASE_PHRASES):
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

    def _handle_selection(self, utterance: str) -> dict[str, Any]:
        choices = self._pending_choices or []
        if not utterance.isdigit() or not 1 <= int(utterance) <= len(choices):
            speech = "Just say the number — " + ", ".join(
                f"{c['position']}) {c['label']}" for c in choices
            )
            return {"kind": "choices", "speech": speech, "choices": choices}
        choice = choices[int(utterance) - 1]
        source = self._pending_utterance or choice["label"]
        alternates = self._pending_alternates
        self._pending_choices = None
        self._pending_utterance = None
        self._pending_alternates = []
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
