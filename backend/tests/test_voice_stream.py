"""Tests for the Twilio/OpenAI realtime voice stream bridge."""

from __future__ import annotations

import audioop
import asyncio
import base64
import json

from app.conversation.tools import TOOL_DEFINITIONS
from app.voice import stream


def test_twilio_payload_to_openai_pcm24_known_values() -> None:
    """A short 8 kHz PCM clip should become 24 kHz PCM wrapped in base64."""

    pcm8 = b"\x00\x00\xff\x7f\x00\x80\x34\x12"
    mulaw = audioop.lin2ulaw(pcm8, 2)
    payload = base64.b64encode(mulaw).decode("ascii")

    converted, state = stream.twilio_payload_to_openai_pcm24(payload)

    pcm24 = base64.b64decode(converted)
    assert state is not None
    assert len(pcm24) >= len(pcm8) * 2  # ratecv may hold boundary samples in state
    assert len(pcm24) % 2 == 0


def test_openai_pcm24_to_twilio_payload_known_values() -> None:
    """A 24 kHz PCM clip should become Twilio mulaw audio."""

    pcm24 = b"\x00\x00\x00\x10\x00\x20\x00\x30\x00\x40\x00\x50"
    payload = base64.b64encode(pcm24).decode("ascii")

    converted, state = stream.openai_pcm24_to_twilio_payload(payload)

    mulaw = base64.b64decode(converted)
    assert state is not None
    assert len(mulaw) > 0
    assert len(mulaw) < len(pcm24)


def test_audio_conversion_round_trip() -> None:
    """Round-tripping through the bridge codecs should preserve valid PCM shape."""

    pcm8 = b"".join(int(i * 300).to_bytes(2, "little", signed=True) for i in range(-20, 20))
    twilio_payload = base64.b64encode(audioop.lin2ulaw(pcm8, 2)).decode("ascii")

    openai_payload, _ = stream.twilio_payload_to_openai_pcm24(twilio_payload)
    round_trip_payload, _ = stream.openai_pcm24_to_twilio_payload(openai_payload)
    round_trip_mulaw = base64.b64decode(round_trip_payload)
    round_trip_pcm8 = audioop.ulaw2lin(round_trip_mulaw, 2)

    assert len(round_trip_mulaw) > 0
    assert len(round_trip_pcm8) % 2 == 0
    assert max(abs(int.from_bytes(round_trip_pcm8[i : i + 2], "little", signed=True)) for i in range(0, len(round_trip_pcm8), 2)) > 0


def test_parse_twilio_event_and_media_message() -> None:
    raw = json.dumps(
        {
            "event": "media",
            "streamSid": "MZ123",
            "media": {"payload": "abc123"},
        }
    )

    parsed = stream.parse_twilio_event(raw)
    message = stream.twilio_media_message("MZ123", "payload")

    assert parsed["event"] == "media"
    assert parsed["media"]["payload"] == "abc123"
    assert message == {"event": "media", "streamSid": "MZ123", "media": {"payload": "payload"}}


def test_parse_twilio_event_rejects_non_object() -> None:
    try:
        stream.parse_twilio_event("[]")
    except ValueError as exc:
        assert "JSON object" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_realtime_tool_definitions_are_flattened() -> None:
    tools = stream.realtime_tool_definitions(TOOL_DEFINITIONS)

    assert tools
    assert tools[0]["type"] == "function"
    assert tools[0]["name"] == "log_medication"
    assert "function" not in tools[0]
    assert tools[0]["parameters"]["type"] == "object"


def test_extract_function_call_event_shapes() -> None:
    done_event = {
        "type": "response.function_call_arguments.done",
        "name": "record_mood",
        "call_id": "call_1",
        "arguments": '{"mood":"good"}',
    }
    item_event = {
        "type": "response.output_item.done",
        "item": {
            "type": "function_call",
            "name": "record_mood",
            "call_id": "call_2",
            "arguments": '{"mood":"okay"}',
        },
    }

    assert stream.extract_function_call(done_event) == {
        "name": "record_mood",
        "call_id": "call_1",
        "arguments": '{"mood":"good"}',
    }
    assert stream.extract_function_call(item_event)["call_id"] == "call_2"


def test_maybe_handle_function_call_sends_tool_output(monkeypatch, db) -> None:
    sent: list[str] = []

    async def fake_send(message: str) -> None:
        sent.append(message)

    def fake_session_local():
        return db

    monkeypatch.setattr(stream, "SessionLocal", fake_session_local)
    monkeypatch.setattr(
        stream,
        "execute_tool",
        lambda db, call_log_id, name, arguments: {"status": "ok", "name": name, "args": arguments},
    )
    state = stream.StreamState()
    state.call_log_id = 42

    asyncio.run(
        stream.maybe_handle_function_call(
            {
                "type": "response.function_call_arguments.done",
                "name": "record_mood",
                "call_id": "call_1",
                "arguments": '{"mood":"good"}',
            },
            fake_send,
            state,
        )
    )

    assert len(sent) == 2
    output = json.loads(sent[0])
    assert output["type"] == "conversation.item.create"
    assert output["item"]["type"] == "function_call_output"
    assert json.loads(output["item"]["output"])["status"] == "ok"
    assert json.loads(sent[1]) == {"type": "response.create"}
