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
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.conversation.repair import suggest_repair_candidates
from app.conversation.tools import execute_tool

MED_WORDS = ("pill", "pills", "medication", "meds", "dose")
MED_CHANGE_PHRASES = ("should i", "take half", "skip", "double", "stop taking")
MEDICAL_ADVICE_WORDS = (
    "diagnose",
    "diagnosis",
    "treatment",
    "treat",
    "antibiotic",
    "symptom",
    "tremor",
)
MEDICAL_ADVICE_PHRASES = (
    "what treatment",
    "which treatment",
    "should i try",
    "do you think",
    "is getting worse",
    "does this mean",
)
EMERGENCY_WORDS = ("911", "emergency", "ambulance", "can't breathe", "cant breathe", "chest pain", "fell")
EMERGENCY_SUBSTITUTION_PHRASES = ("instead of calling", "handle it instead", "can't get up", "cant get up")
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
    "cancel that",
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
    "up": "I heard up, but there isn't a device, choice, or local action waiting.",
    "down": "I heard down, but there isn't a device, choice, or local action waiting.",
    "left": "I heard left, but there isn't a device, choice, or local action waiting.",
    "right": "I heard right, but there isn't a device, choice, or local action waiting.",
    "on": "I heard on, but there isn't a device or local action waiting.",
    "off": "I heard off, but there isn't a device or local action waiting.",
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


def _looks_like_medical_advice(lowered: str) -> bool:
    return any(word in lowered for word in MEDICAL_ADVICE_WORDS) and any(
        phrase in lowered for phrase in MEDICAL_ADVICE_PHRASES
    )


def _extract_revision_fragment(utterance: str) -> str:
    fragment = utterance.strip().strip(" .!?")
    fragment = re.sub(r"^(?:wait|hold on)[\s,]+", "", fragment, flags=re.IGNORECASE)
    fragment = re.sub(r"^(?:no|nope)[\s,]+", "", fragment, flags=re.IGNORECASE)
    fragment = re.sub(r"^(?:actually|change that|make it|make that|scratch that|cancel that)[\s,]*", "", fragment, flags=re.IGNORECASE)
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


class TextSession:
    """One conversational text session bound to a call log."""

    def __init__(self, db: Session, call_log_id: int, *, model_client: "Any | None" = None):
        self.db = db
        self.call_log_id = call_log_id
        self._model_client = model_client  # anthropic.Anthropic or None; None → hardcoded fallback
        self._pending_choices: Optional[list[dict[str, Any]]] = None
        self._pending_utterance: Optional[str] = None
        # Labels from the most recently offered (and user-rejected) repair choices.
        # Passed to suggest_repair_candidates on the next offer so the model can
        # generate genuinely different alternatives instead of repeating itself.
        self._prior_offered_labels: Optional[list[str]] = None

    def handle(self, text: str) -> dict[str, Any]:
        """Route one utterance and return {kind, speech, ...}."""

        utterance = text.strip()
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
        if lowered.count("...") >= 2 or any(p in lowered for p in VAGUE_PHRASES):
            return self._offer_choices(utterance)

        match = MESSAGE_PATTERN.match(utterance) or SEND_PATTERN.match(utterance)
        if match:
            recipient, body = match.group(1), match.group(2).strip()
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
        if "?" in utterance or lowered.startswith(("what", "how", "who", "when", "where")):
            return {
                "kind": "answer",
                "speech": "I'd look that up and summarize it for you. (Research answers are stubbed in the local text loop.)",
            }
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
        if _looks_like_emergency_substitution(revision_lower):
            return {
                "kind": "emergency_redirect",
                "speech": (
                    "Cancelled the earlier draft. I can't replace emergency services. "
                    "If this may be urgent, call emergency services now or ask a nearby caregiver for help."
                ),
                "flag_for_family": True,
            }
        if _looks_like_sensitive_private_disclosure(revision_lower):
            return {
                "kind": "refused",
                "speech": (
                    "Cancelled the earlier draft. I can't read or share private credentials or sensitive notes."
                ),
            }
        if _looks_like_medical_advice(revision_lower):
            return {
                "kind": "refused",
                "speech": (
                    "Cancelled the earlier draft. I can't diagnose or recommend treatment — "
                    "that's one for your doctor. I can note what you're noticing so the family can follow up."
                ),
                "flag_for_family": True,
            }
        if any(w in revision_lower for w in MED_WORDS) and any(p in revision_lower for p in MED_CHANGE_PHRASES):
            return {
                "kind": "refused",
                "speech": (
                    "Cancelled the earlier draft. I can't help change medication — "
                    "that's one for your doctor. I can note how you're feeling so the family can follow up."
                ),
                "flag_for_family": True,
            }
        if any(p in revision_lower for p in PURCHASE_PHRASES):
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
        raw = suggest_repair_candidates(
            utterance,
            client=self._model_client,
            prior_choices=self._prior_offered_labels,
        )
        candidates = [{"label": lbl, "action_type": at} for lbl, at in raw]
        result = execute_tool(
            self.db,
            self.call_log_id,
            "offer_repair_choices",
            {"candidates": candidates},
        )
        self._pending_choices = result["choices"]
        self._pending_utterance = utterance
        return {"kind": "choices", "speech": result["spoken_prompt"], "choices": result["choices"]}

    def _handle_selection(self, utterance: str) -> dict[str, Any]:
        choices = self._pending_choices or []
        if not utterance.isdigit() or not 1 <= int(utterance) <= len(choices):
            speech = "Just say the number — " + ", ".join(
                f"{c['position']}) {c['label']}" for c in choices
            )
            return {"kind": "choices", "speech": speech, "choices": choices}
        choice = choices[int(utterance) - 1]
        source = self._pending_utterance or choice["label"]
        self._pending_choices = None
        self._pending_utterance = None
        if choice["action_type"] is None:
            # Save the rejected labels so the next offer can generate different alternatives.
            self._prior_offered_labels = [
                c["label"] for c in choices if c["action_type"] is not None
            ]
            return {"kind": "retry", "speech": "Okay, none of those. Tell me again in your own words."}
        return self._capture(
            intent_text=source,
            requested_action=choice["action_type"],
            subject=source[:120],
            recipient=None,
            speech=f"Got it — I'll treat that as: {choice['label']}. I'll confirm before anything runs.",
        )

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
    from app.db.database import SessionLocal, create_tables
    from app.db.models import CallLog

    create_tables()
    db = SessionLocal()
    call = CallLog(call_sid=f"TEXT-{datetime.utcnow():%Y%m%d%H%M%S}", call_type="text_loop")
    db.add(call)
    db.commit()
    db.refresh(call)
    session = TextSession(db, call.id, model_client=_build_model_client())
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
