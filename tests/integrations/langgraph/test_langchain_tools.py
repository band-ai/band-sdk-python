"""Tests for LangGraph platform tool wrappers."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from thenvoi.core.types import AdapterFeatures, Capability
from thenvoi.integrations.langgraph.langchain_tools import agent_tools_to_langchain
from thenvoi.runtime.tools import CHAT_TOOL_NAMES, CONTACT_TOOL_NAMES, MEMORY_TOOL_NAMES


def make_tools() -> MagicMock:
    tools = MagicMock()
    tools.is_hub_room = False

    async def execute_tool_call(tool_name: str, arguments: dict[str, Any]) -> Any:
        results = {
            "thenvoi_send_message": {"status": "sent"},
            "thenvoi_send_event": {"status": "event-sent"},
            "thenvoi_add_contact": {"id": "contact-1"},
            "thenvoi_store_memory": {"id": "memory-1"},
        }
        return results.get(tool_name, {"status": "ok"})

    tools.execute_tool_call = AsyncMock(side_effect=execute_tool_call)
    return tools


def by_name(tools: list[Any]) -> dict[str, Any]:
    return {tool.name: tool for tool in tools}


def test_base_tools_are_always_exposed() -> None:
    tool_names = {tool.name for tool in agent_tools_to_langchain(make_tools())}

    assert CHAT_TOOL_NAMES <= tool_names
    assert CONTACT_TOOL_NAMES.isdisjoint(tool_names)
    assert MEMORY_TOOL_NAMES.isdisjoint(tool_names)


def test_contact_and_memory_tools_are_capability_gated() -> None:
    without_optional = {tool.name for tool in agent_tools_to_langchain(make_tools())}
    with_contacts = {
        tool.name
        for tool in agent_tools_to_langchain(
            make_tools(),
            features=AdapterFeatures(capabilities=frozenset({Capability.CONTACTS})),
        )
    }
    with_memory = {
        tool.name
        for tool in agent_tools_to_langchain(
            make_tools(),
            features=AdapterFeatures(capabilities=frozenset({Capability.MEMORY})),
        )
    }

    assert CONTACT_TOOL_NAMES.isdisjoint(without_optional)
    assert MEMORY_TOOL_NAMES.isdisjoint(without_optional)
    assert CONTACT_TOOL_NAMES <= with_contacts
    assert MEMORY_TOOL_NAMES <= with_memory


@pytest.mark.asyncio
async def test_wrappers_call_agent_tools_methods() -> None:
    tools = make_tools()
    wrapped = by_name(
        agent_tools_to_langchain(
            tools,
            features=AdapterFeatures(
                capabilities=frozenset({Capability.CONTACTS, Capability.MEMORY})
            ),
        )
    )

    assert await wrapped["thenvoi_send_message"].ainvoke(
        {"content": "hello", "mentions": ["00000000-0000-0000-0000-000000000001"]}
    ) == {"status": "sent"}
    tools.execute_tool_call.assert_any_await(
        "thenvoi_send_message",
        {"content": "hello", "mentions": ["00000000-0000-0000-0000-000000000001"]},
    )

    assert await wrapped["thenvoi_send_event"].ainvoke(
        {"content": "working", "message_type": "thought"}
    ) == {"status": "event-sent"}
    tools.execute_tool_call.assert_any_await(
        "thenvoi_send_event",
        {"content": "working", "message_type": "thought", "metadata": None},
    )

    assert await wrapped["thenvoi_add_participant"].ainvoke(
        {"identifier": "Helper"}
    ) == {"status": "ok"}
    tools.execute_tool_call.assert_any_await(
        "thenvoi_add_participant", {"identifier": "Helper", "role": "member"}
    )

    assert await wrapped["thenvoi_add_contact"].ainvoke(
        {"handle": "@bob", "message": "hi"}
    ) == {"id": "contact-1"}
    tools.execute_tool_call.assert_any_await(
        "thenvoi_add_contact", {"handle": "@bob", "message": "hi"}
    )

    assert await wrapped["thenvoi_store_memory"].ainvoke(
        {
            "content": "prefers concise answers",
            "system": "long_term",
            "type": "semantic",
            "segment": "user",
            "thought": "user stated preference",
        }
    ) == {"id": "memory-1"}
    tools.execute_tool_call.assert_any_await(
        "thenvoi_store_memory",
        {
            "content": "prefers concise answers",
            "system": "long_term",
            "type": "semantic",
            "segment": "user",
            "thought": "user stated preference",
            "scope": "subject",
            "subject_id": None,
            "metadata": None,
        },
    )


def test_add_participant_schema_exposes_identifier_and_role() -> None:
    wrapped = by_name(agent_tools_to_langchain(make_tools()))
    schema_fields = wrapped["thenvoi_add_participant"].args_schema.model_fields

    assert "identifier" in schema_fields
    assert "role" in schema_fields


@pytest.mark.asyncio
async def test_wrapper_errors_are_returned_to_model() -> None:
    tools = make_tools()
    tools.execute_tool_call.side_effect = RuntimeError("platform unavailable")
    wrapped = by_name(agent_tools_to_langchain(tools))

    result = await wrapped["thenvoi_send_message"].ainvoke(
        {"content": "hello", "mentions": ["00000000-0000-0000-0000-000000000001"]}
    )

    assert result == "Error executing thenvoi_send_message: see agent logs."
    assert "platform unavailable" not in result
