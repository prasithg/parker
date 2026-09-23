"""Shared test fixtures."""

import pytest

pytest.register_assert_rewrite("scenario_harness")
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db.database import Base, get_db
from app.main import app


@pytest.fixture
def db(tmp_path_factory):
    """One file-backed SQLite database per test, shaped like production.

    This used to be ``sqlite:///:memory:`` on a ``StaticPool`` — ONE
    connection shared by every Session on every thread. SQLite then had no
    isolation to offer: a reader's ``Session.close()`` (a ROLLBACK on the
    shared connection) silently discarded a writer's in-flight transaction
    on another thread, with no exception — the mechanism behind the
    "unreproducible" realtime/converse flakes and the false-green
    concurrent-session test (independent review, 2026-09-01). A real file
    gives every Session its own connection, so a rollback only ever
    touches its own transaction and concurrent writers queue on SQLite's
    lock instead of corrupting each other. WAL keeps readers from blocking
    writers; the busy timeout makes contention a wait, never a crash.

    Teardown disposes the engine instead of ``drop_all``: a threadpool
    thread that outlives its test cannot race a table drop when the file is
    simply discarded with pytest's temp tree. The file lives in its own
    directory, not ``tmp_path`` — tests use ``tmp_path`` as PARKER_HOME
    and assert on its contents.
    """

    db_dir = tmp_path_factory.mktemp("parker-db")
    engine = create_engine(
        f"sqlite:///{db_dir / 'parker-test.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=OFF")  # a throwaway per-test file
        cursor.close()

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _discard_test_databases(tmp_path_factory):
    """Each test writes a ~0.5 MB database; pytest keeps three basetemps.
    Delete ours at session end (never per test, where a straggling thread
    could still be writing) so a busy day does not hold gigabytes."""

    yield
    import shutil

    root = tmp_path_factory.getbasetemp()
    for path in root.glob("parker-db*"):
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(autouse=True)
def override_get_db(db):
    """Route tests use the same in-memory session fixture."""

    def _get_db():
        yield db

    app.dependency_overrides[get_db] = _get_db
    try:
        yield
    finally:
        app.dependency_overrides.clear()


# The scenario gauntlet's world fixture, registered globally so every
# tests/test_scenarios_*.py file can request `voice_world` directly.
import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(__file__))
from scenario_harness import voice_world  # noqa: E402,F401


@pytest.fixture(autouse=True)
def keyless_settings(monkeypatch):
    """The pytest suite is keyless by design (docs/brain-adapters.md).

    A developer's live keys in backend/.env must never leak real API calls
    into tests — found live when the realtime no-key test opened a real
    OpenAI socket. Tests that want a (fake) key set it explicitly; autouse
    fixtures run first, so per-test monkeypatches still win.
    """

    from app.config import settings

    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "parker_openclaw_gateway_url", "")
    yield


@pytest.fixture(autouse=True)
def reset_hands():
    """No test inherits another test's (fake) OpenClaw hands registry."""

    from app.parker import hands

    hands.configure_hands(None)
    try:
        yield
    finally:
        hands.configure_hands(None)
