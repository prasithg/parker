"""Conversation agent for v0 phone-call flows."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from sqlalchemy.orm import Session

from app.config import settings
from app.conversation.prompts import get_system_prompt
from app.conversation.tools import TOOL_DEFINITIONS, execute_tool
from app.db.models import CallLog, DoseLog
from app.meds.models import CallSummary
from app.meds.tracker import get_due_medications

logger = logging.getLogger("parker.agent")

Message = dict[str, Any]


class ConversationAgent:
    """Manage in-memory conversation history and OpenAI tool execution."""

    def __init__(
        self,
        db_session_factory: Callable[[], Session] | Session,
        openai_client: Any | None = None,
    ) -> None:
        self.db_session_factory = db_session_factory
        self.openai_client = openai_client
        self.histories: dict[int, list[Message]] = {}

    def start_conversation(
        self,
        call_log_id: int,
        call_type: str,
        patient_name: str,
    ) -> list[Message]:
        """Initialize a call transcript and return initial messages."""

        with self._session() as db:
            due_meds = [
                {
                    "id": medication.id,
                    "name": medication.name,
                    "dosage": medication.dosage,
                    "time": scheduled_time,
                }
                for medication, scheduled_time in get_due_medications(db)
            ]

        system_prompt = get_system_prompt(
            call_type=call_type,
            patient_name=patient_name,
            due_meds=due_meds,
        )
        assistant_text = self._initial_assistant_text(call_type, patient_name, due_meds)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "assistant", "content": assistant_text},
        ]
        self.histories[call_log_id] = messages
        return messages

    def handle_user_turn(self, call_log_id: int, user_text: str) -> str:
        """Process a user utterance and return the assistant's next text."""

        messages = self.histories.setdefault(
            call_log_id,
            [
                {
                    "role": "system",
                    "content": get_system_prompt("check_in", settings.patient_name),
                }
            ],
        )
        messages.append({"role": "user", "content": user_text})

        if not self._has_openai_client():
            assistant_text = self._stub_response(user_text)
            messages.append({"role": "assistant", "content": assistant_text})
            return assistant_text

        response = self._chat_completion(messages, tools=TOOL_DEFINITIONS)
        message = response.choices[0].message
        tool_calls = getattr(message, "tool_calls", None) or []

        if tool_calls:
            messages.append(_message_to_dict(message))
            with self._session() as db:
                for tool_call in tool_calls:
                    args = json.loads(tool_call.function.arguments or "{}")
                    result = execute_tool(db, call_log_id, tool_call.function.name, args)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps(result),
                        }
                    )
            response = self._chat_completion(messages, tools=TOOL_DEFINITIONS)
            message = response.choices[0].message

        assistant_text = getattr(message, "content", None) or "I'm here with you."
        messages.append({"role": "assistant", "content": assistant_text})
        return assistant_text

    def end_conversation(self, call_log_id: int) -> CallSummary:
        """Summarize transcript, update the call log, and return a call summary."""

        transcript = self.histories.get(call_log_id, [])
        summary_text = self._summarize(transcript)
        with self._session() as db:
            call = db.get(CallLog, call_log_id)
            if call is None:
                return CallSummary(
                    call_id=call_log_id,
                    started_at=None,
                    duration_seconds=None,
                    summary=summary_text,
                    mood=None,
                    dose_logs=[],
                )
            call.summary = summary_text
            db.commit()
            db.refresh(call)
            return _call_summary(call)

    # Realtime API integration belongs here in a later version.

    @contextmanager
    def _session(self) -> Iterator[Session]:
        if isinstance(self.db_session_factory, Session):
            yield self.db_session_factory
            return
        db = self.db_session_factory()
        try:
            yield db
        finally:
            db.close()

    def _has_openai_client(self) -> bool:
        if self.openai_client is not None:
            return True
        return bool(settings.openai_api_key)

    def _client(self) -> Any:
        if self.openai_client is None:
            from openai import OpenAI

            self.openai_client = OpenAI(api_key=settings.openai_api_key)
        return self.openai_client

    def _chat_completion(self, messages: list[Message], tools: list[dict[str, Any]] | None = None):
        kwargs: dict[str, Any] = {
            "model": "gpt-4o-mini",
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
        return self._client().chat.completions.create(**kwargs)

    def _summarize(self, transcript: list[Message]) -> str:
        content = [
            message
            for message in transcript
            if message.get("role") in {"user", "assistant"} and message.get("content")
        ]
        if not content:
            return "Call ended with no conversation."
        if not self._has_openai_client():
            last_user = next(
                (m["content"] for m in reversed(content) if m.get("role") == "user"),
                "",
            )
            if last_user:
                return f"Brief call completed. Last patient response: {last_user[:160]}"
            return "Brief call completed."
        response = self._chat_completion(
            transcript
            + [
                {
                    "role": "user",
                    "content": (
                        "Summarize this call in 2-3 sentences. Include mood, "
                        "medication confirmations, and concerns for family."
                    ),
                }
            ]
        )
        return response.choices[0].message.content or "Summary unavailable."

    @staticmethod
    def _initial_assistant_text(
        call_type: str,
        patient_name: str,
        due_meds: list[dict[str, Any]],
    ) -> str:
        if call_type == "med_reminder" and due_meds:
            meds = ", ".join(
                f"{med['name']} {med['dosage']}".strip() for med in due_meds
            )
            return f"Hi {patient_name}, just a gentle reminder about {meds}. Have you taken it yet?"
        if call_type == "evening_chat":
            return f"Hi {patient_name}, I wanted to hear how your day has been."
        return f"Good morning {patient_name}, how are you feeling today?"

    @staticmethod
    def _stub_response(user_text: str) -> str:
        lowered = user_text.lower()
        if "yes" in lowered or "taken" in lowered or "took" in lowered:
            return "Thank you for letting me know. How are you feeling right now?"
        if "no" in lowered or "forgot" in lowered:
            return "That's okay. Please follow your usual instructions, and we can let family know if you need help."
        return "I hear you. Tell me a little more when you're ready."


def _message_to_dict(message: Any) -> Message:
    if hasattr(message, "model_dump"):
        return message.model_dump(exclude_none=True)
    if isinstance(message, dict):
        return message
    return {
        "role": "assistant",
        "content": getattr(message, "content", None),
        "tool_calls": getattr(message, "tool_calls", None),
    }


def _call_summary(call: CallLog) -> CallSummary:
    return CallSummary(
        call_id=call.id,
        started_at=call.started_at,
        duration_seconds=call.duration_seconds,
        summary=call.summary,
        mood=call.patient_mood,
        dose_logs=[_dose_log_payload(dose) for dose in call.dose_logs],
    )


def _dose_log_payload(dose: DoseLog) -> dict[str, Any]:
    return {
        "id": dose.id,
        "medication_id": dose.medication_id,
        "scheduled_time": dose.scheduled_time,
        "confirmed": dose.confirmed,
        "confirmed_at": dose.confirmed_at,
        "patient_response": dose.patient_response,
    }
