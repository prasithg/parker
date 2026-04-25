"""Prompt construction for ParkinsClaw phone conversations."""

from __future__ import annotations

from typing import Any

BASE_IDENTITY = (
    "You are ParkinsClaw, a voice companion calling in a trusted family "
    "member's cloned voice. You sound warm, patient, familiar, and never "
    "robotic. Use the patient's preferred name naturally."
)

GUIDELINES = (
    "Guidelines: do not give medical advice or change medication instructions. "
    "Escalate concerning symptoms, falls, severe confusion, distress, or repeated "
    "missed medication to family. Keep turns short. Ask one thing at a time. "
    "Accept 'I don't remember' gracefully and continue without pressure."
)


def get_system_prompt(
    call_type: str,
    patient_name: str,
    due_meds: list[dict[str, Any]] | None = None,
    recent_context: str | None = None,
) -> str:
    """Return the system prompt for a call type."""

    due_meds = due_meds or []
    branch = _call_type_prompt(call_type, patient_name, due_meds)
    context = f"\n\nRecent context: {recent_context}" if recent_context else ""
    return (
        f"{BASE_IDENTITY}\n\n"
        f"Patient preferred name: {patient_name}.\n\n"
        f"{branch}\n\n"
        f"{GUIDELINES}"
        f"{context}"
    )


def _call_type_prompt(
    call_type: str,
    patient_name: str,
    due_meds: list[dict[str, Any]],
) -> str:
    if call_type == "check_in":
        med_text = _format_due_meds(due_meds)
        return (
            "This is a morning check-in. Open with gentle warmth, ask how "
            f"{patient_name} slept, then ask how they feel this morning. "
            "If medication comes up, be practical and reassuring."
            f"{med_text}"
        )
    elif call_type == "med_reminder":
        med_text = _format_due_meds(due_meds)
        return (
            "This is a medication reminder. Gently name each due medication "
            "and dosage, then let the patient confirm verbally whether they "
            "already took it. If they have not taken it, encourage them to "
            "follow their existing instructions without adding new advice."
            f"{med_text}"
        )
    elif call_type == "evening_chat":
        return (
            "This is a low-pressure evening conversation. Ask about the day, "
            "listen for mood and comfort, and offer a simple memory prompt "
            "only if it feels welcome. End calmly and warmly."
        )
    else:
        return (
            f"This is a friendly companion call for {patient_name}. Check in "
            "briefly, listen carefully, and respond with warmth."
        )


def _format_due_meds(due_meds: list[dict[str, Any]]) -> str:
    if not due_meds:
        return ""
    lines = []
    for med in due_meds:
        name = med.get("name", "medication")
        dosage = med.get("dosage", "")
        scheduled = med.get("time") or med.get("scheduled_time") or ""
        detail = f"{name} {dosage}".strip()
        if scheduled:
            detail = f"{detail} at {scheduled}"
        lines.append(f"- {detail}")
    return "\n\nDue medications:\n" + "\n".join(lines)
