"""ClaudeBrainAdapter — the v0 brain, direct Anthropic API.

Implements the ``BrainAdapter`` contract: speech in text blocks, action
proposals only through the ``propose_action`` tool. The adapter is pure
conversation — it holds no database handle and no pipeline access, so it
*cannot* capture or execute even if the model tries. Everything it
returns is screened again by ``app.brain.guard`` in the brainstem.

Zero-config invariant: ``build_brain_adapter()`` returns ``None`` when
``ANTHROPIC_API_KEY`` is unset, and callers fall back to the
deterministic answer stub. Tests inject a fake client; nothing here runs
on the network in the suite.
"""

from __future__ import annotations

from typing import Any, Optional

from app.brain.adapter import (
    PROPOSABLE_ACTION_TYPES,
    BrainContext,
    BrainReply,
    Message,
    ProposedAction,
)

PROPOSE_ACTION_TOOL: dict[str, Any] = {
    "name": "propose_action",
    "description": (
        "Propose one concrete action for Parker to offer the user as a "
        "confirmation choice. Nothing happens unless the user confirms it "
        "through Parker's own pipeline — never describe the action as "
        "already done. Use at most two proposals per reply."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action_type": {
                "type": "string",
                "enum": sorted(PROPOSABLE_ACTION_TYPES),
            },
            "label": {
                "type": "string",
                "description": "Short spoken description of the choice, ≤ 80 chars (e.g. \"a reminder to call the physio tomorrow\").",
            },
            "subject": {
                "type": "string",
                "description": "Short human-readable subject Parker resurfaces later.",
            },
            "intent_text": {
                "type": "string",
                "description": "The full intent in the user's terms (for messages: the message body).",
            },
            "recipient": {
                "type": "string",
                "description": "Family contact name, only for family_message, only from the known names.",
            },
        },
        "required": ["action_type", "label", "subject", "intent_text"],
    },
}

_SYSTEM_TEMPLATE = """\
You are Parker, a home voice assistant for {patient_name}, who has Parkinson's disease and speaks with effort.

You are the conversational brain only. Parker's deterministic layer owns safety, confirmation, and every action. You cannot do anything yourself — you may only suggest actions with the propose_action tool, and Parker asks {patient_name} to confirm before anything happens.

How to answer:
- Your words are spoken aloud by TTS to a listener. Default to 1-3 short, warm, plain sentences. No lists, no markdown, no URLs, no stage directions.
- If a longer answer would genuinely help, give the short version first and offer more.
- Be honest about limits: you may not have live data (weather, today's news). Say so plainly and offer what you can do instead.

Hard boundaries — never cross these, even when asked directly or hypothetically:
- No medical advice: never diagnose, evaluate symptoms, recommend treatment, or comment on medication or doses — including "what do you think about..." questions. Redirect warmly to their doctor or family, and offer to save it as a question for the next appointment.
- Never claim to have sent, ordered, bought, scheduled, or changed anything. You cannot.
- Never act as emergency services. If anything sounds urgent, tell them to call emergency services or get a family member right away.
- No credentials, passwords, or bank/account details, ever.

Proposing actions:
- When {patient_name} clearly wants something done — a reminder, a message to family, a speech or movement exercise, a playlist, a note for an appointment — call propose_action instead of describing it as done.
- Family and familiar names you may use: {names}. Never invent or guess other recipients.
"""


def _system_prompt(context: BrainContext) -> str:
    names = ", ".join(context.lexicon_names) if context.lexicon_names else "(none configured)"
    return _SYSTEM_TEMPLATE.format(patient_name=context.patient_name, names=names)


class ClaudeBrainAdapter:
    """BrainAdapter over ``anthropic.Anthropic.messages.create``."""

    def __init__(
        self,
        client: Any,
        *,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> None:
        from app.config import settings

        self._client = client
        self._model = model or settings.parker_brain_model
        self._max_tokens = max_tokens or settings.parker_brain_max_tokens

    def respond(
        self,
        history: list[Message],
        utterance: str,
        context: BrainContext,
    ) -> BrainReply:
        messages = [{"role": m.role, "content": m.content} for m in history]
        messages.append({"role": "user", "content": utterance})
        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=_system_prompt(context),
            messages=messages,
            tools=[PROPOSE_ACTION_TOOL],
        )
        speech_parts: list[str] = []
        proposals: list[ProposedAction] = []
        for block in response.content:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                speech_parts.append(block.text)
            elif block_type == "tool_use" and getattr(block, "name", "") == "propose_action":
                data = block.input or {}
                proposals.append(
                    ProposedAction(
                        action_type=str(data.get("action_type", "")),
                        label=str(data.get("label", "")).strip(),
                        subject=str(data.get("subject", "")).strip(),
                        intent_text=str(data.get("intent_text", "")).strip(),
                        recipient=(str(data["recipient"]).strip() if data.get("recipient") else None),
                    )
                )
        return BrainReply(speech=" ".join(part.strip() for part in speech_parts if part.strip()).strip(), proposed_actions=tuple(proposals))


def build_brain_adapter() -> Optional[ClaudeBrainAdapter]:
    """A configured brain, or None so callers keep the deterministic stub."""

    from app.config import settings

    if not settings.anthropic_api_key:
        return None
    try:
        import anthropic

        return ClaudeBrainAdapter(anthropic.Anthropic(api_key=settings.anthropic_api_key))
    except Exception:  # noqa: BLE001
        return None


def build_brain_context() -> BrainContext:
    """Context card from family-administered settings."""

    from app.config import settings
    from app.conversation.textloop import _lexicon_names

    return BrainContext(
        patient_name=settings.patient_name,
        lexicon_names=tuple(_lexicon_names()),
    )
