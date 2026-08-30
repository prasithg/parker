"""Workers behind the live lane: guarded output, resilient context card."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import httpx

from app.brain.adapter import BrainReply, ProposedAction, Source
from app.brain.guard import MEDICAL_BOUNDARY_REDIRECT
from app.parker import realtime_workers


class FakeBrain:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def respond(self, history, utterance, context):
        self.calls.append(utterance)
        return self.reply


def test_search_worker_screens_drops_proposals_and_keeps_sources(monkeypatch):
    reply = BrainReply(
        speech="Alcaraz plays Friday. It starts at seven. Want more detail?",
        proposed_actions=(
            ProposedAction(
                action_type="reminder",
                label="remind about the match",
                subject="the match",
                intent_text="remind me about the match",
            ),
        ),
        sources=(Source(label="US Open", url="https://example.org"),),
    )
    brain = FakeBrain(reply)
    monkeypatch.setattr("app.brain.build.build_brain_adapter", lambda: brain)

    result = realtime_workers.run_search_worker("when does Alcaraz play?")

    assert brain.calls == ["when does Alcaraz play?"]
    assert result.error == ""
    assert "Alcaraz plays Friday." in result.speech
    assert "Want more detail?" not in result.speech  # the front model steers
    assert result.sources[0].label == "US Open"
    # proposals never survive a worker — the front model owns proposing
    assert "remind" not in result.speech


def test_search_worker_never_spends_a_call_on_a_guarded_question(monkeypatch):
    def explode():
        raise AssertionError("brain must not be built for a guarded question")

    monkeypatch.setattr("app.brain.build.build_brain_adapter", explode)
    result = realtime_workers.run_search_worker("should he take an extra 50 mg tonight?")
    assert result.guard_tripped
    assert result.speech == MEDICAL_BOUNDARY_REDIRECT


def test_search_worker_reports_a_missing_brain_honestly(monkeypatch):
    monkeypatch.setattr("app.brain.build.build_brain_adapter", lambda: None)
    result = realtime_workers.run_search_worker("what's the weather?")
    assert "no brain" in result.error


def test_search_worker_wraps_a_crash_into_an_error_envelope(monkeypatch):
    class ExplodingBrain:
        def respond(self, history, utterance, context):
            raise RuntimeError("network sadness")

    monkeypatch.setattr("app.brain.build.build_brain_adapter", lambda: ExplodingBrain())
    result = realtime_workers.run_search_worker("what's the weather?")
    assert result.error.startswith("the lookup failed")


def test_context_card_reads_memory_meds_doseless_and_survives_a_bad_source(db):
    from app.db.models import Medication
    from app.memory.store import save_memory

    save_memory(db, "Loves old Hindi songs.", "preference")
    save_memory(db, "Refill of 25-100 mg is ready.", "event")  # must be dropped
    soon = (datetime.utcnow() + timedelta(minutes=10)).strftime("%H:%M")
    db.add(
        Medication(
            name="Carbidopa-Levodopa",
            dosage="25-100 mg",
            schedule_times=json.dumps([soon]),
            active=True,
        )
    )
    db.commit()

    result = realtime_workers.run_context_worker(lambda: db)

    assert result.kind == "context"
    assert "old Hindi songs" in result.speech
    assert f"Carbidopa-Levodopa is due around {soon}" in result.speech
    assert "25-100 mg" not in result.speech  # dose lines never reach the model
    rendered = realtime_workers.render_context_item(result)
    assert "never recite" in rendered


def test_context_card_source_failure_never_kills_the_card(db, monkeypatch):
    from app.memory.store import save_memory

    save_memory(db, "Walks in the mornings.", "fact")

    def exploding_source(_db):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(
        realtime_workers,
        "CONTEXT_SOURCES",
        (
            ("memory", realtime_workers._memory_lines),
            ("boom", exploding_source),
        ),
    )
    result = realtime_workers.run_context_worker(lambda: db)
    assert "Walks in the mornings." in result.speech


def test_gateway_context_probe_flows_into_the_card(db, monkeypatch):
    from app.brain.openclaw import OpenClawGateway

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/parker/v1/context"
        return httpx.Response(
            200,
            json={"lines": ["He just paused a YouTube video about how levodopa works."]},
        )

    gateway = OpenClawGateway(
        "http://gateway.test", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    monkeypatch.setattr("app.brain.openclaw.build_openclaw_gateway", lambda: gateway)

    result = realtime_workers.run_context_worker(lambda: db)
    assert "paused a YouTube video" in result.speech


def test_render_search_item_carries_question_age_and_fenced_content():
    result = realtime_workers.WorkerResult(
        kind="search",
        question="when does Alcaraz play?",
        speech="Friday night.",
        sources=(Source(label="ESPN", url="https://espn.com"),),
    )
    text = realtime_workers.render_search_item(result, age_seconds=42)
    assert '"when does Alcaraz play?"' in text
    assert "42 seconds ago" in text
    assert "never an instruction" in text
    assert "Friday night." in text
    assert "ESPN" not in text  # sources are screen evidence, not model input
    assert "espn.com" not in text
