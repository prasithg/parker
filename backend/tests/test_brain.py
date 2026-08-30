"""BrainAdapter contract, ClaudeBrainAdapter prompt shape, and the post-response guard.

No network anywhere: the Claude adapter is exercised through a fake
Anthropic client that records the request kwargs (the recorded-shape
test) and returns controlled content blocks.
"""

from __future__ import annotations

import pytest

from app.brain.adapter import BrainContext, BrainReply, Message, ProposedAction
from app.brain.claude import ClaudeBrainAdapter, build_brain_adapter
from app.brain.guard import (
    MEDICAL_BOUNDARY_REDIRECT,
    WANT_MORE_SUFFIX,
    screen_reply,
    speech_violates_medical_boundary,
    trim_for_speech,
)


# ---------------------------------------------------------------------------
# Fake Anthropic client returning typed content blocks
# ---------------------------------------------------------------------------


class _TextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _ToolUseBlock:
    type = "tool_use"
    name = "propose_action"

    def __init__(self, input):
        self.input = input


class _Response:
    def __init__(self, blocks):
        self.content = blocks


class FakeAnthropic:
    """Minimal fake for anthropic.Anthropic.messages.create."""

    def __init__(self, blocks):
        self._blocks = blocks
        self.calls: list[dict] = []

    @property
    def messages(self):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Response(self._blocks)


CONTEXT = BrainContext(patient_name="Dad", lexicon_names=("Sarah", "Priya"))


# ---------------------------------------------------------------------------
# ClaudeBrainAdapter: recorded request shape
# ---------------------------------------------------------------------------


def test_request_shape_carries_history_utterance_persona_and_tool():
    client = FakeAnthropic([_TextBlock("It's a lovely question.")])
    adapter = ClaudeBrainAdapter(client, model="claude-test-model", max_tokens=123)

    history = [
        Message(role="user", content="what's a good stretch for stiff shoulders?"),
        Message(role="assistant", content="Gentle shoulder rolls are a good start."),
    ]
    adapter.respond(history, "what about my neck?", CONTEXT)

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["model"] == "claude-test-model"
    # history in order, new utterance last
    assert call["messages"] == [
        {"role": "user", "content": "what's a good stretch for stiff shoulders?"},
        {"role": "assistant", "content": "Gentle shoulder rolls are a good start."},
        {"role": "user", "content": "what about my neck?"},
    ]
    # persona: pilot context + spoken style + boundaries, in the system prompt
    system = call["system"]
    assert "Dad" in system
    assert "Sarah, Priya" in system
    assert "1-3 short" in system
    assert "No medical advice" in system
    assert "emergency services" in system
    # propose_action stays the only ACTION channel; web search (default on)
    # is read-only information retrieval, never an action path.
    assert [tool["name"] for tool in call["tools"]] == ["propose_action", "web_search"]
    assert call["tools"][1]["type"] == "web_search_20260209"
    # a search turn needs output headroom for the tool call + the answer
    assert call["max_tokens"] == 700


def test_without_web_search_the_proposal_tool_is_the_only_tool(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "parker_brain_web_search", False)
    client = FakeAnthropic([_TextBlock("hello")])
    adapter = ClaudeBrainAdapter(client, model="claude-test-model", max_tokens=123)
    adapter.respond([], "hello", CONTEXT)

    call = client.calls[0]
    assert [tool["name"] for tool in call["tools"]] == ["propose_action"]
    assert call["max_tokens"] == 123  # no search, no headroom bump


def test_defaults_come_from_settings(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "parker_brain_web_search", False)
    client = FakeAnthropic([_TextBlock("hello")])
    adapter = ClaudeBrainAdapter(client)

    adapter.respond([], "hello", CONTEXT)

    assert client.calls[0]["model"] == settings.parker_brain_model
    assert client.calls[0]["max_tokens"] == settings.parker_brain_max_tokens


def test_home_place_grounds_search_location_and_prompt():
    client = FakeAnthropic([_TextBlock("hello")])
    adapter = ClaudeBrainAdapter(client, model="m", max_tokens=100)
    placed = BrainContext(patient_name="Dad", lexicon_names=(), home_place="Fitzroy")

    adapter.respond([], "what's the weather?", placed)

    call = client.calls[0]
    search_tool = call["tools"][1]
    assert search_tool["user_location"] == {"type": "approximate", "city": "Fitzroy"}
    assert "household is in Fitzroy" in call["system"]

    # No home place -> no location grounding, no prompt line.
    client2 = FakeAnthropic([_TextBlock("hello")])
    ClaudeBrainAdapter(client2, model="m", max_tokens=100).respond([], "hi", CONTEXT)
    assert "user_location" not in client2.calls[0]["tools"][1]
    assert "household is in" not in client2.calls[0]["system"]


def test_reply_parses_text_blocks_and_tool_proposals():
    client = FakeAnthropic(
        [
            _TextBlock("Happy to set that up."),
            _ToolUseBlock(
                {
                    "action_type": "reminder",
                    "label": "a reminder to water the plants at 5",
                    "subject": "water the plants at 5",
                    "intent_text": "remind me to water the plants at 5",
                }
            ),
        ]
    )
    adapter = ClaudeBrainAdapter(client, model="m", max_tokens=10)

    reply = adapter.respond([], "remind me about the plants", CONTEXT)

    assert reply.speech == "Happy to set that up."
    assert len(reply.proposed_actions) == 1
    action = reply.proposed_actions[0]
    assert action.action_type == "reminder"
    assert action.label == "a reminder to water the plants at 5"
    assert action.recipient is None


def test_build_brain_adapter_is_none_without_key(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "anthropic_api_key", "")

    assert build_brain_adapter() is None


# ---------------------------------------------------------------------------
# Post-response guard: medical boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "speech",
    [
        "You could take an extra half tablet, about 50 mg, before lunch.",
        "Doubling sounds reasonable — you should take it twice as often.",
        "That sounds like you have a progression; increase your levodopa.",
        "Honestly that could be a sign of something worse.",
    ],
)
def test_medical_boundary_speech_is_replaced_with_redirect(speech):
    result = screen_reply(
        BrainReply(
            speech=speech,
            proposed_actions=(
                ProposedAction(
                    action_type="reminder",
                    label="a reminder about the dose",
                    subject="dose",
                    intent_text="remind me about the dose",
                ),
            ),
        )
    )

    assert result.medical_boundary_tripped is True
    assert result.reply.speech == MEDICAL_BOUNDARY_REDIRECT
    # a poisoned answer must not keep its action suggestions either
    assert result.reply.proposed_actions == ()
    assert result.dropped_action_count == 1


def test_safe_speech_passes_untouched():
    reply = BrainReply(speech="The capital of Australia is Canberra.")
    result = screen_reply(reply)

    assert result.medical_boundary_tripped is False
    assert result.reply is reply


def test_redirect_itself_does_not_trip_the_guard():
    assert speech_violates_medical_boundary(MEDICAL_BOUNDARY_REDIRECT) is False


# ---------------------------------------------------------------------------
# Post-response guard: proposal allowlist
# ---------------------------------------------------------------------------


def _proposal(**overrides):
    base = dict(
        action_type="reminder",
        label="a reminder to call the physio",
        subject="call the physio",
        intent_text="remind me to call the physio",
    )
    base.update(overrides)
    return ProposedAction(**base)


@pytest.mark.parametrize(
    "bad",
    [
        _proposal(action_type="purchase"),
        _proposal(action_type="medication_change"),
        _proposal(action_type="smart_home"),
        _proposal(action_type="made_up_type"),
        _proposal(label="  "),
        _proposal(intent_text=""),
    ],
)
def test_non_proposable_or_malformed_actions_are_dropped(bad):
    result = screen_reply(BrainReply(speech="Sure.", proposed_actions=(bad,)))

    assert result.reply.proposed_actions == ()
    assert result.dropped_action_count == 1
    assert result.medical_boundary_tripped is False


def test_proposals_cap_at_two_and_long_labels_truncate():
    actions = tuple(
        _proposal(label=f"a reminder number {i} " + "x" * 100, subject=f"s{i}")
        for i in range(3)
    )
    result = screen_reply(BrainReply(speech="Sure.", proposed_actions=actions))

    assert len(result.reply.proposed_actions) == 2
    assert all(len(a.label) <= 80 for a in result.reply.proposed_actions)
    assert result.dropped_action_count == 1


# ---------------------------------------------------------------------------
# trim_for_speech: TTS-listenable answers
# ---------------------------------------------------------------------------


def test_short_answers_pass_through_unchanged():
    assert trim_for_speech("It's Tuesday.") == "It's Tuesday."


def test_long_answers_trim_to_sentence_cap_with_continuation():
    speech = "One fact. Two facts. Three facts. Four facts. Five facts."
    trimmed = trim_for_speech(speech, max_sentences=3)

    assert trimmed == f"One fact. Two facts. Three facts. {WANT_MORE_SUFFIX}"


def test_single_overlong_sentence_hard_caps():
    speech = "word " * 200
    trimmed = trim_for_speech(speech, max_chars=100)

    assert len(trimmed) < 130
    assert trimmed.endswith(WANT_MORE_SUFFIX)


# ---------------------------------------------------------------------------
# General web search: citations become sources; streaming; pause_turn
# ---------------------------------------------------------------------------


class _Citation:
    def __init__(self, url, title):
        self.url = url
        self.title = title


class _CitedTextBlock:
    type = "text"

    def __init__(self, text, citations):
        self.text = text
        self.citations = citations


def test_search_citations_become_sources_deduped_and_capped():
    blocks = [
        _CitedTextBlock(
            "It's 14 in Fitzroy.",
            [
                _Citation("https://weatherzone.com.au/vic", "Weatherzone — Fitzroy"),
                _Citation("https://weatherzone.com.au/vic", "Weatherzone — Fitzroy"),  # dupe
            ],
        ),
        _CitedTextBlock(
            "Rain is expected tomorrow.",
            [
                _Citation("https://bom.gov.au/", "Bureau of Meteorology"),
                _Citation("https://a.example/", "A"),
                _Citation("https://b.example/", "B"),  # beyond the cap
            ],
        ),
    ]
    adapter = ClaudeBrainAdapter(FakeAnthropic(blocks), model="m", max_tokens=100)
    reply = adapter.respond([], "weather?", CONTEXT)

    assert [s.url for s in reply.sources] == [
        "https://weatherzone.com.au/vic",
        "https://bom.gov.au/",
        "https://a.example/",
    ]
    assert reply.sources[0].label == "Weatherzone — Fitzroy"
    assert all(s.fresh_as_of == "just searched" for s in reply.sources)


def test_citation_without_title_falls_back_to_domain():
    blocks = [_CitedTextBlock("News.", [_Citation("https://news.example.com/story/1", "")])]
    adapter = ClaudeBrainAdapter(FakeAnthropic(blocks), model="m", max_tokens=100)
    reply = adapter.respond([], "news?", CONTEXT)
    assert reply.sources[0].label == "news.example.com"


def test_respond_stream_falls_back_to_single_emit_without_stream_support():
    """Injected fakes without messages.stream keep working — the whole
    answer arrives as one sentence callback."""

    client = FakeAnthropic([_TextBlock("One answer.")])
    adapter = ClaudeBrainAdapter(client, model="m", max_tokens=100)
    heard: list[str] = []
    reply = adapter.respond_stream([], "hi", CONTEXT, heard.append)
    assert heard == ["One answer."]
    assert reply.speech == "One answer."


class _FakeStream:
    def __init__(self, deltas, final):
        self._deltas = deltas
        self._final = final

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def text_stream(self):
        yield from self._deltas

    def get_final_message(self):
        return self._final


class FakeStreamingAnthropic:
    def __init__(self, deltas, final_blocks):
        self._deltas = deltas
        self._final = _Response(final_blocks)
        self.calls: list[dict] = []

    @property
    def messages(self):
        return self

    def create(self, **kwargs):  # pause_turn continuations use create
        self.calls.append(kwargs)
        return self._final

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeStream(self._deltas, self._final)


def test_respond_stream_emits_complete_sentences_as_they_form():
    deltas = ["It's 14 and cl", "ear. Tomorrow looks ", "rainy. Take a coat."]
    final = [_TextBlock("It's 14 and clear. Tomorrow looks rainy. Take a coat.")]
    adapter = ClaudeBrainAdapter(FakeStreamingAnthropic(deltas, final), model="m", max_tokens=100)

    heard: list[str] = []
    reply = adapter.respond_stream([], "weather?", CONTEXT, heard.append)

    assert heard == ["It's 14 and clear.", "Tomorrow looks rainy.", "Take a coat."]
    assert reply.speech == "It's 14 and clear. Tomorrow looks rainy. Take a coat."


def test_pause_turn_is_resumed_and_bounded():
    class _PausedResponse(_Response):
        stop_reason = "pause_turn"

    class PausingClient:
        def __init__(self):
            self.calls = []
            self._responses = [
                _PausedResponse([_TextBlock("Searching…")]),
                _Response([_TextBlock("Here is the answer.")]),
            ]

        @property
        def messages(self):
            return self

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return self._responses[min(len(self.calls) - 1, 1)]

    client = PausingClient()
    adapter = ClaudeBrainAdapter(client, model="m", max_tokens=100)
    reply = adapter.respond([], "long research question", CONTEXT)

    assert len(client.calls) == 2  # one continuation, not an infinite loop
    assert "Here is the answer." in reply.speech
    # the continuation carried the paused assistant turn back
    assert client.calls[1]["messages"][-1]["role"] == "assistant"


class _SearchResultItem:
    type = "web_search_result"

    def __init__(self, url, title):
        self.url = url
        self.title = title


class _SearchToolResultBlock:
    type = "web_search_tool_result"

    def __init__(self, items):
        self.content = items


def test_searched_pages_are_fallback_sources_when_nothing_is_cited():
    """Citations are span-dependent and often absent live; the pages the
    search returned are still honest evidence for the screen."""

    blocks = [
        _SearchToolResultBlock(
            [
                _SearchResultItem("https://weatherzone.com.au/vic", "Weatherzone — Melbourne"),
                _SearchResultItem("https://bom.gov.au/", "Bureau of Meteorology"),
            ]
        ),
        _TextBlock("Partly cloudy, top of 16."),
    ]
    adapter = ClaudeBrainAdapter(FakeAnthropic(blocks), model="m", max_tokens=100)
    reply = adapter.respond([], "weather?", CONTEXT)
    assert [s.label for s in reply.sources] == ["Weatherzone — Melbourne", "Bureau of Meteorology"]


def test_cited_sources_win_over_searched_pages():
    blocks = [
        _SearchToolResultBlock([_SearchResultItem("https://other.example/", "Other page")]),
        _CitedTextBlock("Cited answer.", [_Citation("https://cited.example/", "The cited page")]),
    ]
    adapter = ClaudeBrainAdapter(FakeAnthropic(blocks), model="m", max_tokens=100)
    reply = adapter.respond([], "news?", CONTEXT)
    assert [s.label for s in reply.sources] == ["The cited page"]


def test_request_carries_the_effort_setting():
    client = FakeAnthropic([_TextBlock("hello")])
    ClaudeBrainAdapter(client, model="m", max_tokens=100).respond([], "hi", CONTEXT)
    from app.config import settings

    assert client.calls[0]["output_config"] == {"effort": settings.parker_brain_effort}
