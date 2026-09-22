"""Shared tool-event payloads for conformance and framework-specific tests.

Payloads include BOTH top-level keys and nested data.* paths because
different frameworks read from different locations:
  - Anthropic, ClaudeSDK, PydanticAI: read top-level args/output
  - LangChain: reads data.input / data.output

Always pair tool_call + tool_result so frameworks that require
pairing (e.g. LangChain) produce valid output.
"""

from __future__ import annotations

import json
from types import MappingProxyType

__all__ = [
    "TOOL_CALL_LOOKUP",
    "TOOL_CALL_SEARCH",
    "TOOL_CALL_SEARCH_EMPTY",
    "TOOL_RESULT_LOOKUP",
    "TOOL_RESULT_SEARCH",
    "TOOL_RESULT_SEARCH_FOUND",
]


def _tool_call_content(name: str, args: dict, tool_call_id: str) -> str:
    """Build a tool_call content JSON string with both top-level and nested paths.

    Top-level ``args`` is read by Anthropic, ClaudeSDK, PydanticAI.
    Nested ``data.input`` is read by LangChain.
    If you change the tool event schema, this single function is the
    only place that needs updating.
    """
    return json.dumps(
        {
            "name": name,
            "args": args,
            "data": {"input": args},
            "tool_call_id": tool_call_id,
        },
    )


def _tool_result_content(name: str, output: str, tool_call_id: str) -> str:
    """Build a tool_result content JSON string with both top-level and nested paths.

    Top-level ``output`` is read by Anthropic, ClaudeSDK, PydanticAI.
    Nested ``data.output`` is read by LangChain.
    If you change the tool event schema, this single function is the
    only place that needs updating.
    """
    return json.dumps(
        {
            "name": name,
            "output": output,
            "data": {"output": output},
            "tool_call_id": tool_call_id,
        },
    )


# Each payload below includes BOTH top-level keys (args/output) and nested
# data.* paths so that every framework's converter sees the fields it expects.
# See _tool_call_content() / _tool_result_content() docstrings for which
# frameworks read which path.  When adding a new converter, verify that these
# payloads satisfy its schema — do NOT rely on a path you haven't checked.

TOOL_CALL_SEARCH = MappingProxyType(
    {
        "role": "assistant",
        "content": _tool_call_content("search", {"query": "test"}, "tc_1"),
        "message_type": "tool_call",
    }
)
TOOL_RESULT_SEARCH = MappingProxyType(
    {
        "role": "assistant",
        "content": _tool_result_content("search", "result data", "tc_1"),
        "message_type": "tool_result",
    }
)
TOOL_CALL_LOOKUP = MappingProxyType(
    {
        "role": "assistant",
        "content": _tool_call_content("lookup", {"id": "42"}, "tc_2"),
        "message_type": "tool_call",
    }
)
TOOL_RESULT_LOOKUP = MappingProxyType(
    {
        "role": "assistant",
        "content": _tool_result_content("lookup", "found item 42", "tc_2"),
        "message_type": "tool_result",
    }
)
TOOL_CALL_SEARCH_EMPTY = MappingProxyType(
    {
        "role": "assistant",
        "content": _tool_call_content("search", {}, "tc_1"),
        "message_type": "tool_call",
    }
)
TOOL_RESULT_SEARCH_FOUND = MappingProxyType(
    {
        "role": "assistant",
        "content": _tool_result_content("search", "found", "tc_1"),
        "message_type": "tool_result",
    }
)
