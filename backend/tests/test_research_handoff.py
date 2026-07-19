"""Local research-handoff cards seeded by repaired public-audio queries."""

from datetime import datetime

from fastapi.testclient import TestClient

from app.conversation.textloop import TextSession, UtteranceContext
from app.db.models import CallLog, CapturedIntent, StagedAction
from app.main import app
from app.parker.research_handoff import LocalResearchHandoff

NOW = datetime(2026, 7, 19, 8, 0, 0)
PRIMARY = "Please give me information on Martin Jackson."
ALTERNATE = "Please give me information on Michael Jackson."


def _session(db, sid="CA_RESEARCH_HANDOFF"):
    call = CallLog(call_sid=sid, call_type="wake_context")
    db.add(call)
    db.commit()
    db.refresh(call)
    return TextSession(db, call.id)


def _offer_repaired_person_query(session):
    context = UtteranceContext(addressed_to_parker=True, source="wake_confirmed")
    offered = session.handle(PRIMARY, alternates=[ALTERNATE], context=context)
    assert offered["kind"] == "choices"
    return session.handle("2", context=context)


def test_repaired_information_query_requires_explicit_local_handoff_confirmation(db):
    session = _session(db)

    answered = _offer_repaired_person_query(session)

    assert answered["kind"] == "answer"
    assert answered["resolved_query"] == "Tell me about Michael Jackson."
    assert answered["research_handoff_offered"] is True
    assert [choice["label"] for choice in answered["research_handoff_choices"]] == [
        "leave a local research card for family",
        "do not create a card",
    ]
    assert db.query(LocalResearchHandoff).count() == 0

    created = session.handle("yes, go ahead")

    assert created["kind"] == "research_handoff_created"
    card = db.query(LocalResearchHandoff).one()
    assert created["research_handoff_id"] == card.id
    assert card.query == "Tell me about Michael Jackson."
    assert card.selected_interpretation == "information about Michael Jackson"
    assert card.repair_family == "person_entity"
    assert card.source_kind == "local_asr_nbest_repair"
    assert card.provenance_status == "user_confirmed_interpretation_no_external_source_fetched"
    assert card.risk_label == "read_only_research_no_external_action"
    assert card.status == "ready"
    assert db.query(CapturedIntent).count() == 0
    assert db.query(StagedAction).count() == 0


def test_research_handoff_none_of_these_creates_no_card(db):
    session = _session(db, sid="CA_RESEARCH_HANDOFF_SKIP")
    answered = _offer_repaired_person_query(session)
    assert answered["research_handoff_offered"] is True

    skipped = session.handle("no thanks")

    assert skipped["kind"] == "research_handoff_skipped"
    assert db.query(LocalResearchHandoff).count() == 0
    assert db.query(CapturedIntent).count() == 0


def test_research_handoff_review_feed_and_lifecycle_are_local_and_terminal(db):
    session = _session(db, sid="CA_RESEARCH_HANDOFF_REVIEW")
    _offer_repaired_person_query(session)
    created = session.handle("1")
    card_id = created["research_handoff_id"]
    client = TestClient(app)

    review = client.get("/parker/review").json()
    card = review["research_handoffs"][0]
    assert card == {
        "id": card_id,
        "query": "Tell me about Michael Jackson.",
        "selected_interpretation": "information about Michael Jackson",
        "repair_family": "person_entity",
        "source_kind": "local_asr_nbest_repair",
        "provenance_status": "user_confirmed_interpretation_no_external_source_fetched",
        "risk_label": "read_only_research_no_external_action",
        "status": "ready",
        "created_at": card["created_at"],
        "completed_at": None,
        "cancelled_at": None,
        "completed_by": None,
        "cancelled_by": None,
    }

    completed = client.post(
        f"/parker/research-handoffs/{card_id}/complete",
        json={"completed_by": "caregiver", "now": NOW.isoformat()},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["completed_by"] == "caregiver"
    assert completed.json()["completed_at"] == NOW.isoformat()

    # Terminal state: cancel cannot rewrite a completed handoff.
    cancelled_after_complete = client.post(
        f"/parker/research-handoffs/{card_id}/cancel",
        json={"cancelled_by": "caregiver", "now": "2026-07-19T09:00:00"},
    )
    assert cancelled_after_complete.status_code == 200
    assert cancelled_after_complete.json()["status"] == "completed"
    assert cancelled_after_complete.json()["cancelled_at"] is None


def test_research_handoff_cancel_and_missing_id(db):
    session = _session(db, sid="CA_RESEARCH_HANDOFF_CANCEL")
    _offer_repaired_person_query(session)
    card_id = session.handle("1")["research_handoff_id"]
    client = TestClient(app)

    cancelled = client.post(
        f"/parker/research-handoffs/{card_id}/cancel",
        json={"cancelled_by": "caregiver", "now": NOW.isoformat()},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["cancelled_by"] == "caregiver"

    for operation in ("complete", "cancel"):
        response = client.post(f"/parker/research-handoffs/9999/{operation}", json={})
        assert response.status_code == 404


def test_research_handoff_ui_has_visible_keyboard_buttons_and_no_external_action(db):
    page = TestClient(app).get("/parker/review/ui").text

    assert "Research handoffs" in page
    assert "research_handoffs" in page
    assert "/parker/research-handoffs/${h.id}/complete" in page
    assert "/parker/research-handoffs/${h.id}/cancel" in page
    assert "Mark research complete" in page
    assert "Cancel research card" in page
    assert "No live fetch, purchase, submission, account change, or external message" in page
    assert "${h.query}" not in page
    assert "query.textContent = h.query" in page


def test_research_handoff_module_has_no_external_capability_or_sensitive_fields():
    import inspect
    import app.db.database as database
    import app.parker.research_handoff as handoff_module

    source = inspect.getsource(handoff_module).lower()
    for forbidden_import in ("import requests", "import httpx", "urllib", "selenium", "playwright"):
        assert forbidden_import not in source
    assert "app.parker.research_handoff" in inspect.getsource(database.create_tables)
    column_names = {column.name for column in LocalResearchHandoff.__table__.columns}
    assert column_names.isdisjoint(
        {"url", "password", "credential", "payment", "account", "submission", "recipient"}
    )
