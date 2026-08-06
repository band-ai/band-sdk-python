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
from tests.strandskit import outline, text, tool_call, tool_result  # noqa: E402


class TestToolEventFormat:
    def test_tool_call_becomes_assistant_tool_use_block(self):
        converter = StrandsHistoryConverter()

        result = converter.convert(
            [
                tool_call("band_send_message", {"content": "hi"}, "call-1"),
                tool_result("band_send_message", "sent", "call-1"),
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
                tool_call("search", {"q": "x"}, "call-1"),
                tool_result("search", "found it", "call-1"),
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
                tool_call("search", {}, "call-1"),
                tool_result("search", "boom", "call-1", is_error=True),
            ]
        )

        assert result[1]["content"][0]["toolResult"]["status"] == "error"


class TestBatching:
    def test_parallel_tool_calls_batch_into_one_assistant_message(self):
        converter = StrandsHistoryConverter()

        result = converter.convert(
            [
                tool_call("a", {}, "call-a"),
                tool_call("b", {}, "call-b"),
                tool_result("a", "ra", "call-a"),
                tool_result("b", "rb", "call-b"),
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
                tool_call("band_send_message", {"content": "hi"}, "call-1"),
                text("hi", sender="Bot", role="assistant"),
                tool_result("band_send_message", "sent", "call-1"),
            ]
        )

        assert outline(result) == [
            "assistant: toolUse(call-1)",
            "user: toolResult(call-1, success)",
        ]

    def test_peer_message_mid_tool_call_is_held_until_the_pair_closes(self):
        converter = StrandsHistoryConverter(agent_name="Bot")

        result = converter.convert(
            [
                tool_call("calc", {"expr": "2+2"}, "call-1"),
                text("also, hello"),
                tool_result("calc", "4", "call-1"),
            ]
        )

        assert outline(result) == [
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
                tool_call("a", {}, "call-a"),
                text("also, hello"),
                tool_call("b", {}, "call-b"),
                tool_result("a", "ra", "call-a"),
                tool_result("b", "rb", "call-b"),
            ]
        )

        assert outline(result) == [
            "assistant: toolUse(call-a) toolUse(call-b)",
            "user: toolResult(call-a, success) toolResult(call-b, success) "
            "text([Alice]: also, hello)",
        ]

    def test_missing_tool_result_is_answered_with_a_synthetic_error(self):
        converter = StrandsHistoryConverter(agent_name="Bot")

        result = converter.convert(
            [
                tool_call("calc", {}, "call-1"),
                text("still there?"),
            ]
        )

        assert outline(result) == [
            "assistant: toolUse(call-1)",
            "user: toolResult(call-1, error) text([Alice]: still there?)",
        ]

    def test_orphaned_tool_result_gets_a_synthetic_tool_use(self):
        """The tool_call event can be lost (failed send, unparseable payload).

        Emitting the surviving result alone would leave a toolResult with no
        preceding toolUse — which Converse rejects on every later turn in the
        room — so the converter synthesizes the call it answers.
        """
        converter = StrandsHistoryConverter(agent_name="Bot")

        result = converter.convert(
            [
                text("please send it"),
                tool_result("band_send_message", "sent", "call-lost"),
                text("thanks"),
            ]
        )

        assert outline(result) == [
            "user: text([Alice]: please send it)",
            "assistant: toolUse(call-lost)",
            "user: toolResult(call-lost, success) text([Alice]: thanks)",
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
                tool_call("a", {}, "call-a"),
                tool_call("b", {}, "call-b"),
                tool_result("a", "ra", "call-a"),
                tool_call("c", {}, "call-c"),
                tool_result("b", "rb", "call-b"),
                tool_result("c", "rc", "call-c"),
            ]
        )

        assert outline(result) == [
            "assistant: toolUse(call-a) toolUse(call-b) toolUse(call-c)",
            "user: toolResult(call-a, success) toolResult(call-b, success) "
            "toolResult(call-c, success)",
        ]

    def test_partially_answered_parallel_calls_are_completed_in_place(self):
        converter = StrandsHistoryConverter(agent_name="Bot")

        result = converter.convert(
            [
                tool_call("a", {}, "call-a"),
                tool_call("b", {}, "call-b"),
                tool_result("a", "ra", "call-a"),
            ]
        )

        assert outline(result) == [
            "assistant: toolUse(call-a) toolUse(call-b)",
            "user: toolResult(call-b, error) toolResult(call-a, success)",
        ]

    def test_own_text_after_the_pair_closes_is_preserved(self):
        converter = StrandsHistoryConverter(agent_name="Bot")

        result = converter.convert(
            [
                tool_call("calc", {}, "call-1"),
                tool_result("calc", "4", "call-1"),
                text("the answer is 4", sender="Bot", role="assistant"),
            ]
        )

        assert outline(result) == [
            "assistant: toolUse(call-1)",
            "user: toolResult(call-1, success)",
            "assistant: text(the answer is 4)",
        ]


class TestRoleAlternation:
    """Bedrock's Converse rejects a conversation that does not alternate."""

    def test_consecutive_speakers_become_one_turn(self):
        converter = StrandsHistoryConverter(agent_name="Bot")

        result = converter.convert([text("one"), text("two", sender="Bob")])

        assert outline(result) == ["user: text([Alice]: one) text([Bob]: two)"]

    def test_every_transcript_alternates(self):
        converter = StrandsHistoryConverter(agent_name="Bot")

        result = converter.convert(
            [
                text("one"),
                text("two", sender="Bob"),
                text("on it", sender="Bot", role="assistant"),
                tool_call("calc", {}, "call-1"),
                text("hurry up"),
                tool_result("calc", "4", "call-1"),
                text("done", sender="Bot", role="assistant"),
            ]
        )

        assert outline(result) == [
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
