"""Shared test fixtures."""

import pytest

pytest.register_assert_rewrite("scenario_harness")
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base, get_db
from app.main import app


@pytest.fixture
def db():
    """In-memory SQLite session for tests."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


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
