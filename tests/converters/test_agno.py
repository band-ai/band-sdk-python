"""Agno-specific history converter tests.

These cover behavior the framework-conformance suite cannot assert because it
only checks generic shape via an output adapter ("tool name appears somewhere",
text/own-message handling). Here we assert on the real Agno ``Message`` objects:
tool_call/tool_result structure, batching, role mapping, and the ``from_history``
tagging that stops Agno from re-adding stored session history.
"""

from __future__ import annotations

import json

from band.converters.agno import AgnoHistoryConverter
from tests.framework_configs.fixtures import (
    TOOL_CALL_LOOKUP,
    TOOL_CALL_SEARCH,
    TOOL_CALL_SEARCH_EMPTY,
    TOOL_RESULT_SEARCH,
)


def _text(content: str, *, role: str = "user", sender_name: str = "") -> dict:
    return {
        "role": role,
        "content": content,
        "sender_name": sender_name,
        "message_type": "text",
    }


class TestToolCallShape:
    def test_tool_call_becomes_assistant_message_with_function_dict(self):
        result = AgnoHistoryConverter().convert([dict(TOOL_CALL_SEARCH)])

        assert len(result) == 1
        msg = result[0]
        assert msg.role == "assistant"
        assert msg.content is None
        assert msg.from_history is True
        assert msg.tool_calls == [
            {
                "id": "tc_1",
                "type": "function",
                "function": {
                    "name": "search",
                    "arguments": json.dumps({"query": "test"}),
                },
            }
        ]

    def test_arguments_are_json_string_not_dict(self):
        result = AgnoHistoryConverter().convert([dict(TOOL_CALL_SEARCH)])

        arguments = result[0].tool_calls[0]["function"]["arguments"]
        assert isinstance(arguments, str)
        assert json.loads(arguments) == {"query": "test"}


class TestToolResultShape:
    def test_tool_result_becomes_tool_role_message(self):
        result = AgnoHistoryConverter().convert(
            [dict(TOOL_CALL_SEARCH), dict(TOOL_RESULT_SEARCH)]
        )

        assert len(result) == 2
        tool_msg = result[1]
        assert tool_msg.role == "tool"
        assert tool_msg.tool_call_id == "tc_1"
        assert tool_msg.tool_name == "search"
        assert tool_msg.content == "result data"
        assert tool_msg.tool_call_error is False
        assert tool_msg.from_history is True

    def test_error_flag_maps_to_tool_call_error(self):
        errored = {
            "role": "assistant",
            "content": json.dumps(
                {
                    "name": "search",
                    "output": "boom",
                    "tool_call_id": "tc_1",
                    "is_error": True,
                }
            ),
            "message_type": "tool_result",
        }

        result = AgnoHistoryConverter().convert([errored])

        assert result[0].tool_call_error is True


class TestBatchingAndFlush:
    def test_consecutive_tool_calls_batch_into_one_message(self):
        result = AgnoHistoryConverter().convert(
            [dict(TOOL_CALL_SEARCH), dict(TOOL_CALL_LOOKUP)]
        )

        assert len(result) == 1
        assert len(result[0].tool_calls) == 2
        assert [tc["function"]["name"] for tc in result[0].tool_calls] == [
            "search",
            "lookup",
        ]

    def test_text_flushes_pending_calls_before_appending(self):
        result = AgnoHistoryConverter().convert(
            [dict(TOOL_CALL_SEARCH), _text("done", sender_name="Alice")]
        )

        assert [m.role for m in result] == ["assistant", "user"]
        assert result[0].tool_calls[0]["function"]["name"] == "search"
        assert result[1].content == "[Alice]: done"

    def test_orphaned_trailing_tool_calls_are_flushed(self):
        result = AgnoHistoryConverter().convert([dict(TOOL_CALL_SEARCH)])

        # No matching tool_result, but the pending call still lands as a message.
        assert len(result) == 1
        assert result[0].role == "assistant"


class TestTextRoleMapping:
    def test_own_agent_text_kept_as_assistant(self):
        converter = AgnoHistoryConverter(agent_name="TestBot")

        result = converter.convert(
            [_text("on it", role="assistant", sender_name="TestBot")]
        )

        assert len(result) == 1
        assert result[0].role == "assistant"
        assert result[0].content == "on it"
        assert result[0].from_history is True

    def test_other_sender_gets_user_role_with_prefix(self):
        converter = AgnoHistoryConverter(agent_name="TestBot")

        result = converter.convert([_text("hi", sender_name="Alice")])

        assert result[0].role == "user"
        assert result[0].content == "[Alice]: hi"

    def test_missing_sender_name_has_no_prefix(self):
        result = AgnoHistoryConverter().convert([_text("hi")])

        assert result[0].content == "hi"


class TestFromHistoryInvariant:
    def test_every_message_is_tagged_from_history(self):
        converter = AgnoHistoryConverter(agent_name="TestBot")

        result = converter.convert(
            [
                _text("hi", sender_name="Alice"),
                dict(TOOL_CALL_SEARCH),
                dict(TOOL_RESULT_SEARCH),
                _text("done", role="assistant", sender_name="TestBot"),
            ]
        )

        assert result  # sanity: not empty
        assert all(m.from_history for m in result)


class TestMalformedAndUnknown:
    def test_tool_call_missing_id_is_skipped(self):
        result = AgnoHistoryConverter().convert([dict(TOOL_CALL_SEARCH_EMPTY)])

        # TOOL_CALL_SEARCH_EMPTY still has a tool_call_id, so it converts; a
        # genuinely id-less call is dropped:
        idless = {
            "role": "assistant",
            "content": json.dumps({"name": "search", "args": {}}),
            "message_type": "tool_call",
        }
        assert AgnoHistoryConverter().convert([idless]) == []
        assert len(result) == 1  # empty args still produce a valid call

    def test_unknown_message_type_is_skipped(self):
        thought = {"role": "assistant", "content": "hmm", "message_type": "thought"}

        assert AgnoHistoryConverter().convert([thought]) == []

    def test_empty_history(self):
        assert AgnoHistoryConverter().convert([]) == []
