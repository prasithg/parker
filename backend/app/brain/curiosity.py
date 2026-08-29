"""CuriosityBrain — the keyless current-information lane, in front of any brain.

The strategy doc's first proof is the job Dad already hires Google Home for:
weather, scores, and follow-up questions. The Claude brain is deliberately
honest that it may lack live data, and no OpenClaw gateway ships a structured
source list today — so this adapter answers the two high-frequency current
topics itself from keyless public APIs and delegates everything else to the
wrapped inner brain (Claude / OpenClaw / the honest stub).

Same contract, same gates as every brain (docs/brain-adapters.md): it runs
only after the deterministic pre-model guards, it returns speech plus
``Source`` evidence (label/url/freshness — shown, never spoken), it proposes
no actions, and it holds no send path. Every reply still passes the
post-response guard.

Providers (both keyless, no accounts):

- Weather: Open-Meteo geocoding + forecast (https://open-meteo.com/).
- Scores: the ESPN public scoreboard JSON, per family-configured league
  (``PARKER_SPORTS_LEAGUES``). Undocumented-but-stable public endpoints;
  every parse is defensive and every failure degrades to brief honest
  speech, never a crash.

Follow-up continuity lives here as small per-session state: the forecast is
cached per place so "what about tomorrow?" answers instantly with no second
fetch, and "did they win? / when do they play next?" resolves against the
last-mentioned team. The suite injects a fake fetcher; nothing here touches
the network under test.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable, Optional

from app.brain.adapter import BrainContext, BrainReply, Message, Source

# An injectable GET: (url, params) -> parsed JSON. The default is built
# lazily from httpx so the suite never needs the network or the dependency.
Fetcher = Callable[[str, dict[str, Any]], Any]

FETCH_TIMEOUT_SECONDS = 5.0
SCOREBOARD_CACHE_SECONDS = 120.0

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"

# Family-configurable league keys -> ESPN sport/league paths. Deliberately a
# small allowlist: an unknown key is reported honestly at question time, and
# new leagues (cricket needs a different endpoint shape) are a follow-up —
# the exact teams/leagues remain a Pras decision (execution plan, decision 5).
LEAGUE_PATHS: dict[str, str] = {
    "nba": "basketball/nba",
    "wnba": "basketball/wnba",
    "nfl": "football/nfl",
    "mlb": "baseball/mlb",
    "nhl": "hockey/nhl",
    "epl": "soccer/eng.1",
    "mls": "soccer/usa.1",
    "afl": "australian-football/afl",
}

NO_BRAIN_STUB_SPEECH = (
    "I can check the weather and sports scores for you. For other questions, "
    "the family needs to connect my full brain first."
)
WEATHER_DOWN_SPEECH = (
    "I couldn't reach the weather service just now. Try me again in a minute."
)
SCORES_DOWN_SPEECH = (
    "I couldn't reach the scores service just now. Try me again in a minute."
)

_WEATHER_WORDS = re.compile(
    r"\b(?:weather|forecast|temperature|rain|raining|rainy|snow|snowing|windy|"
    r"humid|sunny|cloudy|how\s+hot|how\s+cold|umbrella)\b",
    re.IGNORECASE,
)
_PLACE_PATTERN = re.compile(
    r"\b(?:in|for|at|around)\s+(?P<place>[A-Za-z][A-Za-z' .-]{1,40}?)"
    r"(?=\s*(?:$|[?.!,])|\s+(?:today|tomorrow|tonight|this|next|on)\b)",
    re.IGNORECASE,
)
_DAY_AFTER_TOMORROW = re.compile(r"\bday\s+after\s+tomorrow\b", re.IGNORECASE)
_TOMORROW = re.compile(r"\btomorrow\b", re.IGNORECASE)
_WEEKEND = re.compile(r"\bweekend\b", re.IGNORECASE)
# Time frames past the forecast horizon, or too vague to pin to a day.
# These must never silently become "today" under a source chip — a wrong
# answer wearing a credibility badge is worse than an honest question.
_UNKNOWN_TIME_FRAME = re.compile(
    r"\bnext\s+(?:week|month|fortnight)\b|\bthe\s+day\s+after\s*(?:$|[?.!])|"
    r"\bin\s+(?:a\s+few|two|three|several)\s+(?:days|weeks)\b",
    re.IGNORECASE,
)
# Generic follow-up frames ("what about X?", "how about X?", "and X?") keep
# the CONVERSATION's last topic alive — weather or scores, whichever
# answered most recently. The lane itself decides what X means.
_GENERIC_FOLLOWUP = re.compile(
    r"^\s*(?:and|what about|how about|what's it like)\b", re.IGNORECASE
)
_DAY_NAMES = (
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
)

_SPORTS_WORDS = re.compile(
    r"\b(?:score|scores|game|match|win|won|lost|lose|beat|playing|play|played|"
    r"result|fixture)\b",
    re.IGNORECASE,
)
_NEXT_GAME = re.compile(r"\b(?:next|when)\b.*\bplay", re.IGNORECASE)
_THEY_FRAME = re.compile(r"\b(?:they|them|the game|that game)\b", re.IGNORECASE)
_OPPONENT_FRAME = re.compile(
    r"\bwho\b.*\b(?:play|played|against|beat)\b|\bagainst who|\bwho was it\b",
    re.IGNORECASE,
)
_WIN_QUESTION = re.compile(r"\bdid\b.*\bwin\b|\bdid\s+they\s+win\b", re.IGNORECASE)

# WMO weather codes -> short spoken description (Open-Meteo contract).
_WEATHER_CODES: dict[int, str] = {
    0: "clear",
    1: "mostly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "foggy",
    48: "foggy",
    51: "drizzly",
    53: "drizzly",
    55: "drizzly",
    56: "icy and drizzly",
    57: "icy and drizzly",
    61: "lightly rainy",
    63: "rainy",
    65: "very rainy",
    66: "icy and rainy",
    67: "icy and rainy",
    71: "lightly snowy",
    73: "snowy",
    75: "very snowy",
    77: "snowy",
    80: "showery",
    81: "showery",
    82: "heavy showers",
    85: "snow showers",
    86: "snow showers",
    95: "stormy",
    96: "stormy",
    99: "stormy",
}


def _describe_weather_code(code: Any) -> str:
    try:
        return _WEATHER_CODES.get(int(code), "mixed")
    except (TypeError, ValueError):
        return "mixed"


def _default_fetcher() -> Fetcher:
    import httpx

    def _fetch(url: str, params: dict[str, Any]) -> Any:
        response = httpx.get(url, params=params, timeout=FETCH_TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.json()

    return _fetch


class ProviderError(RuntimeError):
    """A current-information provider was unreachable or answered garbage."""


def looks_like_weather_question(utterance: str) -> bool:
    return bool(_WEATHER_WORDS.search(utterance))


def looks_like_sports_question(utterance: str) -> bool:
    return bool(_SPORTS_WORDS.search(utterance))


def extract_place(utterance: str) -> Optional[str]:
    """A place named in the utterance ("weather in Fitzroy"), or None."""

    match = _PLACE_PATTERN.search(utterance)
    if match is None:
        return None
    place = match.group("place").strip(" .")
    # "in the morning", "for today" are time frames, not places.
    lowered = place.lower()
    if lowered.startswith("the ") or lowered in {
        "the",
        "morning",
        "afternoon",
        "evening",
        "town",
        "here",
        "today",
        "tomorrow",
        "tonight",
        "weekend",
        "now",
        "later",
        *_DAY_NAMES,
    }:
        return None
    return place


def requested_day(utterance: str) -> str:
    """'today' | 'tomorrow' | 'day_after_tomorrow' | 'weekend' | weekday | 'unknown'.

    A bare weather question means now/today. A time frame the forecast
    cannot cover ("next week") or too vague to pin ("the day after")
    returns 'unknown' so the caller asks instead of guessing.
    """

    if _DAY_AFTER_TOMORROW.search(utterance):
        return "day_after_tomorrow"
    if _TOMORROW.search(utterance):
        return "tomorrow"
    if _WEEKEND.search(utterance):
        return "weekend"
    lowered = utterance.lower()
    for day in _DAY_NAMES:
        if re.search(rf"\b{day}\b", lowered):
            return day
    if _UNKNOWN_TIME_FRAME.search(utterance):
        return "unknown"
    return "today"


def _looks_like_bare_place(utterance: str) -> bool:
    """A short answer to "which town?" — words only, no verbs we route on."""

    stripped = utterance.strip(" .!?")
    if not stripped or len(stripped.split()) > 4:
        return False
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z' .-]*", stripped))


class CuriosityBrain:
    """BrainAdapter answering weather/scores live; everything else delegates."""

    def __init__(
        self,
        inner: Any | None = None,
        *,
        fetcher: Fetcher | None = None,
        home_place: str | None = None,
        leagues: str | None = None,
        temperature_unit: str | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        from app.config import settings

        self._inner = inner
        self._fetcher = fetcher
        self._home_place = (
            home_place if home_place is not None else settings.parker_home_place
        ).strip()
        raw_leagues = leagues if leagues is not None else settings.parker_sports_leagues
        self._leagues = [
            key for key in (part.strip().lower() for part in raw_leagues.split(",")) if key
        ]
        self._temperature_unit = (
            temperature_unit
            if temperature_unit is not None
            else settings.parker_weather_units
        ).strip().lower() or "celsius"
        import time as _time

        self._clock = clock or _time.monotonic

        # Per-session follow-up state. The brain instance lives exactly as
        # long as its TextSession, so this is conversation memory, not a
        # global cache.
        self._last_lane: Optional[str] = None  # "weather" | "sports"
        self._awaiting_place = False
        self._last_place: Optional[dict[str, Any]] = None  # geocoded place
        self._last_forecast: Optional[dict[str, Any]] = None
        self._geocode_cache: dict[str, dict[str, Any]] = {}
        self._scoreboard_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._last_event: Optional[dict[str, Any]] = None
        self._last_team: Optional[str] = None

    # ------------------------------------------------------------------
    # Contract
    # ------------------------------------------------------------------

    def respond(
        self,
        history: list[Message],
        utterance: str,
        context: BrainContext,
    ) -> BrainReply:
        text = utterance.strip()

        if self._awaiting_place and _looks_like_bare_place(text):
            self._awaiting_place = False
            return self._weather_reply(place_name=text.strip(" .!?"), day="today")

        self._awaiting_place = False

        if looks_like_weather_question(text):
            return self._handle_weather(text)

        if looks_like_sports_question(text):
            return self._handle_sports(text)

        # "What about X?" / "How about X?" continues whichever lane answered
        # last. Without this, a bare sports follow-up fell through to the
        # inner brain — whose prompt honestly denies having live data, so it
        # retracted the ESPN score it had just given (found live by the UX
        # verifier). The lane itself decides what X means.
        if _GENERIC_FOLLOWUP.match(text):
            if self._last_lane == "sports":
                return self._handle_sports(text)
            if self._last_lane == "weather" and self._last_forecast is not None:
                return self._handle_weather(text)

        if self._inner is not None:
            return self._inner.respond(history, utterance, context)
        return BrainReply(speech=NO_BRAIN_STUB_SPEECH)

    # ------------------------------------------------------------------
    # Weather
    # ------------------------------------------------------------------

    def _handle_weather(self, text: str) -> BrainReply:
        self._last_lane = "weather"
        day = requested_day(text)
        if day == "unknown":
            place = self._last_place["name"] if self._last_place else "there"
            return BrainReply(
                speech=(
                    f"I can see about a week ahead for {place}. "
                    "Which day should I check?"
                )
            )
        place_name = extract_place(text)
        if place_name is None and self._last_place is not None:
            # Follow-up: keep the conversation's place.
            return self._weather_reply(day=day)
        if place_name is None:
            place_name = self._home_place
        if not place_name:
            self._awaiting_place = True
            return BrainReply(
                speech=(
                    "Happy to check the weather — which town or suburb should "
                    "I look at?"
                )
            )
        return self._weather_reply(place_name=place_name, day=day)

    def _weather_reply(self, *, place_name: str | None = None, day: str) -> BrainReply:
        try:
            if place_name is not None:
                place = self._geocode(place_name)
                if place is None:
                    return BrainReply(
                        speech=(
                            f"I couldn't find a town called {place_name.strip()}. "
                            "Could you say the town name again?"
                        )
                    )
                if (
                    self._last_place is None
                    or place.get("id") != self._last_place.get("id")
                    or self._last_forecast is None
                ):
                    self._last_place = place
                    self._last_forecast = self._fetch_forecast(place)
            elif self._last_place is None or self._last_forecast is None:
                self._awaiting_place = True
                return BrainReply(
                    speech=(
                        "Happy to check the weather — which town or suburb "
                        "should I look at?"
                    )
                )
        except ProviderError:
            return BrainReply(speech=WEATHER_DOWN_SPEECH)

        return self._speak_forecast(day)

    def _geocode(self, place_name: str) -> Optional[dict[str, Any]]:
        key = place_name.strip().lower()
        if key in self._geocode_cache:
            return self._geocode_cache[key]
        data = self._fetch(GEOCODE_URL, {"name": place_name.strip(), "count": 1})
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list) or not results:
            return None
        first = results[0]
        if not isinstance(first, dict) or "latitude" not in first:
            return None
        place = {
            "id": first.get("id") or f"{first.get('latitude')},{first.get('longitude')}",
            "name": str(first.get("name") or place_name).strip(),
            "latitude": first["latitude"],
            "longitude": first.get("longitude"),
        }
        self._geocode_cache[key] = place
        return place

    def _fetch_forecast(self, place: dict[str, Any]) -> dict[str, Any]:
        data = self._fetch(
            FORECAST_URL,
            {
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,weather_code",
                "daily": (
                    "temperature_2m_max,temperature_2m_min,"
                    "precipitation_probability_max,weather_code"
                ),
                "forecast_days": 7,
                "timezone": "auto",
                "temperature_unit": self._temperature_unit,
            },
        )
        if not isinstance(data, dict) or "daily" not in data:
            raise ProviderError("forecast payload had no daily block")
        return data

    def _speak_forecast(self, day: str) -> BrainReply:
        assert self._last_place is not None and self._last_forecast is not None
        place = self._last_place["name"]
        forecast = self._last_forecast
        daily = forecast.get("daily") or {}
        dates: list[str] = list(daily.get("time") or [])
        highs = daily.get("temperature_2m_max") or []
        lows = daily.get("temperature_2m_min") or []
        codes = daily.get("weather_code") or []
        rain = daily.get("precipitation_probability_max") or []

        def day_summary(index: int, label: str) -> str | None:
            if index >= len(dates) or index >= len(highs):
                return None
            desc = _describe_weather_code(codes[index] if index < len(codes) else None)
            high = round(highs[index])
            low = round(lows[index]) if index < len(lows) else None
            rain_part = ""
            if index < len(rain) and rain[index] is not None and rain[index] >= 40:
                rain_part = f" and a {round(rain[index])} percent chance of rain"
            low_part = f", down to {low} overnight" if low is not None else ""
            return f"{label} looks {desc} with a top of {high}{low_part}{rain_part}"

        index, label = self._resolve_day_index(day, dates)
        if day == "weekend":
            parts = [
                part
                for offset, part_label in self._weekend_indices(dates)
                if (part := day_summary(offset, part_label)) is not None
            ]
            speech = (
                f"For the weekend in {place}: " + "; ".join(parts) + "."
                if parts
                else f"I don't have weekend details for {place} yet."
            )
        elif index is None:
            speech = f"I only have about a week of forecast for {place}."
        elif index == 0:
            current = forecast.get("current") or {}
            now_part = ""
            if isinstance(current, dict) and current.get("temperature_2m") is not None:
                desc = _describe_weather_code(current.get("weather_code"))
                now_part = (
                    f"It's {round(current['temperature_2m'])} and {desc} in "
                    f"{place} right now. "
                )
            summary = day_summary(0, "Today")
            speech = f"{now_part}{summary}." if summary else now_part.strip()
        else:
            summary = day_summary(index, label)
            speech = (
                f"{summary} in {place}."
                if summary
                else f"I don't have that day's forecast for {place} yet."
            )

        fresh = ""
        current = forecast.get("current")
        if isinstance(current, dict) and current.get("time"):
            fresh = f"as of {self._friendly_time(str(current['time']))}"
        return BrainReply(
            speech=speech,
            sources=(
                Source(
                    label=f"Open-Meteo — {place}",
                    url="https://open-meteo.com/",
                    fresh_as_of=fresh or "just fetched",
                ),
            ),
        )

    @staticmethod
    def _friendly_time(iso_time: str) -> str:
        try:
            moment = datetime.fromisoformat(iso_time)
        except ValueError:
            return iso_time
        return moment.strftime("%-I:%M %p today") if hasattr(moment, "strftime") else iso_time

    @staticmethod
    def _resolve_day_index(day: str, dates: list[str]) -> tuple[Optional[int], str]:
        if day == "today" or not dates:
            return 0, "Today"
        if day == "tomorrow":
            return (1, "Tomorrow") if len(dates) > 1 else (None, "Tomorrow")
        if day == "day_after_tomorrow":
            return (2, "The day after tomorrow") if len(dates) > 2 else (None, "That day")
        if day == "weekend":
            return 0, "Weekend"  # handled by _weekend_indices
        for index, value in enumerate(dates):
            try:
                weekday = datetime.fromisoformat(value).strftime("%A").lower()
            except ValueError:
                continue
            if weekday == day:
                return index, weekday.capitalize()
        return None, day.capitalize()

    @staticmethod
    def _weekend_indices(dates: list[str]) -> list[tuple[int, str]]:
        """THIS weekend's remaining days, chronological, never past Sunday.

        Asked on a Sunday, "the weekend" is just today — reaching ahead to
        next Saturday read as a reversed, confusing pair (found live).
        """

        found: list[tuple[int, str]] = []
        for index, value in enumerate(dates):
            try:
                weekday = datetime.fromisoformat(value).strftime("%A")
            except ValueError:
                continue
            if weekday in ("Saturday", "Sunday"):
                found.append((index, weekday))
            if weekday == "Sunday":
                break
        return found[:2]

    # ------------------------------------------------------------------
    # Scores
    # ------------------------------------------------------------------

    def _handle_sports(self, text: str) -> BrainReply:
        self._last_lane = "sports"
        if not self._leagues:
            return BrainReply(
                speech=(
                    "The family hasn't picked any leagues for me to follow yet, "
                    "so I can't check scores. I can still help with the weather "
                    "or other questions."
                )
            )
        unknown = [key for key in self._leagues if key not in LEAGUE_PATHS]
        leagues = [key for key in self._leagues if key in LEAGUE_PATHS]
        if not leagues:
            return BrainReply(
                speech=(
                    f"I don't know how to check {', '.join(unknown)} yet. "
                    "The family can pick from the leagues I support."
                )
            )

        try:
            events = self._all_events(leagues)
        except ProviderError:
            return BrainReply(speech=SCORES_DOWN_SPEECH)

        event = self._match_event(text, events)
        if event is None and self._last_event is not None and _THEY_FRAME.search(text):
            event = self._last_event
        if event is None:
            league_names = ", ".join(key.upper() for key in leagues)
            if not events:
                # Off-season / rest day: an empty board is the honest answer,
                # not a request to repeat the team name.
                return BrainReply(
                    speech=(
                        f"I don't see any {league_names} games on today's board — "
                        "there may be none on right now."
                    )
                )
            return BrainReply(
                speech=(
                    f"I can check today's {league_names} games. "
                    "Which team do you want to hear about?"
                )
            )

        self._last_event = event
        self._last_team = event.get("matched_team")
        return BrainReply(
            speech=self._speak_event(
                event,
                next_game=bool(_NEXT_GAME.search(text)),
                opponent_question=bool(_OPPONENT_FRAME.search(text)),
                win_question=bool(_WIN_QUESTION.search(text)),
            ),
            sources=(
                Source(
                    label=f"ESPN — {event.get('league', '').upper()}".strip(" —"),
                    url=event.get("link") or "https://www.espn.com/",
                    fresh_as_of=event.get("status_detail") or "today's scoreboard",
                ),
            ),
        )

    def _all_events(self, leagues: list[str]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for key in leagues:
            events.extend(self._scoreboard(key))
        return events

    def _scoreboard(self, league_key: str) -> list[dict[str, Any]]:
        cached = self._scoreboard_cache.get(league_key)
        if cached is not None and (self._clock() - cached[0]) < SCOREBOARD_CACHE_SECONDS:
            return cached[1]
        url = ESPN_SCOREBOARD_URL.format(path=LEAGUE_PATHS[league_key])
        data = self._fetch(url, {})
        raw_events = data.get("events") if isinstance(data, dict) else None
        parsed = [
            event
            for raw in (raw_events if isinstance(raw_events, list) else [])
            if (event := self._parse_event(raw, league_key)) is not None
        ]
        self._scoreboard_cache[league_key] = (self._clock(), parsed)
        return parsed

    @staticmethod
    def _parse_event(raw: Any, league_key: str) -> Optional[dict[str, Any]]:
        if not isinstance(raw, dict):
            return None
        competitions = raw.get("competitions")
        competition = (
            competitions[0]
            if isinstance(competitions, list) and competitions and isinstance(competitions[0], dict)
            else {}
        )
        competitors = []
        for entry in competition.get("competitors") or []:
            if not isinstance(entry, dict):
                continue
            team = entry.get("team") if isinstance(entry.get("team"), dict) else {}
            names = {
                str(team.get(field) or "").strip()
                for field in ("displayName", "shortDisplayName", "name", "location", "abbreviation")
            }
            competitors.append(
                {
                    "names": {name for name in names if name},
                    "display": str(
                        team.get("shortDisplayName") or team.get("displayName") or "team"
                    ),
                    "score": str(entry.get("score") or "").strip(),
                    "winner": bool(entry.get("winner")),
                    "home": entry.get("homeAway") == "home",
                }
            )
        if len(competitors) != 2:
            return None
        status = raw.get("status") if isinstance(raw.get("status"), dict) else {}
        status_type = status.get("type") if isinstance(status.get("type"), dict) else {}
        link = ""
        links = raw.get("links")
        if isinstance(links, list) and links and isinstance(links[0], dict):
            link = str(links[0].get("href") or "")
        return {
            "league": league_key,
            "name": str(raw.get("name") or raw.get("shortName") or ""),
            "date": str(raw.get("date") or ""),
            "state": str(status_type.get("state") or ""),  # pre | in | post
            "status_detail": str(
                status_type.get("shortDetail") or status_type.get("detail") or ""
            ),
            "competitors": competitors,
            "link": link,
        }

    @staticmethod
    def _match_event(
        text: str, events: list[dict[str, Any]]
    ) -> Optional[dict[str, Any]]:
        lowered = text.lower()
        for event in events:
            for competitor in event["competitors"]:
                for name in competitor["names"]:
                    if len(name) >= 3 and name.lower() in lowered:
                        matched = dict(event)
                        matched["matched_team"] = competitor["display"]
                        return matched
        return None

    @staticmethod
    def _speak_event(
        event: dict[str, Any],
        *,
        next_game: bool,
        opponent_question: bool = False,
        win_question: bool = False,
    ) -> str:
        first, second = event["competitors"]
        state = event["state"]
        detail = event.get("status_detail") or ""
        if win_question and state == "post" and event.get("matched_team"):
            # "Did Collingwood win?" deserves a yes or no first, not a
            # winner's name he has to invert in his head (found live).
            matched = event["matched_team"]
            mine = first if first["display"] == matched else second
            other = second if mine is first else first
            if mine["winner"]:
                return (
                    f"Yes — {mine['display']} won, {mine['score']} to "
                    f"{other['score']} over {other['display']}."
                )
            if other["winner"]:
                return (
                    f"No — {mine['display']} lost to {other['display']}, "
                    f"{other['score']} to {mine['score']}."
                )
        if opponent_question and event.get("matched_team"):
            matched = event["matched_team"]
            opponent = second if first["display"] == matched else first
            them = first if opponent is second else second
            result = CuriosityBrain._speak_event(event, next_game=False)
            return f"{them['display']} played {opponent['display']}. {result}"
        if next_game and state == "post":
            # "When do they play next?" after a final: today's board only
            # shows this game — be honest instead of misreading the past
            # game as an upcoming one.
            result = CuriosityBrain._speak_event(event, next_game=False)
            return f"{result} I don't see their next game on today's board yet."
        if state == "pre":
            when = f" — {detail}" if detail else ""
            return (
                f"{first['display']} play {second['display']} next{when}. "
                "I'll have the score once it's on."
            )
        with_scores = all(competitor["score"] for competitor in (first, second))
        if not with_scores:
            return f"{first['display']} and {second['display']}: no score is up yet."
        if state == "in":
            return (
                f"It's on now — {first['display']} {first['score']}, "
                f"{second['display']} {second['score']}, {detail}."
            )
        winner = first if first["winner"] else second if second["winner"] else None
        loser = second if winner is first else first
        if winner is None:
            return (
                f"Final score: {first['display']} {first['score']}, "
                f"{second['display']} {second['score']}."
            )
        # No leading article: "Celtics won" and "Collingwood won" both read
        # naturally; "the Collingwood" (seen live on the AFL board) does not.
        return (
            f"{winner['display']} won — {winner['score']} to "
            f"{loser['score']} over {loser['display']}."
        )

    # ------------------------------------------------------------------
    # Fetch plumbing
    # ------------------------------------------------------------------

    def _fetch(self, url: str, params: dict[str, Any]) -> Any:
        if self._fetcher is None:
            self._fetcher = _default_fetcher()
        try:
            return self._fetcher(url, params)
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 — any transport/parse failure degrades
            raise ProviderError(str(exc)) from exc


def build_curiosity_brain(inner: Any | None) -> CuriosityBrain:
    """The converse harness's brain: live current info over the inner brain."""

    return CuriosityBrain(inner)
