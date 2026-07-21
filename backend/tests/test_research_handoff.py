"""Local research-handoff cards seeded by repaired public-audio queries."""
import inspect as python_inspect
import json
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from app.config import settings
from app.conversation.repair_events import RepairEvent
from app.conversation.textloop import TextSession, UtteranceContext
from app.db.database import create_tables
from app.db.models import CallLog, CapturedIntent, StagedAction
from app.main import app
from app.parker.research_handoff import (
    DEFAULT_QUERY_RETENTION_DAYS,
    REDACTED_TEXT,
    LocalResearchHandoff,
    redact_expired_local_research_handoffs,
)

NOW = datetime(2026, 7, 19, 8, 0, 0)
PRIMARY = "Please give me information on Martin Jackson."
ALTERNATE = "Please give me information on Michael Jackson."
REDACTION_CREDS = ("family", "open-sesame")


def _enable_redaction_auth(monkeypatch):
    monkeypatch.setattr(settings, "dashboard_username", REDACTION_CREDS[0])
    monkeypatch.setattr(settings, "dashboard_password", REDACTION_CREDS[1])


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
    assert card.retention_expires_at == card.created_at + timedelta(
        days=DEFAULT_QUERY_RETENTION_DAYS
    )
    assert card.redacted_at is None
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
        "retention_expires_at": card["retention_expires_at"],
        "query_redacted": False,
        "redacted_at": None,
        "redacted_by": None,
        "redaction_reason": None,
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


def test_research_handoff_redaction_removes_query_and_consented_repair_copy(
    db, monkeypatch
):
    _enable_redaction_auth(monkeypatch)
    monkeypatch.setattr(settings, "repair_event_capture_consented", True)
    session = _session(db, sid="CA_RESEARCH_HANDOFF_REDACT")
    _offer_repaired_person_query(session)
    card_id = session.handle("1")["research_handoff_id"]
    client = TestClient(app)
    completed_at = datetime(2026, 7, 19, 8, 15, 0)
    repair_event = db.query(RepairEvent).one()
    assert "Martin Jackson" in repair_event.utterance
    assert "Michael Jackson" in repair_event.alternates_json
    client.post(
        f"/parker/research-handoffs/{card_id}/complete",
        json={"completed_by": "caregiver", "now": completed_at.isoformat()},
        auth=REDACTION_CREDS,
    )
    before_redaction = datetime.utcnow()

    redacted = client.post(
        f"/parker/research-handoffs/{card_id}/redact",
        json={"confirmed": True},
        auth=REDACTION_CREDS,
    )
    after_redaction = datetime.utcnow()

    assert redacted.status_code == 200
    payload = redacted.json()
    assert payload["query"] == REDACTED_TEXT
    assert payload["selected_interpretation"] == REDACTED_TEXT
    assert payload["query_redacted"] is True
    redacted_at = datetime.fromisoformat(payload["redacted_at"])
    assert before_redaction <= redacted_at <= after_redaction
    assert payload["redacted_by"] == "family"
    assert payload["redaction_reason"] == "manual_caregiver_redaction"
    assert payload["status"] == "completed"
    assert payload["completed_at"] == completed_at.isoformat()
    assert payload["completed_by"] == "caregiver"
    assert payload["cancelled_at"] is None
    db.refresh(repair_event)
    assert repair_event.utterance == REDACTED_TEXT
    assert json.loads(repair_event.alternates_json) == []
    assert json.loads(repair_event.offered_choices_json) == []
    assert repair_event.selected_label == REDACTED_TEXT

    # Redaction is idempotent and cannot rewrite its actor/time audit.
    repeated = client.post(
        f"/parker/research-handoffs/{card_id}/redact",
        json={"confirmed": True},
        auth=REDACTION_CREDS,
    ).json()
    assert repeated["redacted_at"] == payload["redacted_at"]
    assert repeated["redacted_by"] == "family"

    missing = client.post(
        "/parker/research-handoffs/9999/redact",
        json={"confirmed": True},
        auth=REDACTION_CREDS,
    )
    assert missing.status_code == 404


def test_manual_query_redaction_requires_configured_auth_and_confirmation(db, monkeypatch):
    session = _session(db, sid="CA_RESEARCH_HANDOFF_REDACT_CONFIRM")
    _offer_repaired_person_query(session)
    card_id = session.handle("1")["research_handoff_id"]
    client = TestClient(app)

    no_auth_config = client.post(
        f"/parker/research-handoffs/{card_id}/redact",
        json={"confirmed": True},
    )
    assert no_auth_config.status_code == 503
    assert db.get(LocalResearchHandoff, card_id).query != REDACTED_TEXT

    _enable_redaction_auth(monkeypatch)
    response = client.post(
        f"/parker/research-handoffs/{card_id}/redact",
        json={"confirmed": False},
        auth=REDACTION_CREDS,
    )

    assert response.status_code == 400
    assert "irreversible" in response.json()["detail"]
    assert db.get(LocalResearchHandoff, card_id).query != REDACTED_TEXT


def test_privacy_schema_migration_preserves_and_backfills_existing_cards():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE local_research_handoffs ("
                "id INTEGER PRIMARY KEY, call_log_id INTEGER, query TEXT NOT NULL, "
                "selected_interpretation VARCHAR(256) NOT NULL, repair_family VARCHAR(64) NOT NULL, "
                "source_kind VARCHAR(64), provenance_status VARCHAR(128), risk_label VARCHAR(128), "
                "status VARCHAR(16), created_at DATETIME, completed_at DATETIME, "
                "completed_by VARCHAR(64), cancelled_at DATETIME, cancelled_by VARCHAR(64))"
            )
        )
        connection.execute(
            text(
                "INSERT INTO local_research_handoffs "
                "(id, call_log_id, query, selected_interpretation, repair_family, status, created_at) "
                "VALUES (1, 7, 'existing private query', 'existing interpretation', "
                "'person_entity', 'completed', '2026-06-01 08:00:00')"
            )
        )

    create_tables(bind=engine)

    columns = {column["name"] for column in inspect(engine).get_columns("local_research_handoffs")}
    assert {
        "repair_event_id",
        "retention_expires_at",
        "redacted_at",
        "redacted_by",
        "redaction_reason",
    }.issubset(columns)
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT query, status, retention_expires_at "
                "FROM local_research_handoffs WHERE id = 1"
            )
        ).mappings().one()
    assert row["query"] == "existing private query"
    assert row["status"] == "completed"
    assert str(row["retention_expires_at"]).startswith("2026-07-01")

    # A stacked-diff database can also link the previously captured repair copy
    # on the next startup without guessing across calls or interpretations.
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO repair_events "
                "(id, call_log_id, utterance, alternates_json, offered_choices_json, "
                "selected_position, selected_label, created_at) VALUES "
                "(9, 7, 'existing utterance', '[\"alternate\"]', '[{\"label\": \"existing interpretation\"}]', "
                "2, 'existing interpretation', '2026-06-01 08:00:01')"
            )
        )

    # Re-running startup migration is idempotent and never resets the row.
    create_tables(bind=engine)
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT COUNT(*) FROM local_research_handoffs")
        ).scalar_one() == 1
        assert connection.execute(
            text("SELECT repair_event_id FROM local_research_handoffs WHERE id = 1")
        ).scalar_one() == 9


def test_running_app_registers_startup_and_hourly_retention_maintenance():
    import app.main as main_module

    source = python_inspect.getsource(main_module.lifespan)
    assert "run_research_handoff_retention(SessionLocal)" in source
    assert 'id="parker-research-handoff-retention"' in source
    assert '"interval"' in source
    assert "hours=1" in source


def test_expired_research_queries_redact_automatically_without_rewriting_status(db):
    old_session = _session(db, sid="CA_RESEARCH_HANDOFF_EXPIRED")
    _offer_repaired_person_query(old_session)
    old_id = old_session.handle("1")["research_handoff_id"]
    old_card = db.get(LocalResearchHandoff, old_id)
    old_card.created_at = datetime(2026, 6, 1, 8, 0, 0)
    old_card.retention_expires_at = datetime(2026, 7, 1, 8, 0, 0)
    db.commit()

    current_session = _session(db, sid="CA_RESEARCH_HANDOFF_CURRENT")
    _offer_repaired_person_query(current_session)
    current_id = current_session.handle("1")["research_handoff_id"]

    redacted = redact_expired_local_research_handoffs(
        db, now=datetime(2026, 7, 21, 8, 0, 0)
    )

    assert [card.id for card in redacted] == [old_id]
    assert db.get(LocalResearchHandoff, old_id).query == REDACTED_TEXT
    assert db.get(LocalResearchHandoff, old_id).status == "ready"
    assert db.get(LocalResearchHandoff, old_id).redacted_by == "retention_policy"
    assert db.get(LocalResearchHandoff, old_id).redaction_reason == "retention_window_expired"
    assert db.get(LocalResearchHandoff, current_id).query != REDACTED_TEXT


def test_open_tick_cannot_use_injected_future_time_to_redact_queries(db):
    session = _session(db, sid="CA_RESEARCH_HANDOFF_TICK_TIME")
    _offer_repaired_person_query(session)
    card_id = session.handle("1")["research_handoff_id"]

    tick = TestClient(app).post("/parker/tick", json={"now": "2099-01-01T00:00:00"})

    assert tick.status_code == 200
    assert tick.json()["research_handoffs_redacted"] == 0
    assert db.get(LocalResearchHandoff, card_id).query != REDACTED_TEXT


def test_review_feed_enforces_expired_query_retention_before_serializing(db):
    session = _session(db, sid="CA_RESEARCH_HANDOFF_REVIEW_RETENTION")
    _offer_repaired_person_query(session)
    card_id = session.handle("1")["research_handoff_id"]
    card = db.get(LocalResearchHandoff, card_id)
    card.retention_expires_at = datetime(2026, 7, 1, 8, 0, 0)
    db.commit()

    payload = TestClient(app).get("/parker/review").json()["research_handoffs"][0]

    assert payload["query"] == REDACTED_TEXT
    assert payload["selected_interpretation"] == REDACTED_TEXT
    assert payload["query_redacted"] is True
    assert payload["redacted_by"] == "retention_policy"
    assert payload["status"] == "ready"

    # Once the query is gone, completion cannot imply research happened; the
    # still-local card may only be cancelled as lifecycle cleanup.
    complete = TestClient(app).post(
        f"/parker/research-handoffs/{card_id}/complete",
        json={"completed_by": "caregiver"},
    ).json()
    assert complete["status"] == "ready"
    assert complete["completed_at"] is None
    cancelled = TestClient(app).post(
        f"/parker/research-handoffs/{card_id}/cancel",
        json={"cancelled_by": "caregiver"},
    ).json()
    assert cancelled["status"] == "cancelled"


def test_research_handoff_ui_has_visible_keyboard_buttons_and_no_external_action(db):
    page = TestClient(app).get("/parker/review/ui").text

    assert "Research handoffs" in page
    assert "research_handoffs" in page
    assert "/parker/research-handoffs/${h.id}/complete" in page
    assert "/parker/research-handoffs/${h.id}/cancel" in page
    assert "/parker/research-handoffs/${h.id}/redact" in page
    assert "Mark research complete" in page
    assert "Cancel research card" in page
    assert "Redact query now" in page
    assert "window.confirm('Redact this query and selected interpretation? This cannot be undone.')" in page
    assert "confirmed: true" in page
    assert "Configure DASHBOARD_PASSWORD to enable confirmed manual redaction" in page
    assert "within the hourly maintenance window after 30 days" in page
    assert "No live fetch, purchase, submission, account change, or external message" in page
    assert "${h.query}" not in page
    assert "query.textContent = h.query" in page
    assert "completed ${h.completed_at ?? '—'} by ${h.completed_by ?? '—'}" in page
    assert "cancelled ${h.cancelled_at ?? '—'} by ${h.cancelled_by ?? '—'}" in page
    assert "redacted ${h.redacted_at ?? '—'} by ${h.redacted_by ?? '—'}" in page


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
