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

import re
from typing import Any, Optional

from app.brain.adapter import (
    PROPOSABLE_ACTION_TYPES,
    BrainContext,
    BrainReply,
    Message,
    ProposedAction,
    Source,
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
- When a question needs current information (news, people, events, prices, weather, sport), do exactly one web search, then answer immediately from what you find. Without search, be honest about limits: say plainly what you don't know and offer what you can do instead.
- Never read a URL or web address aloud; the screen shows sources. Use the units natural to the household's country.

Hard boundaries — never cross these, even when asked directly or hypothetically:
- No medical advice: never diagnose, evaluate symptoms, recommend treatment, or comment on medication or doses — including "what do you think about..." questions. Redirect warmly to their doctor or family, and offer to save it as a question for the next appointment.
- Never claim to have sent, ordered, bought, scheduled, or changed anything. You cannot.
- Never act as emergency services. If anything sounds urgent, tell them to call emergency services or get a family member right away.
- No credentials, passwords, or bank/account details, ever.

Proposing actions:
- When {patient_name} clearly wants something done — a reminder, a message to family, a speech or movement exercise, a playlist, a note for an appointment — call propose_action instead of describing it as done.
- Family and familiar names you may use: {names}. Never invent or guess other recipients.
"""

_HOME_PLACE_LINE = (
    "\nThe household is in {home_place}. Use that for local questions — weather, "
    "what's on nearby, local times — without asking which town. When searching "
    "about local topics, put \"{home_place}\" in the query and ignore results "
    "for other places with the same name."
)


def _system_prompt(context: BrainContext) -> str:
    names = ", ".join(context.lexicon_names) if context.lexicon_names else "(none configured)"
    prompt = _SYSTEM_TEMPLATE.format(patient_name=context.patient_name, names=names)
    if context.home_place:
        prompt += _HOME_PLACE_LINE.format(home_place=context.home_place)
    return prompt


# Sentence boundary for incremental streaming: emit complete sentences as
# deltas arrive so TTS can start after the first one.
_STREAM_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

MAX_ANSWER_SOURCES = 3
_PAUSE_TURN_CONTINUATIONS = 2


def _drain_sentences(buffer: str, emit: Any) -> str:
    """Emit every complete sentence in ``buffer``; return the remainder."""

    parts = _STREAM_SENTENCE_END.split(buffer)
    for complete in parts[:-1]:
        if complete.strip():
            emit(complete.strip())
    return parts[-1]


class ClaudeBrainAdapter:
    """BrainAdapter over the Anthropic Messages API.

    With ``PARKER_BRAIN_WEB_SEARCH`` on (default), the server-side web
    search tool covers the long tail of current-information questions and
    its citations surface as ``Source`` chips — searched, labeled, never
    spoken as URLs. ``respond_stream`` emits complete sentences as they
    generate so the harness can start speaking after the first one.
    """

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

    def _tools(self, context: BrainContext) -> list[dict[str, Any]]:
        import copy

        from app.config import settings
        from app.parker.hands import effective_proposable_action_types

        # Advertise only stageable action types — a static enum let the
        # brain promise appointment notes that died at the gate (live find).
        propose_tool = copy.deepcopy(PROPOSE_ACTION_TOOL)
        propose_tool["input_schema"]["properties"]["action_type"]["enum"] = sorted(
            effective_proposable_action_types()
        )
        tools: list[dict[str, Any]] = [propose_tool]
        if settings.parker_brain_web_search:
            search_tool: dict[str, Any] = {
                "type": "web_search_20260209",
                "name": "web_search",
                "max_uses": settings.parker_brain_web_search_max_uses,
            }
            if context.home_place:
                # Ground searches where the household actually is, so
                # "weather today" finds the right town's forecast.
                search_tool["user_location"] = {
                    "type": "approximate",
                    "city": context.home_place,
                }
            tools.append(search_tool)
        return tools

    def _request_kwargs(
        self, history: list[Message], utterance: str, context: BrainContext
    ) -> dict[str, Any]:
        from app.config import settings

        messages = [{"role": m.role, "content": m.content} for m in history]
        messages.append({"role": "user", "content": utterance})
        tools = self._tools(context)
        max_tokens = self._max_tokens
        if settings.parker_brain_web_search:
            # A search turn spends output on the tool call before the answer.
            max_tokens = max(max_tokens, 700)
        return {
            "model": self._model,
            "max_tokens": max_tokens,
            "system": _system_prompt(context),
            "messages": messages,
            "tools": tools,
            # Low effort is the measured latency lever for spoken turns —
            # short warm answers don't need deep thinking between steps.
            "output_config": {"effort": settings.parker_brain_effort},
        }

    @staticmethod
    def _merge_messages(responses: list[Any]) -> BrainReply:
        """Speech, proposals, and citation sources from one or more messages."""

        speech_parts: list[str] = []
        proposals: list[ProposedAction] = []
        cited: list[Source] = []
        searched: list[Source] = []
        seen_urls: set[str] = set()

        def add_source(bucket: list[Source], url: Any, title: Any) -> None:
            url_text = str(url or "")
            if not url_text or url_text in seen_urls:
                return
            seen_urls.add(url_text)
            label = str(title or "").strip() or url_text.split("//")[-1].split("/")[0]
            bucket.append(Source(label=label[:80], url=url_text, fresh_as_of="just searched"))

        for response in responses:
            for block in getattr(response, "content", []) or []:
                block_type = getattr(block, "type", None)
                if block_type == "text":
                    speech_parts.append(block.text)
                    for citation in getattr(block, "citations", None) or []:
                        add_source(
                            cited, getattr(citation, "url", ""), getattr(citation, "title", "")
                        )
                elif block_type == "web_search_tool_result":
                    # The pages the search actually returned. Citations are
                    # span-dependent and often absent (observed live), so
                    # these are the fallback evidence when nothing is cited.
                    content = getattr(block, "content", None)
                    if isinstance(content, list):
                        for item in content:
                            if getattr(item, "type", "") == "web_search_result":
                                add_source(
                                    searched,
                                    getattr(item, "url", ""),
                                    getattr(item, "title", ""),
                                )
                elif block_type == "tool_use" and getattr(block, "name", "") == "propose_action":
                    data = block.input or {}
                    proposals.append(
                        ProposedAction(
                            action_type=str(data.get("action_type", "")),
                            label=str(data.get("label", "")).strip(),
                            subject=str(data.get("subject", "")).strip(),
                            intent_text=str(data.get("intent_text", "")).strip(),
                            recipient=(
                                str(data["recipient"]).strip() if data.get("recipient") else None
                            ),
                        )
                    )
        sources = cited if cited else searched
        return BrainReply(
            speech=" ".join(part.strip() for part in speech_parts if part.strip()).strip(),
            proposed_actions=tuple(proposals),
            sources=tuple(sources[:MAX_ANSWER_SOURCES]),
        )

    def _continue_paused(
        self, kwargs: dict[str, Any], responses: list[Any]
    ) -> list[Any]:
        """Resume ``pause_turn`` stops (long server-tool turns), bounded."""

        continuations = 0
        while (
            getattr(responses[-1], "stop_reason", None) == "pause_turn"
            and continuations < _PAUSE_TURN_CONTINUATIONS
        ):
            continuations += 1
            kwargs = dict(kwargs)
            kwargs["messages"] = list(kwargs["messages"]) + [
                {"role": "assistant", "content": responses[-1].content}
            ]
            responses.append(self._client.messages.create(**kwargs))
        return responses

    def respond(
        self,
        history: list[Message],
        utterance: str,
        context: BrainContext,
    ) -> BrainReply:
        kwargs = self._request_kwargs(history, utterance, context)
        responses = [self._client.messages.create(**kwargs)]
        responses = self._continue_paused(kwargs, responses)
        return self._merge_messages(responses)

    def respond_stream(
        self,
        history: list[Message],
        utterance: str,
        context: BrainContext,
        on_sentence: Any,
    ) -> BrainReply:
        """Stream sentences to ``on_sentence`` as they complete.

        Falls back to the non-streaming path (emitting the whole answer
        once) when the client has no ``messages.stream`` — injected fakes
        and unusual transports keep working unchanged.
        """

        stream_factory = getattr(getattr(self._client, "messages", None), "stream", None)
        if stream_factory is None:
            reply = self.respond(history, utterance, context)
            if reply.speech:
                on_sentence(reply.speech)
            return reply

        kwargs = self._request_kwargs(history, utterance, context)
        buffer = ""
        with stream_factory(**kwargs) as stream:
            for text in stream.text_stream:
                buffer += text
                buffer = _drain_sentences(buffer, on_sentence)
            final = stream.get_final_message()
        if buffer.strip():
            on_sentence(buffer.strip())
        responses = self._continue_paused(kwargs, [final])
        for late in responses[1:]:  # continuation text was never streamed
            late_reply = self._merge_messages([late])
            if late_reply.speech:
                on_sentence(late_reply.speech)
        return self._merge_messages(responses)


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
        home_place=settings.parker_home_place.strip(),
    )
