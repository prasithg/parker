"""Family notification dispatch for escalations."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Iterable

from app.config import settings
from app.escalation.models import Escalation

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FamilyContact:
    """Configured family/caregiver contact."""

    name: str
    phone: str = ""
    email: str = ""
    role: str = "family"

    @property
    def identifier(self) -> str:
        """Return a stable identifier for notification audit logs."""
        return self.phone or self.email or self.name


def get_family_contacts() -> list[FamilyContact]:
    """Return configured contacts from FAMILY_CONTACTS_JSON/settings fallback."""

    raw = getattr(settings, "family_contacts_json", "") or ""
    if not raw:
        import os

        raw = os.getenv("FAMILY_CONTACTS_JSON", "")
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Invalid FAMILY_CONTACTS_JSON; using no contacts")
        return []
    contacts: list[FamilyContact] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                contacts.append(
                    FamilyContact(
                        name=str(item.get("name") or item.get("phone") or item.get("email") or "Unknown"),
                        phone=str(item.get("phone") or ""),
                        email=str(item.get("email") or ""),
                        role=str(item.get("role") or "family"),
                    )
                )
    return contacts


def notify_contact(contact: FamilyContact, escalation: Escalation) -> bool:
    """Notify one contact; use Twilio when configured, otherwise structured log."""

    message = (
        f"ParkinsClaw escalation ({escalation.severity}): {escalation.reason} "
        f"[call {escalation.call_log_id}, escalation {escalation.id}]"
    )
    if settings.twilio_account_sid and settings.twilio_auth_token and contact.phone:
        try:
            from twilio.rest import Client

            client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
            client.messages.create(
                body=message,
                from_=settings.twilio_phone_number,
                to=contact.phone,
            )
            return True
        except Exception:
            logger.exception("Twilio escalation notification failed for %s", contact.identifier)
    logger.warning(
        "Escalation notification fallback",
        extra={
            "contact": contact.identifier,
            "role": contact.role,
            "escalation_id": escalation.id,
            "severity": escalation.severity,
            "reason": escalation.reason,
        },
    )
    return True


def dispatch_notifications(
    escalation: Escalation,
    contacts: Iterable[FamilyContact] | None = None,
) -> list[str]:
    """Notify contacts selected by severity and return notified identifiers."""

    selected = _contacts_for_severity(list(contacts if contacts is not None else get_family_contacts()), escalation.severity)
    notified: list[str] = []
    for contact in selected:
        if notify_contact(contact, escalation):
            notified.append(contact.identifier)
    return notified


def _contacts_for_severity(contacts: list[FamilyContact], severity: str) -> list[FamilyContact]:
    if severity == "info":
        return [contact for contact in contacts if contact.role == "primary_caregiver"]
    if severity in {"warning", "missed-dose"}:
        return [contact for contact in contacts if contact.role in {"primary_caregiver", "family"}]
    if severity == "urgent":
        return contacts
    return []
