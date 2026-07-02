"""OpenClawBrainAdapter — the v1 brain: the family's OpenClaw agent.

Same contract, same gates as every brain (docs/brain-adapters.md): the
adapter only converses. Anything the OpenClaw agent wants *done* comes
back as ``ProposedAction``s that Parker turns into confirmation-gated
choices; execution happens later, at the execution seam
(``app.parker.hands``), and only for staged intents the patient confirmed.

Gateway contract (see docs/runbook.md, "Connecting a real OpenClaw
instance"):

- Conversation matches the public OpenClaw gateway API: OpenAI-compatible
  ``POST /v1/chat/completions`` on the gateway port (default 18789),
  bearer-token auth (the gateway's ``OPENCLAW_GATEWAY_TOKEN``). Model id
  ``openclaw`` targets the instance's default agent.
- Action proposals ride the OpenAI ``tools``/``tool_calls`` channel when
  the gateway honors client tools; a ``<propose_action>{...json...}</propose_action>``
  tag in the reply text is accepted as a fallback for agents that answer
  in plain text. Both paths land in the same post-response guard.

Config: ``PARKER_OPENCLAW_GATEWAY_URL`` / ``PARKER_OPENCLAW_GATEWAY_TOKEN``.
No URL configured means no gateway anywhere — the zero-config paths stay
keyless and offline, and tests use a fake transport, never the network.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

import httpx

from app.brain.adapter import BrainContext, BrainReply, Message, ProposedAction
from app.brain.claude import PROPOSE_ACTION_TOOL, _system_prompt

DEFAULT_TIMEOUT_SECONDS = 30.0

# The propose_action tool in OpenAI function-calling format (the gateway is
# OpenAI-compatible); schema shared with the Anthropic-format tool so the
# proposal surface cannot drift between brains.
OPENAI_PROPOSE_ACTION_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": PROPOSE_ACTION_TOOL["name"],
        "description": PROPOSE_ACTION_TOOL["description"],
        "parameters": PROPOSE_ACTION_TOOL["input_schema"],
    },
}

_PROPOSE_TAG = re.compile(r"<propose_action>\s*(\{.*?\})\s*</propose_action>", re.DOTALL)


class GatewayError(RuntimeError):
    """The OpenClaw gateway is unreachable, unauthorized, or answered garbage."""


class OpenClawGateway:
    """Minimal HTTP client for the family's OpenClaw gateway.

    ``client`` is injectable (tests pass ``httpx.Client(transport=MockTransport)``);
    every transport/HTTP/parse failure surfaces as ``GatewayError`` so
    callers degrade instead of crashing the voice loop.
    """

    def __init__(
        self,
        base_url: str,
        *,
        token: str = "",
        client: Optional[httpx.Client] = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        try:
            response = self._client.request(
                method,
                f"{self.base_url}{path}",
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            raise GatewayError(f"OpenClaw gateway {method} {path} failed: {exc}") from exc
        except ValueError as exc:  # non-JSON body
            raise GatewayError(f"OpenClaw gateway {method} {path} returned non-JSON") from exc

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        tools: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """One OpenAI-compatible chat completion against the default agent."""

        payload: dict[str, Any] = {"model": "openclaw", "messages": messages, "stream": False}
        if tools:
            payload["tools"] = tools
        data = self._request("POST", "/v1/chat/completions", payload)
        if not isinstance(data, dict) or not data.get("choices"):
            raise GatewayError("OpenClaw gateway chat reply had no choices")
        return data

    # ------------------------------------------------------------------
    # Parker bridge endpoints (skill discovery + invocation).
    #
    # The public gateway API documents no HTTP route for listing or
    # invoking skills — skills are agent-internal. Parker therefore
    # defines a minimal bridge contract the patient-identity instance
    # exposes (a small OpenClaw plugin route; deployment steps in the
    # runbook). Faked in every test.
    # ------------------------------------------------------------------

    def list_parker_skills(self) -> list[dict[str, Any]]:
        """Enabled skills: ``[{"name", "action_types", "enabled"}, ...]``."""

        data = self._request("GET", "/parker/v1/skills")
        skills = data.get("skills") if isinstance(data, dict) else None
        if not isinstance(skills, list):
            raise GatewayError("OpenClaw gateway skill list was malformed")
        return [entry for entry in skills if isinstance(entry, dict)]

    def invoke_skill(
        self,
        action_type: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Forward one approved intent to the skill behind ``action_type``.

        Returns ``{"status": "ok"|"error", "detail": <speakable result>}``.
        The idempotency key (Parker's staged-action id) lets the bridge
        refuse accidental duplicate side effects; Parker itself never
        retries an invocation.
        """

        data = self._request(
            "POST",
            "/parker/v1/skills/invoke",
            {
                "action_type": action_type,
                "payload": payload,
                "idempotency_key": idempotency_key,
            },
        )
        if not isinstance(data, dict) or data.get("status") not in {"ok", "error"}:
            raise GatewayError("OpenClaw gateway skill invocation reply was malformed")
        return data


def _proposal_from_dict(data: dict[str, Any]) -> ProposedAction:
    return ProposedAction(
        action_type=str(data.get("action_type", "")),
        label=str(data.get("label", "")).strip(),
        subject=str(data.get("subject", "")).strip(),
        intent_text=str(data.get("intent_text", "")).strip(),
        recipient=(str(data["recipient"]).strip() if data.get("recipient") else None),
    )


class OpenClawBrainAdapter:
    """BrainAdapter over the OpenClaw gateway's chat-completions endpoint."""

    def __init__(self, gateway: OpenClawGateway) -> None:
        self._gateway = gateway

    def respond(
        self,
        history: list[Message],
        utterance: str,
        context: BrainContext,
    ) -> BrainReply:
        messages: list[dict[str, str]] = [{"role": "system", "content": _system_prompt(context)}]
        messages.extend({"role": m.role, "content": m.content} for m in history)
        messages.append({"role": "user", "content": utterance})

        data = self._gateway.chat(messages, tools=[OPENAI_PROPOSE_ACTION_TOOL])
        message = data["choices"][0].get("message") or {}

        proposals: list[ProposedAction] = []
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            if function.get("name") != "propose_action":
                continue
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                continue  # a malformed proposal is dropped, never a crash
            if isinstance(arguments, dict):
                proposals.append(_proposal_from_dict(arguments))

        content = message.get("content") or ""
        for match in _PROPOSE_TAG.finditer(content):
            try:
                tagged = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            if isinstance(tagged, dict):
                proposals.append(_proposal_from_dict(tagged))
        speech = _PROPOSE_TAG.sub("", content).strip()

        return BrainReply(speech=speech, proposed_actions=tuple(proposals))


class FallbackBrain:
    """Degrade gracefully when the OpenClaw gateway is down.

    First failure speaks a one-time notice, then the fallback brain (the
    Claude adapter when configured) answers; with no fallback, Parker says
    honestly what still works. Only ``GatewayError`` triggers fallback —
    anything else propagates to the voice loop's own containment.
    """

    NOTICE = "Heads up — I can't reach my main brain right now, so I'm using my backup."
    NO_FALLBACK_SPEECH = (
        "I can't reach my answers right now. I can still set reminders, "
        "draft family messages, and start exercises — just tell me."
    )

    def __init__(self, primary: Any, fallback: Any | None = None) -> None:
        self._primary = primary
        self._fallback = fallback
        self._noticed = False

    def respond(
        self,
        history: list[Message],
        utterance: str,
        context: BrainContext,
    ) -> BrainReply:
        try:
            return self._primary.respond(history, utterance, context)
        except GatewayError:
            notice = "" if self._noticed else FallbackBrain.NOTICE
            self._noticed = True
            if self._fallback is None:
                speech = f"{notice} {FallbackBrain.NO_FALLBACK_SPEECH}".strip()
                return BrainReply(speech=speech)
            reply = self._fallback.respond(history, utterance, context)
            if notice:
                return BrainReply(
                    speech=f"{notice} {reply.speech}".strip(),
                    proposed_actions=reply.proposed_actions,
                )
            return reply


def build_openclaw_gateway() -> Optional[OpenClawGateway]:
    """The configured gateway client, or None (zero-config default)."""

    from app.config import settings

    if not settings.parker_openclaw_gateway_url:
        return None
    return OpenClawGateway(
        settings.parker_openclaw_gateway_url,
        token=settings.parker_openclaw_gateway_token,
    )
