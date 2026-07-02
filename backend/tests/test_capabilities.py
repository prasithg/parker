"""Capability-level administration: family contacts + outbox autonomy.

Trust model under test (docs/runbook.md, capability administration):
the admin enables WHO Parker may message once; within that allowlist the
patient's own confirmation releases a message — recorded as capability
policy and visible in review, never silent. Off-allowlist recipients and
the no-contacts default keep today's per-message caregiver approval gate.
No send transport exists anywhere: released messages stay on this machine.
"""

from datetime import datetime

from fastapi.testclient import TestClient

from app.config import settings
from app.db.models import CallLog, CapturedIntent, OutboxMessage
from app.main import app
from app.parker.contacts import (
    RELEASED_BY_CAPABILITY_POLICY,
    asr_bias_words,
    family_contacts,
    is_allowlisted_recipient,
    lexicon_names,
)
from app.parker.pipeline import (
    approve_outbox_message,
    cancel_outbox_message,
    confirm_staged_action,
    execute_staged_action,
    resolve_captured_intents,
    stage_resolved_actions,
)

NOW = datetime(2026, 7, 1, 19, 0, 0)


# ---------------------------------------------------------------------------
# The contacts store and the derived lexicon (no drift between the two)
# ---------------------------------------------------------------------------


def test_no_contacts_configured_by_default(monkeypatch):
    monkeypatch.setattr(settings, "parker_family_contacts", "")

    assert family_contacts() == ()
    assert is_allowlisted_recipient("Sarah") is False
    assert is_allowlisted_recipient(None) is False


def test_contacts_parse_trim_and_dedup(monkeypatch):
    monkeypatch.setattr(settings, "parker_family_contacts", " Sarah , Michael,, sarah , Priya ")

    assert family_contacts() == ("Sarah", "Michael", "Priya")


def test_allowlist_match_is_case_insensitive(monkeypatch):
    monkeypatch.setattr(settings, "parker_family_contacts", "Sarah, Michael")

    assert is_allowlisted_recipient("sarah") is True
    assert is_allowlisted_recipient(" SARAH ") is True
    assert is_allowlisted_recipient("Dave") is False


def test_lexicon_names_derive_from_contacts_plus_lexicon(monkeypatch):
    """Enabling a contact automatically makes the name recognizable."""

    monkeypatch.setattr(settings, "parker_family_contacts", "Sarah, Michael")
    monkeypatch.setattr(settings, "personal_lexicon", "Priya, physio, tomato plants, sarah")

    # Contacts first; capitalized single-word lexicon entries follow;
    # case-insensitive dedup keeps the contact spelling canonical.
    assert lexicon_names() == ("Sarah", "Michael", "Priya")


def test_lexicon_names_without_contacts_match_previous_behavior(monkeypatch):
    monkeypatch.setattr(settings, "parker_family_contacts", "")
    monkeypatch.setattr(settings, "personal_lexicon", "Sarah, Michael, Priya, Anna")

    assert lexicon_names() == ("Sarah", "Michael", "Priya", "Anna")


def test_asr_bias_words_include_contacts_and_all_lexicon_words(monkeypatch):
    monkeypatch.setattr(settings, "parker_family_contacts", "Sarah")
    monkeypatch.setattr(settings, "personal_lexicon", "physio, tomato plants")

    assert asr_bias_words() == ("Sarah", "physio", "tomato plants")


def test_whisper_bias_prompt_includes_contact_names(monkeypatch):
    from app.voice.transcribe import lexicon_initial_prompt

    monkeypatch.setattr(settings, "parker_family_contacts", "Sarah, Priya")
    monkeypatch.setattr(settings, "personal_lexicon", "physio")

    prompt = lexicon_initial_prompt()
    assert prompt is not None
    assert "Sarah" in prompt and "Priya" in prompt and "physio" in prompt

    monkeypatch.setattr(settings, "parker_family_contacts", "")
    monkeypatch.setattr(settings, "personal_lexicon", "")
    assert lexicon_initial_prompt() is None


def test_recipient_canonicalization_recognizes_contact_names(monkeypatch):
    """Configuring the message capability is one act: allowlist + recognition."""

    from app.conversation.textloop import canonicalize_recipient

    monkeypatch.setattr(settings, "parker_family_contacts", "Priya")
    monkeypatch.setattr(settings, "personal_lexicon", "")

    # ASR mangle snaps to the contact's canonical spelling.
    assert canonicalize_recipient("pria") == ("Priya", True)


# ---------------------------------------------------------------------------
# Outbox autonomy: release within the capability, gate at the edges
# ---------------------------------------------------------------------------


def _staged_message(db, recipient="Sarah", text="Physio went well today."):
    call = CallLog(call_sid="CA_CAPABILITY", call_type="text_loop")
    db.add(call)
    db.commit()
    captured = CapturedIntent(
        call_log_id=call.id,
        intent_text=text,
        requested_action="message",
        subject=f"message {recipient}",
        recipient=recipient,
        due_at=NOW,
    )
    db.add(captured)
    db.commit()
    resolve_captured_intents(db, now=NOW)
    return stage_resolved_actions(db, now=NOW)[0]


def test_confirmed_message_to_allowlisted_contact_auto_releases(db, monkeypatch):
    monkeypatch.setattr(settings, "parker_family_contacts", "Sarah, Michael")
    staged = _staged_message(db, recipient="Sarah")
    confirm_staged_action(db, staged.id, confirmed_by="patient", now=NOW)

    executed = execute_staged_action(db, staged.id, now=NOW)

    assert executed.status == "executed"
    assert "released" in executed.execution_result
    assert "no send transport" in executed.execution_result
    message = db.query(OutboxMessage).one()
    assert message.status == "released_local"
    assert message.released_at == NOW
    assert message.released_by == RELEASED_BY_CAPABILITY_POLICY
    assert message.approved_at is None  # release is policy, not a masked approval
    assert message.sent_at is None  # nothing left the machine


def test_off_allowlist_recipient_stays_approval_gated(db, monkeypatch):
    monkeypatch.setattr(settings, "parker_family_contacts", "Sarah")
    staged = _staged_message(db, recipient="Dave")
    confirm_staged_action(db, staged.id, confirmed_by="patient", now=NOW)

    executed = execute_staged_action(db, staged.id, now=NOW)

    assert executed.status == "executed"
    assert "awaiting family approval" in executed.execution_result
    message = db.query(OutboxMessage).one()
    assert message.status == "queued_local"
    assert message.released_at is None


def test_no_contacts_configured_behaves_like_today(db, monkeypatch):
    monkeypatch.setattr(settings, "parker_family_contacts", "")
    staged = _staged_message(db, recipient="Sarah")
    confirm_staged_action(db, staged.id, confirmed_by="patient", now=NOW)

    execute_staged_action(db, staged.id, now=NOW)

    message = db.query(OutboxMessage).one()
    assert message.status == "queued_local"
    assert message.released_at is None
    assert message.released_by is None


def test_released_message_is_still_cancellable(db, monkeypatch):
    monkeypatch.setattr(settings, "parker_family_contacts", "Sarah")
    staged = _staged_message(db, recipient="Sarah")
    confirm_staged_action(db, staged.id, confirmed_by="patient", now=NOW)
    execute_staged_action(db, staged.id, now=NOW)
    message = db.query(OutboxMessage).one()
    assert message.status == "released_local"

    cancelled = cancel_outbox_message(db, message.id, now=NOW)

    assert cancelled.status == "cancelled"
    assert cancelled.cancelled_at == NOW
    assert cancelled.sent_at is None


def test_approve_does_not_touch_released_rows(db, monkeypatch):
    monkeypatch.setattr(settings, "parker_family_contacts", "Sarah")
    staged = _staged_message(db, recipient="Sarah")
    confirm_staged_action(db, staged.id, confirmed_by="patient", now=NOW)
    execute_staged_action(db, staged.id, now=NOW)
    message = db.query(OutboxMessage).one()

    result = approve_outbox_message(db, message.id, now=NOW)

    # Released rows need no per-message approval; approve is a no-op on them.
    assert result.status == "released_local"
    assert result.approved_at is None


def test_review_feed_shows_released_bucket(db, monkeypatch):
    """The rearview mirror: the family SEES releases without gating them."""

    monkeypatch.setattr(settings, "parker_family_contacts", "Sarah")
    staged = _staged_message(db, recipient="Sarah")
    confirm_staged_action(db, staged.id, confirmed_by="patient", now=NOW)
    execute_staged_action(db, staged.id, now=NOW)
    client = TestClient(app)

    review = client.get("/parker/review").json()

    assert len(review["outbox_released"]) == 1
    released = review["outbox_released"][0]
    assert released["status"] == "released_local"
    assert released["released_by"] == RELEASED_BY_CAPABILITY_POLICY
    assert review["outbox_queued"] == []
    # The executed action also lands in recent history — what happened, visibly.
    assert any(
        item["recipient"] == "Sarah" and "released" in (item["execution_result"] or "")
        for item in review["recent_history"]
    )


def test_review_page_has_released_section(db):
    client = TestClient(app)

    page = client.get("/parker/review/ui").text

    assert "Released to family contacts" in page
    assert "outbox_released" in page
