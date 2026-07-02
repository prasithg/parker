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
    assert call["max_tokens"] == 123
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
    # the structured proposal mechanism is the only action channel
    assert [tool["name"] for tool in call["tools"]] == ["propose_action"]


def test_defaults_come_from_settings():
    client = FakeAnthropic([_TextBlock("hello")])
    adapter = ClaudeBrainAdapter(client)

    adapter.respond([], "hello", CONTEXT)

    from app.config import settings

    assert client.calls[0]["model"] == settings.parker_brain_model
    assert client.calls[0]["max_tokens"] == settings.parker_brain_max_tokens


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
