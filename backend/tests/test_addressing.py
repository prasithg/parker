"""Unit tests for the directed-vs-ambient wake layer (EXP-001 slice 1).

Both error directions matter and are asymmetric: false-directed (ambient TV
speech drawing repair choices) burns household trust; false-ambient (missing a
real request) forces an effortful-speech user to repeat himself. These tests
pin the policy: wake mode requires the name only to *start* an exchange,
never to continue one.
"""

from app.conversation.addressing import decide_utterance_context


def _decide(utterance, **overrides):
    kwargs = {"awaiting_reply": False, "mode": "wake", "wake_name": "parker"}
    kwargs.update(overrides)
    return decide_utterance_context(utterance, **kwargs)


def test_open_mode_everything_directed_and_unchanged():
    context, routed = _decide("remind me to stretch", mode="open")
    assert context.addressed_to_parker is True
    assert context.source == "open_mode"
    assert routed == "remind me to stretch"


def test_wake_mode_without_name_is_ambient():
    # The false-directed guard: a TV line full of intent verbs must not engage.
    context, routed = _decide("You should remind me to buy those tickets tomorrow")
    assert context.addressed_to_parker is False
    assert context.source == "no_wake_signal"
    assert routed == "You should remind me to buy those tickets tomorrow"


def test_wake_mode_leading_name_directed_and_stripped():
    context, routed = _decide("Hey Parker, remind me to stretch")
    assert context.addressed_to_parker is True
    assert context.source == "wake_name"
    assert routed == "remind me to stretch"


def test_wake_mode_bare_name_routes_empty_text():
    # "Parker?" alone: directed, and the empty routed text lets the session
    # answer "I'm listening."
    context, routed = _decide("Parker?")
    assert context.addressed_to_parker is True
    assert routed == ""


def test_wake_mode_name_mid_utterance_directed_and_unstripped():
    # Rewriting the middle of an effortful utterance risks mangling it.
    context, routed = _decide("what day is it, Parker?")
    assert context.addressed_to_parker is True
    assert context.source == "wake_name"
    assert routed == "what day is it, Parker?"


def test_wake_mode_awaiting_reply_grace_needs_no_name():
    # The missed-directed guard: mid-exchange replies never require the name.
    context, routed = _decide("yes", awaiting_reply=True)
    assert context.addressed_to_parker is True
    assert context.source == "pending_reply"
    assert routed == "yes"


def test_wake_name_matching_is_case_insensitive():
    context, routed = _decide("PARKER remind me to stretch")
    assert context.addressed_to_parker is True
    assert routed == "remind me to stretch"


def test_wake_name_requires_word_boundary():
    # "park" or "parked" must not wake Parker.
    context, _ = _decide("we parked near the park entrance")
    assert context.addressed_to_parker is False


def test_unknown_mode_falls_back_to_open():
    context, _ = _decide("remind me to stretch", mode="always-on-super")
    assert context.addressed_to_parker is True
    assert context.source == "open_mode"


def test_empty_wake_name_falls_back_to_default():
    context, routed = _decide("Parker, remind me to stretch", wake_name="  ")
    assert context.addressed_to_parker is True
    assert routed == "remind me to stretch"


def test_custom_wake_name():
    context, routed = _decide("Jarvis, lights please", wake_name="jarvis")
    assert context.addressed_to_parker is True
    assert routed == "lights please"


# ---------------------------------------------------------------------------
# Window-level gating: ASR splits one window into utterance lines, so the
# window — not the line — is the decision unit.
# ---------------------------------------------------------------------------

from app.conversation.addressing import gate_window  # noqa: E402


def _gate(lines, **overrides):
    kwargs = {"awaiting_reply": False, "mode": "wake", "wake_name": "parker"}
    kwargs.update(overrides)
    return gate_window(lines, **kwargs)


def test_window_split_address_directs_command_and_drops_name_fragment():
    # "Parker, remind me to stretch" arrives split on the comma.
    rows = _gate(["Parker", "remind me to stretch"])
    assert len(rows) == 1
    line, context, routed = rows[0]
    assert line == "remind me to stretch"
    assert context.addressed_to_parker is True
    assert context.source == "wake_name"
    assert routed == "remind me to stretch"


def test_window_trailing_name_directs_whole_window():
    rows = _gate(["remind me to stretch, Parker"])
    (_, context, routed), = rows
    assert context.addressed_to_parker is True
    assert routed == "remind me to stretch, Parker"


def test_window_name_in_any_line_directs_earlier_lines_too():
    # Deliberate trade-off, documented in gate_window: the window was
    # end-pointed by the speaker's own voice, so a name anywhere directs all
    # of it rather than risking a missed command.
    rows = _gate(["the game is on", "Parker, remind me to stretch"])
    assert [routed for _, _, routed in rows] == ["the game is on", "remind me to stretch"]
    assert all(context.addressed_to_parker for _, context, _ in rows)


def test_window_without_name_is_ambient_for_every_line():
    rows = _gate(["turn that TV down", "remind me to call someone"])
    assert len(rows) == 2
    assert all(context.addressed_to_parker is False for _, context, _ in rows)
    assert all(context.source == "no_wake_signal" for _, context, _ in rows)


def test_window_bare_name_alone_is_kept():
    rows = _gate(["Parker?"])
    (line, context, routed), = rows
    assert context.addressed_to_parker is True
    assert routed == ""  # session answers "I'm listening."


def test_window_awaiting_reply_directs_without_name():
    rows = _gate(["yes"], awaiting_reply=True)
    (_, context, routed), = rows
    assert context.addressed_to_parker is True
    assert context.source == "pending_reply"
    assert routed == "yes"


def test_window_open_mode_passthrough():
    rows = _gate(["anything at all"], mode="open")
    (line, context, routed), = rows
    assert context.addressed_to_parker is True
    assert context.source == "open_mode"
    assert routed == "anything at all"


# ---------------------------------------------------------------------------
# Effortful-speech tolerance and misconfiguration visibility
# ---------------------------------------------------------------------------

from app.conversation.addressing import (  # noqa: E402
    describe_address_config,
    resolved_wake_name,
)


def test_leading_fillers_before_name_are_stripped():
    for utterance in (
        "Uh Parker remind me to stretch",
        "Um, Parker, remind me to stretch",
        "Okay, uh, Parker remind me to stretch",
    ):
        context, routed = _decide(utterance)
        assert context.addressed_to_parker is True
        assert routed == "remind me to stretch", utterance


def test_repeated_name_palilalia_is_stripped():
    context, routed = _decide("Parker Parker remind me to stretch")
    assert context.addressed_to_parker is True
    assert routed == "remind me to stretch"


def test_asr_possessive_name_is_stripped():
    # ASR sometimes renders "Parker" as "Parker's".
    context, routed = _decide("Parker's remind me to stretch")
    assert context.addressed_to_parker is True
    assert routed == "remind me to stretch"


def test_punctuated_configured_wake_name_still_matches():
    # "parker!" in config would compile to a never-matching pattern without
    # sanitization — wake mode silently deaf.
    assert resolved_wake_name("parker!") == "parker"
    context, routed = _decide("Parker, remind me to stretch", wake_name="parker!")
    assert context.addressed_to_parker is True
    assert routed == "remind me to stretch"


def test_describe_address_config_surfaces_unknown_mode():
    line = describe_address_config("wake-word", "parker")
    assert "unrecognized" in line
    assert "wake-word" in line


def test_describe_address_config_wake_line_names_the_wake_word():
    line = describe_address_config("wake", "parker")
    assert line.startswith("Address mode: wake")
    assert "parker" in line
