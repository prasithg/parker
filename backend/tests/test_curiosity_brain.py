"""CuriosityBrain + answer-evidence contract (Patient Curiosity Loop).

Everything runs keyless and offline: a recording fake fetcher stands in for
Open-Meteo/ESPN, and fake inner brains stand in for Claude/OpenClaw. The
integration cases pin the strategy acceptance at the brainstem level — a
current answer with visible sources, a follow-up that keeps the topic, and
guards that still run before any provider.
"""

from __future__ import annotations

import pytest

from app.brain.adapter import BrainContext, BrainReply, Message, ProposedAction, Source
from app.brain.curiosity import (
    ESPN_SCOREBOARD_URL,
    FORECAST_URL,
    GEOCODE_URL,
    NO_BRAIN_STUB_SPEECH,
    SCORES_DOWN_SPEECH,
    WEATHER_DOWN_SPEECH,
    CuriosityBrain,
    extract_place,
    requested_day,
)
from app.brain.guard import MEDICAL_BOUNDARY_REDIRECT, screen_reply
from app.brain.openclaw import FallbackBrain, GatewayError
from app.conversation.textloop import TextSession
from app.db.models import CallLog

CONTEXT = BrainContext(patient_name="Dad", lexicon_names=("Sarah",))


def make_session(db, brain):
    call = CallLog(call_sid="TEST-CURIOSITY", call_type="text_loop")
    db.add(call)
    db.commit()
    db.refresh(call)
    return TextSession(db, call.id, brain=brain)


# ---------------------------------------------------------------------------
# Fake provider payloads
# ---------------------------------------------------------------------------


def geocode_payload(name="Fitzroy"):
    return {
        "results": [
            {"id": 42, "name": name, "latitude": -37.8, "longitude": 144.98}
        ]
    }


def forecast_payload():
    return {
        "current": {"time": "2026-08-29T15:00", "temperature_2m": 14.4, "weather_code": 2},
        "daily": {
            "time": [
                "2026-08-29",  # Saturday
                "2026-08-30",  # Sunday
                "2026-08-31",
                "2026-09-01",
            ],
            "temperature_2m_max": [16.2, 18.9, 21.0, 17.5],
            "temperature_2m_min": [8.1, 9.4, 11.2, 9.9],
            "precipitation_probability_max": [10, 65, 20, 30],
            "weather_code": [2, 61, 0, 3],
        },
    }


def scoreboard_payload(state="post", home_winner=True, detail="Final"):
    return {
        "events": [
            {
                "name": "Lakers at Celtics",
                "date": "2026-08-29T00:00Z",
                "status": {"type": {"state": state, "shortDetail": detail}},
                "links": [{"href": "https://www.espn.com/game/401"}],
                "competitions": [
                    {
                        "competitors": [
                            {
                                "homeAway": "home",
                                "score": "112",
                                "winner": home_winner,
                                "team": {
                                    "displayName": "Boston Celtics",
                                    "shortDisplayName": "Celtics",
                                    "location": "Boston",
                                    "abbreviation": "BOS",
                                },
                            },
                            {
                                "homeAway": "away",
                                "score": "104",
                                "winner": not home_winner,
                                "team": {
                                    "displayName": "Los Angeles Lakers",
                                    "shortDisplayName": "Lakers",
                                    "location": "Los Angeles",
                                    "abbreviation": "LAL",
                                },
                            },
                        ]
                    }
                ],
            }
        ]
    }


class FakeFetcher:
    """Recording fetcher with canned payloads keyed by URL."""

    def __init__(self, payloads=None, error_urls=()):
        self.calls = []
        self.payloads = payloads or {}
        self.error_urls = set(error_urls)

    def __call__(self, url, params):
        self.calls.append((url, dict(params)))
        if url in self.error_urls:
            raise ConnectionError("provider down (fake)")
        if url in self.payloads:
            return self.payloads[url]
        raise AssertionError(f"unexpected fetch: {url}")


def weather_fetcher(**kwargs):
    return FakeFetcher(
        payloads={GEOCODE_URL: geocode_payload(), FORECAST_URL: forecast_payload()},
        **kwargs,
    )


def sports_fetcher(payload=None, **kwargs):
    url = ESPN_SCOREBOARD_URL.format(path="basketball/nba")
    return FakeFetcher(payloads={url: payload or scoreboard_payload()}, **kwargs)


def make_brain(fetcher, *, inner=None, home_place="", leagues="", clock=None):
    return CuriosityBrain(
        inner,
        fetcher=fetcher,
        home_place=home_place,
        leagues=leagues,
        temperature_unit="celsius",
        clock=clock,
    )


class EchoBrain:
    def __init__(self):
        self.utterances = []

    def respond(self, history, utterance, context):
        self.utterances.append(utterance)
        return BrainReply(speech=f"inner heard: {utterance}")


# ---------------------------------------------------------------------------
# Answer-evidence contract (Source on BrainReply, through the guard)
# ---------------------------------------------------------------------------


def test_guard_passes_sources_through_on_clean_reply():
    reply = BrainReply(
        speech="Sunny and 16 today.",
        sources=(Source(label="Open-Meteo", url="https://open-meteo.com/", fresh_as_of="3pm"),),
    )
    result = screen_reply(reply)
    assert result.reply.sources == reply.sources


def test_guard_keeps_sources_when_proposals_are_dropped():
    reply = BrainReply(
        speech="Sunny today.",
        proposed_actions=(
            ProposedAction(action_type="purchase", label="buy", subject="x", intent_text="x"),
        ),
        sources=(Source(label="Open-Meteo"),),
    )
    result = screen_reply(reply)
    assert result.dropped_action_count == 1
    assert result.reply.sources == reply.sources


def test_medical_trip_drops_sources_with_everything_else():
    reply = BrainReply(
        speech="You should take 50 mg more.",
        sources=(Source(label="Bad source"),),
    )
    result = screen_reply(reply)
    assert result.medical_boundary_tripped is True
    assert result.reply.speech == MEDICAL_BOUNDARY_REDIRECT
    assert result.reply.sources == ()


def test_fallback_brain_preserves_fallback_sources():
    class DeadGateway:
        def respond(self, history, utterance, context):
            raise GatewayError("down")

    class SourcedBrain:
        def respond(self, history, utterance, context):
            return BrainReply(speech="14 and cloudy.", sources=(Source(label="Open-Meteo"),))

    brain = FallbackBrain(DeadGateway(), fallback=SourcedBrain())
    reply = brain.respond([], "weather?", CONTEXT)
    assert reply.sources == (Source(label="Open-Meteo"),)
    assert FallbackBrain.NOTICE in reply.speech


def test_answer_lane_surfaces_sources_in_response(db):
    class SourcedBrain:
        def respond(self, history, utterance, context):
            return BrainReply(
                speech="It's 14 and partly cloudy.",
                sources=(
                    Source(label="Open-Meteo — Fitzroy", url="https://open-meteo.com/", fresh_as_of="3pm"),
                ),
            )

    session = make_session(db, SourcedBrain())
    response = session.handle("What's the weather today?")
    assert response["kind"] == "answer"
    assert response["sources"] == [
        {"label": "Open-Meteo — Fitzroy", "url": "https://open-meteo.com/", "fresh_as_of": "3pm"}
    ]


# ---------------------------------------------------------------------------
# Weather lane
# ---------------------------------------------------------------------------


def test_weather_question_with_place_answers_with_source_and_freshness():
    fetcher = weather_fetcher()
    brain = make_brain(fetcher)
    reply = brain.respond([], "What's the weather in Fitzroy today?", CONTEXT)

    assert "Fitzroy" in reply.speech
    assert "14" in reply.speech  # current temperature, rounded
    assert len(reply.sources) == 1
    assert reply.sources[0].label == "Open-Meteo — Fitzroy"
    assert "as of" in reply.sources[0].fresh_as_of
    assert [url for url, _ in fetcher.calls] == [GEOCODE_URL, FORECAST_URL]


def test_bare_weather_question_uses_home_place():
    fetcher = weather_fetcher()
    brain = make_brain(fetcher, home_place="Fitzroy")
    reply = brain.respond([], "What is the weather today?", CONTEXT)
    assert "Fitzroy" in reply.speech
    assert fetcher.calls[0][1]["name"] == "Fitzroy"


def test_bare_weather_question_without_home_place_asks_one_bounded_question():
    fetcher = weather_fetcher()
    brain = make_brain(fetcher)
    ask = brain.respond([], "What is the weather today?", CONTEXT)
    assert "which town" in ask.speech.lower()
    assert fetcher.calls == []  # no guessing, no fetch

    answer = brain.respond([], "Fitzroy", CONTEXT)
    assert "Fitzroy" in answer.speech
    assert answer.sources  # resolved with the supplied place


def test_tomorrow_followup_reuses_cached_forecast_without_refetch():
    fetcher = weather_fetcher()
    brain = make_brain(fetcher)
    brain.respond([], "What's the weather in Fitzroy today?", CONTEXT)
    fetches_before = len(fetcher.calls)

    followup = brain.respond([], "What about tomorrow?", CONTEXT)

    assert "Tomorrow" in followup.speech
    assert "19" in followup.speech  # tomorrow's high, rounded from 18.9
    assert "rain" in followup.speech  # 65% precipitation probability surfaces
    assert len(fetcher.calls) == fetches_before  # no second fetch


def test_weekend_followup_summarizes_saturday_and_sunday():
    fetcher = weather_fetcher()
    brain = make_brain(fetcher)
    brain.respond([], "What's the weather in Fitzroy?", CONTEXT)
    followup = brain.respond([], "And the weekend?", CONTEXT)
    assert "Saturday" in followup.speech
    assert "Sunday" in followup.speech


def test_unknown_place_is_an_honest_retry_not_a_guess():
    fetcher = FakeFetcher(payloads={GEOCODE_URL: {"results": []}})
    brain = make_brain(fetcher)
    reply = brain.respond([], "What's the weather in Zzyzxq?", CONTEXT)
    assert "couldn't find" in reply.speech.lower()
    assert reply.sources == ()


def test_weather_provider_failure_is_brief_and_recoverable():
    fetcher = weather_fetcher(error_urls={GEOCODE_URL})
    brain = make_brain(fetcher)
    reply = brain.respond([], "What's the weather in Fitzroy?", CONTEXT)
    assert reply.speech == WEATHER_DOWN_SPEECH

    fetcher.error_urls.clear()
    recovered = brain.respond([], "What's the weather in Fitzroy?", CONTEXT)
    assert "Fitzroy" in recovered.speech


def test_time_words_are_never_treated_as_places():
    assert extract_place("What's the forecast for today?") is None
    assert extract_place("Will it rain in the morning?") is None
    assert extract_place("What's the weather for Saturday?") is None
    assert extract_place("What's the weather in New York today?") == "New York"


def test_requested_day_parsing():
    assert requested_day("what about tomorrow?") == "tomorrow"
    assert requested_day("and the weekend?") == "weekend"
    assert requested_day("how about Sunday?") == "sunday"
    assert requested_day("what's the weather?") == "today"


# ---------------------------------------------------------------------------
# Scores lane
# ---------------------------------------------------------------------------


def test_scores_without_configured_leagues_is_honest_and_offline():
    fetcher = sports_fetcher()
    brain = make_brain(fetcher, leagues="")
    reply = brain.respond([], "Did the Celtics win last night?", CONTEXT)
    assert "hasn't picked any leagues" in reply.speech
    assert fetcher.calls == []


def test_unsupported_league_key_is_honest():
    brain = make_brain(sports_fetcher(), leagues="quidditch")
    reply = brain.respond([], "What was the score?", CONTEXT)
    assert "quidditch" in reply.speech


def test_final_score_names_winner_with_espn_source():
    fetcher = sports_fetcher()
    brain = make_brain(fetcher, leagues="nba")
    reply = brain.respond([], "Did the Celtics win?", CONTEXT)

    assert "Celtics won" in reply.speech
    assert "112" in reply.speech and "104" in reply.speech
    assert reply.sources[0].label == "ESPN — NBA"
    assert reply.sources[0].fresh_as_of == "Final"


def test_in_progress_game_reports_live_score():
    fetcher = sports_fetcher(scoreboard_payload(state="in", detail="3rd Quarter"))
    brain = make_brain(fetcher, leagues="nba")
    reply = brain.respond([], "What's the Lakers score?", CONTEXT)
    assert "on now" in reply.speech
    assert "3rd Quarter" in reply.speech


def test_scheduled_game_reports_start_not_a_fake_score():
    fetcher = sports_fetcher(scoreboard_payload(state="pre", detail="Sat 7:30 PM"))
    brain = make_brain(fetcher, leagues="nba")
    reply = brain.respond([], "Are the Celtics playing today?", CONTEXT)
    assert "play" in reply.speech
    assert "Sat 7:30 PM" in reply.speech
    assert "won" not in reply.speech


def test_they_followup_resolves_to_last_team_from_cache():
    fetcher = sports_fetcher()
    brain = make_brain(fetcher, leagues="nba")
    brain.respond([], "Did the Celtics win?", CONTEXT)
    fetches_before = len(fetcher.calls)

    followup = brain.respond([], "Who did they play?", CONTEXT)

    assert "Lakers" in followup.speech
    assert len(fetcher.calls) == fetches_before  # scoreboard cache, no refetch


def test_no_team_match_asks_which_team():
    fetcher = sports_fetcher()
    brain = make_brain(fetcher, leagues="nba")
    reply = brain.respond([], "What was the score of the game?", CONTEXT)
    assert "Which team" in reply.speech


def test_empty_scoreboard_is_honest_about_no_games():
    """Off-season reality (found live: NBA in August has an empty board)."""

    fetcher = sports_fetcher({"events": []})
    brain = make_brain(fetcher, leagues="nba")
    reply = brain.respond([], "Did the Celtics win last night?", CONTEXT)
    assert "don't see any NBA games" in reply.speech
    assert "Which team" not in reply.speech


def test_scores_provider_failure_is_brief_and_honest():
    url = ESPN_SCOREBOARD_URL.format(path="basketball/nba")
    fetcher = FakeFetcher(error_urls={url})
    brain = make_brain(fetcher, leagues="nba")
    reply = brain.respond([], "Did the Celtics win?", CONTEXT)
    assert reply.speech == SCORES_DOWN_SPEECH


def test_scoreboard_cache_expires_after_ttl():
    moments = iter([0.0, 500.0, 500.0])
    fetcher = sports_fetcher()
    brain = make_brain(fetcher, leagues="nba", clock=lambda: next(moments))
    brain.respond([], "Did the Celtics win?", CONTEXT)
    brain.respond([], "Did the Celtics win?", CONTEXT)
    assert len(fetcher.calls) == 2  # first fetch + refetch after TTL


# ---------------------------------------------------------------------------
# Delegation
# ---------------------------------------------------------------------------


def test_other_questions_delegate_to_inner_brain():
    inner = EchoBrain()
    brain = make_brain(FakeFetcher(), inner=inner)
    reply = brain.respond([], "Tell me about Uri Levine", CONTEXT)
    assert reply.speech == "inner heard: Tell me about Uri Levine"
    assert inner.utterances == ["Tell me about Uri Levine"]


def test_without_inner_brain_other_questions_get_honest_stub():
    brain = make_brain(FakeFetcher())
    reply = brain.respond([], "Tell me about Uri Levine", CONTEXT)
    assert reply.speech == NO_BRAIN_STUB_SPEECH


# ---------------------------------------------------------------------------
# Through the real TextSession (brainstem-level acceptance)
# ---------------------------------------------------------------------------


def test_weather_and_followup_through_text_session(db):
    brain = make_brain(weather_fetcher(), home_place="Fitzroy")
    session = make_session(db, brain)

    first = session.handle("What is the weather today?")
    assert first["kind"] == "answer"
    assert "Fitzroy" in first["speech"]
    assert first["sources"][0]["label"] == "Open-Meteo — Fitzroy"

    followup = session.handle("What about tomorrow?")
    assert followup["kind"] == "answer"
    assert "Tomorrow" in followup["speech"]  # topic carried, no restating


def test_sports_answer_through_text_session_carries_source(db):
    brain = make_brain(sports_fetcher(), leagues="nba")
    session = make_session(db, brain)
    response = session.handle("What was the Celtics score?")
    assert response["kind"] == "answer"
    assert "Celtics" in response["speech"]
    assert response["sources"][0]["label"] == "ESPN — NBA"


def test_guards_still_run_before_any_provider(db):
    fetcher = weather_fetcher()
    brain = make_brain(fetcher, home_place="Fitzroy", leagues="nba")
    session = make_session(db, brain)

    refused = session.handle("Should I take half my pills tomorrow?")
    assert refused["kind"] == "refused"
    assert fetcher.calls == []  # the provider never saw the refused utterance


def test_provider_exception_never_kills_the_session(db):
    class ExplodingFetcher:
        def __call__(self, url, params):
            raise RuntimeError("boom")

    brain = make_brain(ExplodingFetcher(), home_place="Fitzroy")
    session = make_session(db, brain)
    response = session.handle("What's the weather today?")
    assert response["kind"] == "answer"
    assert response["speech"] == WEATHER_DOWN_SPEECH

    ok = session.handle("Remind me to water the plants")
    assert ok["kind"] == "captured"
