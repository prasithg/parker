"""Model-driven repair-choice generation.

suggest_repair_candidates wraps a Claude (haiku) call with a hard
never-raises contract: every error path falls back to generic hardcoded
candidates so the conversation continues. Tests use a fake client that
returns controlled responses — no API calls in the suite.
"""

from __future__ import annotations

import json

import pytest

from app.conversation.repair import (
    _FALLBACK_CANDIDATES,
    suggest_repair_candidates,
)
from app.conversation.textloop import TextSession
from app.db.models import CapturedIntent


# ---------------------------------------------------------------------------
# Fake Anthropic client
# ---------------------------------------------------------------------------


class _Content:
    def __init__(self, text):
        self.text = text


class _Response:
    def __init__(self, text):
        self.content = [_Content(text)]


class FakeAnthropic:
    """Minimal fake for anthropic.Anthropic.messages.create."""

    def __init__(self, response_json: list | None = None, raises: Exception | None = None):
        self._response_json = response_json
        self._raises = raises
        self.calls: list[dict] = []

    @property
    def messages(self):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return _Response(json.dumps(self._response_json))


def _good_client(label1="remind you to call Dr Smith", label2="send your daughter a message about this"):
    return FakeAnthropic([
        {"label": label1, "action_type": "reminder"},
        {"label": label2, "action_type": "family_message"},
    ])


# ---------------------------------------------------------------------------
# suggest_repair_candidates unit tests
# ---------------------------------------------------------------------------


def test_returns_model_candidates_when_client_provided():
    client = _good_client("remind you to call Dr Smith", "send Priya a message about this")
    result = suggest_repair_candidates("call the doctor thing", client=client)

    assert len(result) == 2
    assert result[0] == ("remind you to call Dr Smith", "reminder")
    assert result[1] == ("send Priya a message about this", "family_message")
    assert len(client.calls) == 1
    assert client.calls[0]["model"] == "claude-haiku-4-5-20251001"


def test_falls_back_when_no_client_and_no_key(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "anthropic_api_key", "")

    result = suggest_repair_candidates("the thing you know")
    assert result == list(_FALLBACK_CANDIDATES)


def test_falls_back_on_api_error():
    client = FakeAnthropic(raises=RuntimeError("network timeout"))
    result = suggest_repair_candidates("call the thing", client=client)
    assert result == list(_FALLBACK_CANDIDATES)


def test_falls_back_on_malformed_json():
    class _BadClient:
        class messages:
            @staticmethod
            def create(**kwargs):
                class _R:
                    content = [_Content("not json at all")]
                return _R()

    result = suggest_repair_candidates("something vague", client=_BadClient())
    assert result == list(_FALLBACK_CANDIDATES)


def test_falls_back_when_model_returns_wrong_count():
    client = FakeAnthropic([{"label": "only one", "action_type": "reminder"}])
    result = suggest_repair_candidates("something vague", client=client)
    assert result == list(_FALLBACK_CANDIDATES)


def test_falls_back_when_model_returns_unsafe_action_type():
    client = FakeAnthropic([
        {"label": "change your medication dose", "action_type": "medication_change"},
        {"label": "remind you", "action_type": "reminder"},
    ])
    result = suggest_repair_candidates("pills", client=client)
    assert result == list(_FALLBACK_CANDIDATES)


def test_falls_back_when_label_over_length():
    long_label = "a" * 81
    client = FakeAnthropic([
        {"label": long_label, "action_type": "reminder"},
        {"label": "send a message", "action_type": "family_message"},
    ])
    result = suggest_repair_candidates("something", client=client)
    assert result == list(_FALLBACK_CANDIDATES)


def test_model_prompt_includes_utterance():
    client = _good_client()
    suggest_repair_candidates("call the garden neighbour", client=client)
    call = client.calls[0]
    assert "call the garden neighbour" in call["messages"][0]["content"]


# ---------------------------------------------------------------------------
# TextSession integration: model client wired end-to-end
# ---------------------------------------------------------------------------


def test_text_session_uses_model_client_for_vague_utterance(db):
    call_log = __import__("app.db.models", fromlist=["CallLog"]).CallLog(
        call_sid="TEST-MODEL-REPAIR", call_type="text_loop"
    )
    db.add(call_log)
    db.commit()
    db.refresh(call_log)

    client = _good_client("remind you to call your neighbour", "message your neighbour")
    session = TextSession(db, call_log.id, model_client=client)
    result = session.handle("Call... the... you know... the one with the garden...")

    assert result["kind"] == "choices"
    assert any("neighbour" in c["label"] for c in result["choices"])
    assert len(client.calls) == 1


def test_text_session_falls_back_gracefully_when_no_client(db):
    """No model_client → hardcoded fallback → valid choices offered, conversation continues."""
    call_log = __import__("app.db.models", fromlist=["CallLog"]).CallLog(
        call_sid="TEST-FALLBACK-REPAIR", call_type="text_loop"
    )
    db.add(call_log)
    db.commit()
    db.refresh(call_log)

    session = TextSession(db, call_log.id)  # no model_client
    result = session.handle("Call... the... you know... the one with the garden...")

    assert result["kind"] == "choices"
    assert len(result["choices"]) == 3  # 2 candidates + none-of-these


def test_text_session_model_then_selection_captures_intent(db):
    """Model offers specific choices; user picks one; intent captured correctly."""
    call_log = __import__("app.db.models", fromlist=["CallLog"]).CallLog(
        call_sid="TEST-PICK-MODEL", call_type="text_loop"
    )
    db.add(call_log)
    db.commit()
    db.refresh(call_log)

    client = _good_client("remind you to call Dr Smith", "send your family a message")
    session = TextSession(db, call_log.id, model_client=client)

    r1 = session.handle("you know... the doctor... the one")
    assert r1["kind"] == "choices"

    r2 = session.handle("1")  # pick "remind you to call Dr Smith"
    assert r2["kind"] == "captured"

    intent = db.query(CapturedIntent).one()
    assert intent.requested_action == "reminder"


# ---------------------------------------------------------------------------
# _build_model_client auto-wiring
# ---------------------------------------------------------------------------


def test_build_model_client_returns_none_when_no_key(monkeypatch):
    from app.config import settings
    from app.conversation.textloop import _build_model_client

    monkeypatch.setattr(settings, "anthropic_api_key", "")
    assert _build_model_client() is None


def test_build_model_client_instantiates_client_when_key_set(monkeypatch):
    import sys

    from app.config import settings
    from app.conversation.textloop import _build_model_client

    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test-key")

    class _FakeAnthropicModule:
        class Anthropic:
            def __init__(self, api_key):
                self.api_key = api_key

    monkeypatch.setitem(sys.modules, "anthropic", _FakeAnthropicModule)
    client = _build_model_client()
    assert client is not None
    assert client.api_key == "sk-ant-test-key"


def test_build_model_client_returns_none_on_import_error(monkeypatch):
    import sys

    from app.config import settings
    from app.conversation.textloop import _build_model_client

    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test-key")
    monkeypatch.setitem(sys.modules, "anthropic", None)  # simulate missing package
    # Should not raise — graceful None return
    assert _build_model_client() is None
