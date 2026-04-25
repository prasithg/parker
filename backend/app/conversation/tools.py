"""OpenAI tool definitions and database-backed tool handlers."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.db.models import CallLog, Medication, MoodEntry
from app.meds.tracker import log_dose

logger = logging.getLogger("parkinsclaw.tools")

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "log_medication",
            "description": "Record whether the patient confirmed taking a scheduled medication dose.",
            "parameters": {
                "type": "object",
                "properties": {
                    "medication_id": {"type": "integer"},
                    "scheduled_time": {
                        "type": "string",
                        "description": "Scheduled dose time in HH:MM format.",
                    },
                    "confirmed": {"type": "boolean"},
                    "response_text": {"type": "string"},
                },
                "required": ["medication_id", "confirmed"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_mood",
            "description": "Record the patient's current mood and optional notes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mood": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["mood"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cognitive_exercise",
            "description": "Start a brief cognitive exercise such as a word game, memory prompt, or trivia.",
            "parameters": {
                "type": "object",
                "properties": {
                    "exercise_type": {
                        "type": "string",
                        "enum": ["word_game", "memory_recall", "trivia"],
                    }
                },
                "required": ["exercise_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_family",
            "description": "Log an escalation for family review when something concerning is reported.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                    "severity": {
                        "type": "string",
                        "enum": ["info", "warning", "urgent"],
                    },
                },
                "required": ["reason", "severity"],
            },
        },
    },
]

# Compatibility with the initial scaffold.
ALL_TOOLS = TOOL_DEFINITIONS


def handle_log_medication(
    db: Session,
    call_log_id: int,
    medication_id: int,
    confirmed: bool,
    response_text: str | None = None,
    scheduled_time: str | None = None,
    patient_response: str | None = None,
) -> dict[str, Any]:
    """Persist a medication confirmation from the conversation."""

    medication = db.get(Medication, medication_id)
    if medication is None:
        return {"status": "error", "message": "Medication not found"}

    dose = log_dose(
        db,
        call_log_id=call_log_id,
        medication_id=medication_id,
        scheduled_time=scheduled_time or _first_schedule_time(medication) or "",
        confirmed=confirmed,
        patient_response=response_text or patient_response,
    )
    return {
        "status": "logged",
        "dose_log_id": dose.id,
        "medication_id": medication_id,
        "confirmed": confirmed,
    }


def handle_record_mood(
    db: Session,
    call_log_id: int,
    mood: str,
    notes: str | None = None,
) -> dict[str, Any]:
    """Persist a mood entry and mirror the latest mood on the call log."""

    entry = MoodEntry(call_log_id=call_log_id, mood=mood, notes=notes)
    db.add(entry)
    call = db.get(CallLog, call_log_id)
    if call:
        call.patient_mood = mood
    db.commit()
    db.refresh(entry)
    return {"status": "recorded", "mood_entry_id": entry.id, "mood": mood}


def handle_cognitive_exercise(
    db: Session,
    call_log_id: int,
    exercise_type: str,
) -> dict[str, Any]:
    """Return a short exercise prompt for the agent to use."""

    del db, call_log_id
    prompts = {
        "word_game": "Let's name a few fruits together. What is one fruit you like?",
        "memory_recall": "I'll say three words: garden, penny, radio. Can you say them back?",
        "trivia": "Here's a gentle trivia question: what season comes after spring?",
    }
    return {
        "status": "started",
        "exercise_type": exercise_type,
        "prompt": prompts.get(exercise_type, prompts["word_game"]),
    }


def handle_escalate_to_family(
    db: Session,
    call_log_id: int,
    reason: str,
    severity: str,
) -> dict[str, Any]:
    """Record an escalation signal for v0 through logs and call summary text."""

    call = db.get(CallLog, call_log_id)
    alert_text = f"Family escalation ({severity}): {reason}"
    if call:
        existing = call.summary or ""
        call.summary = f"{existing}\n{alert_text}".strip()
        db.commit()
    logger.warning("Escalation for call %s [%s]: %s", call_log_id, severity, reason)
    return {
        "status": "escalated",
        "severity": severity,
        "reason": reason,
        "recorded_at": datetime.utcnow().isoformat(),
    }


TOOL_HANDLERS: dict[str, Callable[..., dict[str, Any]]] = {
    "log_medication": handle_log_medication,
    "record_mood": handle_record_mood,
    "cognitive_exercise": handle_cognitive_exercise,
    "escalate_to_family": handle_escalate_to_family,
}


def execute_tool(
    db: Session,
    call_log_id: int,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Execute a named tool handler with normalized error handling."""

    handler = TOOL_HANDLERS.get(tool_name)
    if handler is None:
        return {"status": "error", "message": f"Unknown tool: {tool_name}"}
    try:
        return handler(db, call_log_id, **arguments)
    except Exception as exc:
        logger.exception("Tool failed: %s", tool_name)
        return {"status": "error", "message": str(exc)}


def _first_schedule_time(medication: Medication) -> str | None:
    import json

    try:
        values = json.loads(medication.schedule_times)
    except json.JSONDecodeError:
        return None
    if isinstance(values, list) and values:
        return str(values[0])
    return None
