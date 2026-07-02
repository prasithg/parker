"""The family handoff digest: sections, framing, and hard boundaries.

What these pin down, per the roadmap acceptance criteria: every section
renders from real local rows (reminders executed, released/queued outbox,
exercise and evening sessions, escalation candidates, failures, changed
minds); released messages are framed as *what happened* under the
capability trust model — awareness, never an approval ask; the artifact
contains no credentials and no medical advice; and the module has no
external send path at all.
"""

import inspect
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

import app.parker.digest as digest_module
from app.config import settings
from app.db.models import CallLog, OutboxMessage, StagedAction
from app.demo.replay import replay_transcript
from app.demo.seed import seed_demo_data
from app.escalation.candidates import flag_non_response_candidates
from app.evening.session import complete_local_evening_session, start_local_evening_session
from app.exercises.session import start_local_exercise_session
from app.main import app
from app.parker.digest import (
    build_digest,
    render_digest_markdown,
    write_digest_file,
)
from app.parker.pipeline import (
    cancel_outbox_message,
    cancel_staged_action,
    capture_intent,
    confirm_staged_action,
    execute_staged_action,
    get_due_resurfaced_actions,
    resolve_captured_intents,
    stage_resolved_actions,
)

client = TestClient(app)

NOW = datetime(2026, 7, 2, 12, 0)


def _call(db, call_sid="DIGEST-TEST"):
    call = CallLog(call_sid=call_sid, call_type="text_loop")
    db.add(call)
    db.commit()
    db.refresh(call)
    return call


def _stage(db, call, *, requested_action, subject, intent_text, recipient=None, when=NOW):
    capture_intent(
        db,
        call_log_id=call.id,
        intent_text=intent_text,
        requested_action=requested_action,
        subject=subject,
        recipient=recipient,
        due_at=when,
    )
    resolve_captured_intents(db, now=when)
    return stage_resolved_actions(db, now=when)[0]


# ---------------------------------------------------------------------------
# What happened: events inside the window
# ---------------------------------------------------------------------------


def test_executed_reminders_appear_in_window_and_old_ones_drop_out(db):
    call = _call(db)
    recent = _stage(
        db, call, requested_action="remind", subject="call the pharmacy",
        intent_text="Remind me to call the pharmacy.", when=NOW - timedelta(hours=3),
    )
    confirm_staged_action(db, recent.id, confirmed_by="patient", now=NOW - timedelta(hours=2.5))
    execute_staged_action(db, recent.id, now=NOW - timedelta(hours=2))

    stale = _stage(
        db, call, requested_action="remind", subject="water the ferns",
        intent_text="Remind me to water the ferns.", when=NOW - timedelta(hours=73),
    )
    confirm_staged_action(db, stale.id, confirmed_by="patient", now=NOW - timedelta(hours=72.5))
    execute_staged_action(db, stale.id, now=NOW - timedelta(hours=72))

    digest = build_digest(db, now=NOW)
    subjects = [item["subject"] for item in digest["what_happened"]["reminders_done"]]
    assert subjects == ["call the pharmacy"]  # three-day-old execution is out

    text = render_digest_markdown(digest)
    assert "Reminder done: “call the pharmacy” (confirmed by patient)." in text
    assert "water the ferns" not in text


def test_released_message_is_what_happened_not_an_approval_item(db, monkeypatch):
    monkeypatch.setattr(settings, "parker_family_contacts", "Sarah")
    call = _call(db)
    action = _stage(
        db, call, requested_action="message", subject="message Sarah",
        intent_text="The physio went well today.", recipient="Sarah",
        when=NOW - timedelta(hours=1),
    )
    confirm_staged_action(db, action.id, confirmed_by="patient", now=NOW - timedelta(minutes=50))
    execute_staged_action(db, action.id, now=NOW - timedelta(minutes=45))
    assert db.query(OutboxMessage).one().status == "released_local"

    digest = build_digest(db, now=NOW)
    released = digest["what_happened"]["messages_released"]
    assert [m["recipient"] for m in released] == ["Sarah"]
    assert digest["needs_review"]["outbox_queued"] == []

    text = render_digest_markdown(digest)
    happened, needs_a_look = text.split("## Needs a look")
    # The rearview mirror: the release reads as an event that happened...
    assert "released to family contacts on Dad's own confirmation" in happened
    assert "still on this machine" in happened
    # ...and never as something waiting on the family.
    assert "Sarah" not in needs_a_look
    assert "approval" not in happened.split("## What happened")[1]


def test_queued_message_needs_a_look_even_when_older_than_the_window(db):
    call = _call(db)
    action = _stage(
        db, call, requested_action="message", subject="message Rohan",
        intent_text="Dinner on Sunday would be lovely.", recipient="Rohan",
        when=NOW - timedelta(hours=1),
    )
    confirm_staged_action(db, action.id, confirmed_by="patient", now=NOW - timedelta(minutes=50))
    execute_staged_action(db, action.id, now=NOW - timedelta(minutes=45))
    message = db.query(OutboxMessage).one()
    assert message.status == "queued_local"
    message.created_at = NOW - timedelta(days=3)  # an old, still-open decision
    db.commit()

    digest = build_digest(db, now=NOW)
    queued = digest["needs_review"]["outbox_queued"]
    assert [m["recipient"] for m in queued] == ["Rohan"]

    text = render_digest_markdown(digest)
    assert "Message to Rohan waiting for caregiver approval" in text
    assert "not on the family-contacts allowlist" in text


def test_approved_message_counts_as_happened(db):
    call = _call(db)
    action = _stage(
        db, call, requested_action="message", subject="message Rohan",
        intent_text="Bridge night moved to Thursday.", recipient="Rohan",
        when=NOW - timedelta(hours=2),
    )
    confirm_staged_action(db, action.id, confirmed_by="patient", now=NOW - timedelta(hours=2))
    execute_staged_action(db, action.id, now=NOW - timedelta(hours=2))
    from app.parker.pipeline import approve_outbox_message

    approve_outbox_message(
        db, db.query(OutboxMessage).one().id, approved_by="caregiver", now=NOW - timedelta(hours=1)
    )

    digest = build_digest(db, now=NOW)
    assert [m["recipient"] for m in digest["what_happened"]["messages_approved"]] == ["Rohan"]
    assert "approved by caregiver (still on this machine)" in render_digest_markdown(digest)


def test_exercise_sessions_section(db):
    session = start_local_exercise_session(
        db, subject="speech exercise: strong voice", now=NOW - timedelta(hours=2)
    )
    from app.exercises.session import complete_local_exercise_session

    complete_local_exercise_session(db, session.id, now=NOW - timedelta(hours=1.5))

    digest = build_digest(db, now=NOW)
    sessions = digest["what_happened"]["exercise_sessions"]
    assert [s["subject"] for s in sessions] == ["speech exercise: strong voice"]
    assert sessions[0]["status"] == "completed"
    assert "Exercise “speech exercise: strong voice” — completed." in render_digest_markdown(digest)


def test_evening_sessions_section(db):
    session = start_local_evening_session(db, now=NOW - timedelta(hours=14))
    complete_local_evening_session(db, session.id, now=NOW - timedelta(hours=13))

    digest = build_digest(db, now=NOW)
    evenings = digest["what_happened"]["evening_sessions"]
    assert len(evenings) == 1
    assert evenings[0]["status"] == "completed"
    assert "Evening check-in" in render_digest_markdown(digest)


def test_changed_mind_covers_cancelled_actions_and_messages(db):
    call = _call(db)
    action = _stage(
        db, call, requested_action="remind", subject="set up the card table",
        intent_text="Remind me to set up the card table.", when=NOW - timedelta(hours=5),
    )
    cancel_staged_action(db, action.id, cancelled_by="patient", now=NOW - timedelta(hours=4))

    message_action = _stage(
        db, call, requested_action="message", subject="message Rohan",
        intent_text="Never mind the card table.", recipient="Rohan",
        when=NOW - timedelta(hours=3),
    )
    confirm_staged_action(db, message_action.id, confirmed_by="patient", now=NOW - timedelta(hours=3))
    execute_staged_action(db, message_action.id, now=NOW - timedelta(hours=3))
    cancel_outbox_message(db, db.query(OutboxMessage).one().id, now=NOW - timedelta(hours=2))

    digest = build_digest(db, now=NOW)
    whats = [item["what"] for item in digest["what_happened"]["changed_mind"]]
    assert 'reminder “set up the card table”' in whats
    assert "message to Rohan" in whats

    text = render_digest_markdown(digest)
    assert "Changed their mind: reminder “set up the card table” cancelled by patient." in text


# ---------------------------------------------------------------------------
# Needs a look: open items, review-only framing
# ---------------------------------------------------------------------------


def test_escalation_candidate_reads_as_review_only(db):
    call = _call(db, call_sid="DEMO-CANDIDATE")
    _stage(
        db, call, requested_action="remind", subject="afternoon stretches",
        intent_text="Remind me to do the afternoon stretches.", when=NOW - timedelta(hours=4),
    )
    for offset in (3, 2.5, 2):
        get_due_resurfaced_actions(db, now=NOW - timedelta(hours=offset))
    created = flag_non_response_candidates(db, now=NOW)
    assert created

    digest = build_digest(db, now=NOW)
    candidates = digest["needs_review"]["escalation_candidates"]
    assert len(candidates) == 1
    assert "afternoon stretches" in candidates[0]["reason"]

    text = render_digest_markdown(digest)
    assert "Non-response candidate" in text
    assert "Review only — no notification was dispatched." in text


def test_failed_action_is_flagged_and_never_hidden(db):
    call = _call(db)
    action = _stage(
        db, call, requested_action="remind", subject="hindi songs",
        intent_text="Remind me about hindi songs.", when=NOW - timedelta(hours=1),
    )
    action.status = "failed"
    action.execution_result = "openclaw skill failed (no retry was attempted): gateway error"
    db.commit()

    digest = build_digest(db, now=NOW)
    assert digest["needs_review"]["failed_actions"][0]["subject"] == "hindi songs"
    assert "Failed and not retried" in render_digest_markdown(digest)


def test_staged_action_waits_for_confirmation(db):
    call = _call(db)
    _stage(
        db, call, requested_action="remind", subject="water the tomato plants",
        intent_text="Remind me to water the tomato plants.", when=NOW - timedelta(minutes=10),
    )

    text = render_digest_markdown(build_digest(db, now=NOW))
    assert "Waiting for a confirmation: reminder “water the tomato plants”" in text


# ---------------------------------------------------------------------------
# Framing, empties, and the hard boundaries
# ---------------------------------------------------------------------------


def test_empty_digest_degrades_gracefully_with_all_sections(db):
    text = render_digest_markdown(build_digest(db, now=NOW))

    for heading in ("# Parker family digest — 2026-07-02", "## What happened",
                    "## Needs a look", "## What stayed local"):
        assert heading in text
    assert "A quiet day — nothing recorded in this window." in text
    assert "Nothing needs a decision right now." in text
    assert "awareness for the family, not an approval queue" in text


def test_digest_from_the_full_demo_has_no_credentials_and_no_medical_advice(db):
    seed_demo_data(db, now=NOW)
    replay_transcript(db)
    resolve_captured_intents(db, now=NOW)
    stage_resolved_actions(db, now=NOW)

    text = render_digest_markdown(build_digest(db, now=NOW)).lower()

    for secret_marker in ("password", "passcode", "api key", "api_key", "token",
                          "secret", "credential", "private key", "ssn"):
        assert secret_marker not in text, f"digest leaked a credential marker: {secret_marker}"
    # Events only, never recommendations: the demo includes a refused
    # medication question and a pharmacy reminder; neither may become advice.
    for advice_marker in ("you should", "should take", "recommend", "increase your",
                          "double your", "half your pills", "diagnos", "treatment"):
        assert advice_marker not in text, f"digest contains advice-like wording: {advice_marker}"
    # The demo's known in-window events do appear as events. (The seeded
    # pharmacy reminder executed ~26h ago — correctly outside the window.)
    assert "changed their mind" in text
    assert "message to sarah" in text
    assert "reminder done" not in text


def test_digest_module_has_no_external_send_path():
    source = inspect.getsource(digest_module)
    for forbidden in ("import smtplib", "import requests", "import httpx",
                      "import urllib", "import socket", "import twilio",
                      "requests.", "httpx.", "smtplib."):
        assert forbidden not in source, f"digest module must stay send-free: {forbidden}"


def test_write_digest_file_creates_the_local_artifact(tmp_path):
    path = write_digest_file("# Parker family digest — 2026-07-02\n", date="2026-07-02",
                             directory=tmp_path)

    assert path == tmp_path / "parker-digest-2026-07-02.md"
    assert path.read_text(encoding="utf-8").startswith("# Parker family digest")


# ---------------------------------------------------------------------------
# Surfaces: endpoint, review-UI link, make target
# ---------------------------------------------------------------------------


def test_digest_endpoint_serves_the_rendered_digest(db):
    response = client.get("/parker/digest")

    assert response.status_code == 200
    assert "What happened" in response.text
    assert "never sent" in response.text


def test_digest_endpoint_sits_behind_the_caregiver_auth_seam(db, monkeypatch):
    monkeypatch.setattr(settings, "dashboard_password", "open-sesame")

    assert client.get("/parker/digest").status_code == 401


def test_review_ui_links_to_the_digest(db):
    page = client.get("/parker/review/ui").text

    assert 'href="/parker/digest"' in page
    assert "family digest" in page


def test_make_digest_target_exists():
    from pathlib import Path

    makefile = (Path(__file__).resolve().parents[2] / "Makefile").read_text()
    assert "\ndigest: backend-venv" in makefile
    assert "app.parker.digest" in makefile
