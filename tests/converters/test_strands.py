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


class TestToolEventFormat:
    def test_tool_call_becomes_assistant_tool_use_block(self):
        converter = StrandsHistoryConverter()

        result = converter.convert(
            [_tool_call("band_send_message", {"content": "hi"}, "call-1")]
        )

        assert result == [
            {
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
        ]

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

    def test_own_text_mid_tool_call_is_dropped(self):
        """Own text between a toolUse and its toolResult would split the pair."""
        converter = StrandsHistoryConverter(agent_name="Bot")

        result = converter.convert(
            [
                _tool_call("band_send_message", {"content": "hi"}, "call-1"),
                {
                    "role": "assistant",
                    "content": "hi",
                    "sender_name": "Bot",
                    "message_type": "text",
                },
                _tool_result("band_send_message", "sent", "call-1"),
            ]
        )

        assert len(result) == 2
        assert "toolUse" in result[0]["content"][0]
        assert "toolResult" in result[1]["content"][0]


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
