"""Unit tests for the Strands history converter (framework-specific behavior).

The shared behavioral contract is covered by
tests/framework_conformance/test_converter_conformance.py; these tests pin the
Converse-specific output shapes: toolUse/toolResult blocks, batching, and
malformed tool-event handling.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("strands", reason="strands extra not installed")

from band.converters.strands import StrandsHistoryConverter  # noqa: E402


def _tool_call(name: str, args: dict, call_id: str) -> dict:
    return {
        "role": "assistant",
        "content": json.dumps({"name": name, "args": args, "tool_call_id": call_id}),
        "message_type": "tool_call",
    }


def _tool_result(name: str, output: str, call_id: str, is_error: bool = False) -> dict:
    return {
        "role": "assistant",
        "content": json.dumps(
            {
                "name": name,
                "output": output,
                "tool_call_id": call_id,
                "is_error": is_error,
            }
        ),
        "message_type": "tool_result",
    }


def _text(content: str, sender: str = "Alice", role: str = "user") -> dict:
    return {
        "role": role,
        "content": content,
        "sender_name": sender,
        "message_type": "text",
    }


def _outline(messages: list[dict]) -> list[str]:
    """Render each message as ``role: block(id)`` for readable assertions."""

    def describe(block: dict) -> str:
        if "toolUse" in block:
            return f"toolUse({block['toolUse']['toolUseId']})"
        if "toolResult" in block:
            result = block["toolResult"]
            return f"toolResult({result['toolUseId']}, {result['status']})"
        return f"text({block['text']})"

    return [
        f"{message['role']}: {' '.join(describe(b) for b in message['content'])}"
        for message in messages
    ]


class TestToolEventFormat:
    def test_tool_call_becomes_assistant_tool_use_block(self):
        converter = StrandsHistoryConverter()

        result = converter.convert(
            [
                _tool_call("band_send_message", {"content": "hi"}, "call-1"),
                _tool_result("band_send_message", "sent", "call-1"),
            ]
        )

        assert result[0] == {
            "role": "assistant",
            "content": [
                {
                    "toolUse": {
                        "toolUseId": "call-1",
                        "name": "band_send_message",
                        "input": {"content": "hi"},
                    }
                }
            ],
        }

    def test_tool_result_becomes_user_tool_result_block(self):
        converter = StrandsHistoryConverter()

        result = converter.convert(
            [
                _tool_call("search", {"q": "x"}, "call-1"),
                _tool_result("search", "found it", "call-1"),
            ]
        )

        assert result[1] == {
            "role": "user",
            "content": [
                {
                    "toolResult": {
                        "toolUseId": "call-1",
                        "status": "success",
                        "content": [{"text": "found it"}],
                    }
                }
            ],
        }

    def test_error_tool_result_gets_error_status(self):
        converter = StrandsHistoryConverter()

        result = converter.convert(
            [
                _tool_call("search", {}, "call-1"),
                _tool_result("search", "boom", "call-1", is_error=True),
            ]
        )

        assert result[1]["content"][0]["toolResult"]["status"] == "error"


class TestBatching:
    def test_parallel_tool_calls_batch_into_one_assistant_message(self):
        converter = StrandsHistoryConverter()

        result = converter.convert(
            [
                _tool_call("a", {}, "call-a"),
                _tool_call("b", {}, "call-b"),
                _tool_result("a", "ra", "call-a"),
                _tool_result("b", "rb", "call-b"),
            ]
        )

        assert len(result) == 2
        assert result[0]["role"] == "assistant"
        assert [b["toolUse"]["toolUseId"] for b in result[0]["content"]] == [
            "call-a",
            "call-b",
        ]
        assert result[1]["role"] == "user"
        assert [b["toolResult"]["toolUseId"] for b in result[1]["content"]] == [
            "call-a",
            "call-b",
        ]


class TestToolPairIntegrity:
    """Converse rejects a toolUse that its toolResult does not immediately follow."""

    def test_own_narration_mid_tool_call_is_dropped(self):
        """The agent's own text is already implied by the pair, so it is not held."""
        converter = StrandsHistoryConverter(agent_name="Bot")

        result = converter.convert(
            [
                _tool_call("band_send_message", {"content": "hi"}, "call-1"),
                _text("hi", sender="Bot", role="assistant"),
                _tool_result("band_send_message", "sent", "call-1"),
            ]
        )

        assert _outline(result) == [
            "assistant: toolUse(call-1)",
            "user: toolResult(call-1, success)",
        ]

    def test_peer_message_mid_tool_call_is_held_until_the_pair_closes(self):
        converter = StrandsHistoryConverter(agent_name="Bot")

        result = converter.convert(
            [
                _tool_call("calc", {"expr": "2+2"}, "call-1"),
                _text("also, hello"),
                _tool_result("calc", "4", "call-1"),
            ]
        )

        assert _outline(result) == [
            "assistant: toolUse(call-1)",
            "user: toolResult(call-1, success) text([Alice]: also, hello)",
        ]

    def test_peer_message_between_parallel_calls_is_held_too(self):
        """A second parallel call must not release the turns held by the first.

        Releasing them there emits the peer turn before the assistant message
        carrying the calls it actually arrived after, silently reordering history.
        """
        converter = StrandsHistoryConverter(agent_name="Bot")

        result = converter.convert(
            [
                _tool_call("a", {}, "call-a"),
                _text("also, hello"),
                _tool_call("b", {}, "call-b"),
                _tool_result("a", "ra", "call-a"),
                _tool_result("b", "rb", "call-b"),
            ]
        )

        assert _outline(result) == [
            "assistant: toolUse(call-a) toolUse(call-b)",
            "user: toolResult(call-a, success) toolResult(call-b, success) "
            "text([Alice]: also, hello)",
        ]

    def test_missing_tool_result_is_answered_with_a_synthetic_error(self):
        converter = StrandsHistoryConverter(agent_name="Bot")

        result = converter.convert(
            [
                _tool_call("calc", {}, "call-1"),
                _text("still there?"),
            ]
        )

        assert _outline(result) == [
            "assistant: toolUse(call-1)",
            "user: toolResult(call-1, error) text([Alice]: still there?)",
        ]

    def test_a_late_call_joins_the_round_it_interleaved_with(self):
        """Strands runs a round's tools concurrently, so its hooks interleave.

        Call C can be recorded after result A. Emitting A's result there would
        strand B, and C's own result would then answer a toolUse no longer
        immediately before it — which Converse rejects on the next rehydration.
        """
        converter = StrandsHistoryConverter(agent_name="Bot")

        result = converter.convert(
            [
                _tool_call("a", {}, "call-a"),
                _tool_call("b", {}, "call-b"),
                _tool_result("a", "ra", "call-a"),
                _tool_call("c", {}, "call-c"),
                _tool_result("b", "rb", "call-b"),
                _tool_result("c", "rc", "call-c"),
            ]
        )

        assert _outline(result) == [
            "assistant: toolUse(call-a) toolUse(call-b) toolUse(call-c)",
            "user: toolResult(call-a, success) toolResult(call-b, success) "
            "toolResult(call-c, success)",
        ]

    def test_partially_answered_parallel_calls_are_completed_in_place(self):
        converter = StrandsHistoryConverter(agent_name="Bot")

        result = converter.convert(
            [
                _tool_call("a", {}, "call-a"),
                _tool_call("b", {}, "call-b"),
                _tool_result("a", "ra", "call-a"),
            ]
        )

        assert _outline(result) == [
            "assistant: toolUse(call-a) toolUse(call-b)",
            "user: toolResult(call-b, error) toolResult(call-a, success)",
        ]

    def test_own_text_after_the_pair_closes_is_preserved(self):
        converter = StrandsHistoryConverter(agent_name="Bot")

        result = converter.convert(
            [
                _tool_call("calc", {}, "call-1"),
                _tool_result("calc", "4", "call-1"),
                _text("the answer is 4", sender="Bot", role="assistant"),
            ]
        )

        assert _outline(result) == [
            "assistant: toolUse(call-1)",
            "user: toolResult(call-1, success)",
            "assistant: text(the answer is 4)",
        ]


class TestRoleAlternation:
    """Bedrock's Converse rejects a conversation that does not alternate."""

    def test_consecutive_speakers_become_one_turn(self):
        converter = StrandsHistoryConverter(agent_name="Bot")

        result = converter.convert([_text("one"), _text("two", sender="Bob")])

        assert _outline(result) == ["user: text([Alice]: one) text([Bob]: two)"]

    def test_every_transcript_alternates(self):
        converter = StrandsHistoryConverter(agent_name="Bot")

        result = converter.convert(
            [
                _text("one"),
                _text("two", sender="Bob"),
                _text("on it", sender="Bot", role="assistant"),
                _tool_call("calc", {}, "call-1"),
                _text("hurry up"),
                _tool_result("calc", "4", "call-1"),
                _text("done", sender="Bot", role="assistant"),
            ]
        )

        assert _outline(result) == [
            "user: text([Alice]: one) text([Bob]: two)",
            "assistant: text(on it) toolUse(call-1)",
            "user: toolResult(call-1, success) text([Alice]: hurry up)",
            "assistant: text(done)",
        ]


class TestMalformedInput:
    def test_malformed_tool_events_are_skipped(self):
        converter = StrandsHistoryConverter()

        result = converter.convert(
            [
                {
                    "role": "assistant",
                    "content": "not json at all",
                    "message_type": "tool_call",
                },
                {
                    "role": "assistant",
                    "content": json.dumps({"name": "x"}),  # missing tool_call_id
                    "message_type": "tool_result",
                },
            ]
        )

        assert result == []

    def test_unknown_message_type_is_skipped(self):
        converter = StrandsHistoryConverter()

        result = converter.convert(
            [{"role": "user", "content": "hi", "message_type": "mystery"}]
        )

        assert result == []
