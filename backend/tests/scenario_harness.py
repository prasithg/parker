"""Shared world-building for live-lane scenario tests (the scenario gauntlet).

A scenario test file does:

    from scenario_harness import *

and requests the ``voice_world`` fixture (registered globally via conftest).
``voice_world`` wires the safe test world: fake OpenAI key, DB seam onto the
in-memory engine, and teardown that waits for the bridge to drain. The
world object then builds whatever the scenario needs:

    world.seed_ravi()                     # the persona's memories/meds
    world.remember("...")                 # one extra memory line
    world.gateway(lines=[...])            # mock Hermes/OpenClaw context
    calls = world.enable_search({...})    # fake brain answers by substring
    fake = world.script([user_said(...), done()])
    with world.connect() as ws: ...

Event builders (`user_said`, `model_said`, `done`, `look_call`,
`propose_call`, ...) keep upstream scripts readable. Interleave with
``fake.feed(event)`` for ordering scenarios — that is where the real bugs
live (timer-vs-event races), so prefer feed() over pre-scripted lists
whenever ordering matters.

House rules for scenario files (match test_realtime.py):
- never sleep-and-hope: use _wait_until on an observable predicate;
- no real keys, no network — the conftest scrub plus these mocks is the law;
- a scenario asserts the BRIDGE CONTRACT (what gets injected, guarded,
  staged, persisted, sent to the browser) — never what gpt-realtime would
  say, which the fake upstream cannot know.
"""

from __future__ import annotations

import threading  # noqa: F401 — scenario files gate workers with threading.Event

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from app.parker.realtime_workers import WorkerResult
from test_realtime import (  # noqa: F401 — re-exported for scenario files
    FakeUpstream,
    _function_outputs,
    _look_done_event,
    _response_creates,
    _system_items,
    _wait_until,
    client,
)

__all__ = [
    "FakeUpstream",
    "ScenarioWorld",
    "WorkerResult",
    "_function_outputs",
    "_look_done_event",
    "_response_creates",
    "_system_items",
    "_wait_until",
    "audio_delta",
    "client",
    "context_cards",
    "done",
    "look_call",
    "lookup_notes",
    "model_said",
    "propose_call",
    "quick_timers",
    "speech_started",
    "speech_stopped",
    "upstream_error",
    "user_said",
    "voice_world",
]


# ---------------------------------------------------------------------------
# Upstream event builders
# ---------------------------------------------------------------------------


def user_said(text: str) -> dict:
    return {
        "type": "conversation.item.input_audio_transcription.completed",
        "transcript": text,
    }


def model_said(text: str) -> dict:
    return {"type": "response.output_audio_transcript.delta", "delta": text}


def audio_delta(data: str = "UENN") -> dict:
    return {"type": "response.output_audio.delta", "delta": data}


def speech_started() -> dict:
    return {"type": "input_audio_buffer.speech_started"}


def speech_stopped() -> dict:
    return {"type": "input_audio_buffer.speech_stopped"}


def done(*calls: dict) -> dict:
    """response.done carrying zero or more function_call output items."""

    return {"type": "response.done", "response": {"output": list(calls)}}


def look_call(question: str, call_id: str = "look-1") -> dict:
    import json

    return {
        "type": "function_call",
        "name": "look_that_up",
        "call_id": call_id,
        "arguments": json.dumps({"question": question}),
    }


def propose_call(arguments: dict, call_id: str = "prop-1") -> dict:
    import json

    return {
        "type": "function_call",
        "name": "propose_action",
        "call_id": call_id,
        "arguments": json.dumps(arguments),
    }


def upstream_error(message: str, code: str = "") -> dict:
    return {"type": "error", "error": {"code": code, "message": message}}


# ---------------------------------------------------------------------------
# Assertion helpers beyond test_realtime's
# ---------------------------------------------------------------------------


def lookup_notes(fake) -> list[str]:
    """Injected system items carrying a finished (or failed) lookup."""

    return [
        text
        for text in _system_items(fake)
        if "LOOKUP RESULT" in text or "could not finish" in text
    ]


def context_cards(fake) -> list[str]:
    return [text for text in _system_items(fake) if "Background context" in text]


def quick_timers(monkeypatch, *, wrapup=0.15, goodbye=0.15, drain=0.2, tick=0.05):
    """Shrink the idle ladder for watchdog scenarios."""

    from app.parker import realtime

    monkeypatch.setattr(realtime, "IDLE_WRAPUP_SECONDS", wrapup)
    monkeypatch.setattr(realtime, "IDLE_GOODBYE_SECONDS", goodbye)
    monkeypatch.setattr(realtime, "CLOSING_DRAIN_SECONDS", drain)
    monkeypatch.setattr(realtime, "_WATCHDOG_TICK_SECONDS", tick)


# ---------------------------------------------------------------------------
# The world
# ---------------------------------------------------------------------------


class ScenarioWorld:
    """One scenario's mocked universe: DB, gateway, brain, upstream."""

    def __init__(self, db, monkeypatch):
        self.db = db
        self.mp = monkeypatch
        self.fake: FakeUpstream | None = None
        self.search_calls: list[str] = []

    # -- upstream ------------------------------------------------------

    def script(self, events=()) -> FakeUpstream:
        from app.parker import realtime

        fake = FakeUpstream(list(events))

        async def connect():
            return fake

        self.mp.setattr(realtime, "connect_openai", connect)
        self.fake = fake
        return fake

    def connect(self):
        """Open the real websocket endpoint against the scripted upstream."""

        return client.websocket_connect("/parker/converse/realtime")

    def settle_open(self, fake, *, expect_card: bool = True) -> None:
        """Settle the session open: greeting done + context worker finished.

        ALWAYS call this before feeding DB-touching events (propose_call)
        in a seeded world: the test engine shares ONE SQLite connection
        (StaticPool), so the context worker's session racing the staging
        thread intermittently kills staging — a harness artifact, not a
        product behavior (production sessions get their own connections).
        """

        fake.feed(done())
        if expect_card:
            assert _wait_until(lambda: context_cards(fake)), "context card never arrived"

    # -- his world -----------------------------------------------------

    def seed_ravi(self):
        from app.demo.persona import seed_persona_data

        return seed_persona_data(self.db)

    def remember(self, content: str, memory_type: str = "fact"):
        from app.memory.store import save_memory

        return save_memory(self.db, content, memory_type)

    # -- the mocked Hermes/OpenClaw gateway ----------------------------

    def gateway(self, lines=(), *, down=False, skills=(), record=None):
        """Install a mock family-agent gateway.

        ``lines`` -> GET /parker/v1/context; ``down=True`` -> every request
        503s (the plan-for-not-getting-it case); ``skills`` -> enabled-skill
        list; ``record`` (a list) collects invoke payloads.
        """

        from app.brain.openclaw import OpenClawGateway

        def handler(request: httpx.Request) -> httpx.Response:
            if down:
                return httpx.Response(503, json={})
            if request.url.path == "/parker/v1/context":
                return httpx.Response(200, json={"lines": list(lines)})
            if request.url.path == "/parker/v1/skills":
                return httpx.Response(200, json={"skills": list(skills)})
            if request.url.path == "/parker/v1/skills/invoke":
                if record is not None:
                    import json as _json

                    record.append(_json.loads(request.content or b"{}"))
                return httpx.Response(200, json={"status": "ok", "detail": "done (mock)"})
            return httpx.Response(404, json={})

        gw = OpenClawGateway(
            "http://gw.test", client=httpx.Client(transport=httpx.MockTransport(handler))
        )
        self.mp.setattr("app.brain.openclaw.build_openclaw_gateway", lambda: gw)
        return gw

    def enable_hands(self, gw=None):
        """Register the gateway's skills as Parker's hands.

        Gateway-backed action types (media_playlist, open_links) are only
        proposable/executable while the hands registry holds an enabled
        skill — the conftest autouse reset clears it after each test. Pass
        the gateway you built with lines+skills, or omit for a default
        skills-only one.
        """

        from app.parker import hands as hands_module

        if gw is None:
            gw = self.gateway(
                skills=[
                    {
                        "name": "mock",
                        "action_types": ["media_playlist", "open_links"],
                        "enabled": True,
                    }
                ]
            )
        hands_module.configure_hands(hands_module.OpenClawHands.discover(gw))
        return gw

    # -- the mocked search brain ---------------------------------------

    def enable_search(self, answers=None, *, gate: "threading.Event | None" = None, error=None):
        """Offer look_that_up and fake its worker.

        ``answers``: dict of question-substring -> speech string or
        WorkerResult, or a callable(question) -> WorkerResult. ``gate``
        blocks the worker until the test releases it (ordering scenarios).
        ``error`` raises inside the worker. Returns the call list.
        """

        from app.config import settings
        from app.parker import realtime_workers

        self.mp.setattr(settings, "anthropic_api_key", "test-anthropic-key")
        calls = self.search_calls

        def fake_search(question: str) -> WorkerResult:
            calls.append(question)
            if gate is not None:
                gate.wait(timeout=3)
            if error is not None:
                raise error
            if callable(answers):
                return answers(question)
            for key, value in (answers or {}).items():
                if key.lower() in question.lower():
                    if isinstance(value, WorkerResult):
                        return value
                    return WorkerResult(kind="search", question=question, speech=str(value))
            return WorkerResult(
                kind="search", question=question, speech="Nothing much came back on that."
            )

        self.mp.setattr(realtime_workers, "run_search_worker", fake_search)
        return calls

    def disable_brain(self):
        """The honestly-brainless world (keyless is already the default)."""

        from app.config import settings

        self.mp.setattr(settings, "anthropic_api_key", "")
        self.mp.setattr(settings, "parker_openclaw_gateway_url", "")


@pytest.fixture
def voice_world(db, monkeypatch):
    """The safe scenario world: fake key, DB seam, drained teardown."""

    from app.config import settings
    from app.parker import realtime

    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "parker_realtime_enabled", True)
    factory = sessionmaker(bind=db.get_bind())
    monkeypatch.setattr(realtime, "_db_session_factory", factory)
    world = ScenarioWorld(db, monkeypatch)
    yield world
    _wait_until(lambda: realtime._active_bridges == 0, timeout=3.0)
