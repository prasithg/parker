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
PURCHASE_PHRASES = ("order", "buy", "purchase", "card on file")
VAGUE_PHRASES = ("you know", "the thing", "the one with", "no the other")
MESSAGE_PATTERN = re.compile(r"^(?:tell|message)\s+([A-Za-z]+)\s+(.+)$", re.IGNORECASE)
SEND_PATTERN = re.compile(r"^send\s+([A-Za-z]+)\s+(?:a\s+message\s+)?(?:that\s+|saying\s+)?(.+)$", re.IGNORECASE)
REMIND_PATTERN = re.compile(r"^remind\s+(?:me|us|him|her|dad|mom)?\s*(?:to\s+)?(.+)$", re.IGNORECASE)


class TextSession:
    """One conversational text session bound to a call log."""

    def __init__(self, db: Session, call_log_id: int, *, model_client: "Any | None" = None):
        self.db = db
        self.call_log_id = call_log_id
        self._model_client = model_client  # anthropic.Anthropic or None; None → hardcoded fallback
        self._pending_choices: Optional[list[dict[str, Any]]] = None
        self._pending_utterance: Optional[str] = None

    def handle(self, text: str) -> dict[str, Any]:
        """Route one utterance and return {kind, speech, ...}."""

        utterance = text.strip()
        if not utterance:
            return {"kind": "noop", "speech": "I'm listening."}
        if self._pending_choices is not None:
            return self._handle_selection(utterance)

        lowered = utterance.lower()
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
        if "?" in utterance or lowered.startswith(("what", "how", "who", "when", "where")):
            return {
                "kind": "answer",
                "speech": "I'd look that up and summarize it for you. (Research answers are stubbed in the local text loop.)",
            }
        return self._offer_choices(utterance)

    def _offer_choices(self, utterance: str) -> dict[str, Any]:
        raw = suggest_repair_candidates(utterance, client=self._model_client)
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
    session = TextSession(db, call.id)
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
