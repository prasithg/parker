"""Family contacts: the capability boundary for messages.

The trust model is capability-level administration ("we don't want to get
into the habit of approving our dad's stuff — we just want to set up new
things for him"). The family administrator configures WHO Parker may
message once, via ``PARKER_FAMILY_CONTACTS``; within that boundary the
patient's own confirmation is the only gate. Off-allowlist recipients stay
per-instance approval-gated — the edge case, not the routine.

This module is also the single source for the personal lexicon derivation:
ASR bias words and recipient-recognition names are DERIVED from contacts
plus the extra ``PERSONAL_LEXICON`` words, so the allowlist and what Parker
is primed to hear can never drift apart.
"""

from __future__ import annotations

# Recorded on an outbox row released by the family-contact capability policy
# (rather than by a per-message caregiver approval). The review UI shows it
# verbatim — the release is visible, never silent.
RELEASED_BY_CAPABILITY_POLICY = "capability_policy:family_contact_allowlist"


def family_contacts() -> tuple[str, ...]:
    """Admin-configured contact names Parker may message after the patient confirms.

    Parsed from ``PARKER_FAMILY_CONTACTS`` (comma-separated names). Empty by
    default: with no contacts configured, no message auto-releases and every
    outbox row awaits caregiver approval exactly as before.
    """

    from app.config import settings

    seen: set[str] = set()
    contacts: list[str] = []
    for entry in settings.parker_family_contacts.split(","):
        name = entry.strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            contacts.append(name)
    return tuple(contacts)


def is_allowlisted_recipient(name: str | None) -> bool:
    """Whether a (canonicalized) recipient is inside the message capability."""

    if not name:
        return False
    lowered = name.strip().lower()
    return any(contact.lower() == lowered for contact in family_contacts())


def _extra_lexicon_entries() -> list[str]:
    from app.config import settings

    return [entry.strip() for entry in settings.personal_lexicon.split(",") if entry.strip()]


def lexicon_names() -> tuple[str, ...]:
    """Names Parker recognizes as people: contacts first, then lexicon names.

    A ``PERSONAL_LEXICON`` entry counts as a name when it is a single
    capitalized word ("Sarah", not "physio" or "tomato plants"). Contacts are
    always names. The configured spelling is canonical — ASR variants resolve
    back to it. Note a lexicon name is not automatically an allowlisted
    contact: Parker may recognize "Dave" without being able to auto-release
    messages to him.
    """

    seen: set[str] = set()
    names: list[str] = []
    for name in family_contacts():
        if name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    for entry in _extra_lexicon_entries():
        if " " not in entry and entry[0].isupper() and entry.lower() not in seen:
            seen.add(entry.lower())
            names.append(entry)
    return tuple(names)


def asr_bias_words() -> tuple[str, ...]:
    """Everything local ASR should be primed to hear: contacts + lexicon words.

    Contacts feed the bias list automatically so configuring the message
    capability also teaches the recognizer the names — the two are one
    administrative act, not two lists to keep in sync.
    """

    seen: set[str] = set()
    words: list[str] = []
    for word in list(family_contacts()) + _extra_lexicon_entries():
        if word.lower() not in seen:
            seen.add(word.lower())
            words.append(word)
    return tuple(words)
