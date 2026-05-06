"""Tests for LangGraph platform tool wrappers."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from thenvoi.integrations.langgraph.langchain_tools import agent_tools_to_langchain


BASE_TOOL_NAMES = {
    "thenvoi_send_message",
    "thenvoi_add_participant",
    "thenvoi_remove_participant",
    "thenvoi_lookup_peers",
    "thenvoi_get_participants",
    "thenvoi_create_chatroom",
    "thenvoi_send_event",
}

CONTACT_TOOL_NAMES = {
    "thenvoi_list_contacts",
    "thenvoi_add_contact",
    "thenvoi_remove_contact",
    "thenvoi_list_contact_requests",
    "thenvoi_respond_contact_request",
}

MEMORY_TOOL_NAMES = {
    "thenvoi_list_memories",
    "thenvoi_store_memory",
    "thenvoi_get_memory",
    "thenvoi_supersede_memory",
    "thenvoi_archive_memory",
}


def make_tools() -> MagicMock:
    tools = MagicMock()
    tools.send_message = AsyncMock(return_value={"status": "sent"})
    tools.add_participant = AsyncMock(return_value={"id": "participant-1"})
    tools.remove_participant = AsyncMock(return_value={"status": "removed"})
    tools.lookup_peers = AsyncMock(return_value={"peers": []})
    tools.get_participants = AsyncMock(return_value=[])
    tools.create_chatroom = AsyncMock(return_value="room-1")
    tools.send_event = AsyncMock(return_value={"status": "event-sent"})
    tools.list_contacts = AsyncMock(return_value={"contacts": []})
    tools.add_contact = AsyncMock(return_value={"id": "contact-1"})
    tools.remove_contact = AsyncMock(return_value={"status": "removed"})
    tools.list_contact_requests = AsyncMock(return_value={"requests": []})
    tools.respond_contact_request = AsyncMock(return_value={"status": "approved"})
    tools.list_memories = AsyncMock(return_value={"memories": []})
    tools.store_memory = AsyncMock(return_value={"id": "memory-1"})
    tools.get_memory = AsyncMock(return_value={"id": "memory-1"})
    tools.supersede_memory = AsyncMock(return_value={"status": "superseded"})
    tools.archive_memory = AsyncMock(return_value={"status": "archived"})
    return tools


def by_name(tools: list[Any]) -> dict[str, Any]:
    return {tool.name: tool for tool in tools}


def test_base_tools_are_always_exposed() -> None:
    tool_names = {
        tool.name
        for tool in agent_tools_to_langchain(make_tools(), include_contacts=False)
    }

    assert BASE_TOOL_NAMES <= tool_names
    assert CONTACT_TOOL_NAMES.isdisjoint(tool_names)
    assert MEMORY_TOOL_NAMES.isdisjoint(tool_names)


def test_contact_and_memory_tools_are_capability_gated() -> None:
    without_optional = {
        tool.name
        for tool in agent_tools_to_langchain(make_tools(), include_contacts=False)
    }
    with_contacts = {
        tool.name
        for tool in agent_tools_to_langchain(make_tools(), include_contacts=True)
    }
    with_memory = {
        tool.name
        for tool in agent_tools_to_langchain(make_tools(), include_memory_tools=True)
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
            include_contacts=True,
            include_memory_tools=True,
        )
    )

    assert await wrapped["thenvoi_send_message"].ainvoke(
        {"content": "hello", "mentions": ["@alice"]}
    ) == {"status": "sent"}
    tools.send_message.assert_awaited_once_with("hello", ["@alice"])

    assert await wrapped["thenvoi_send_event"].ainvoke(
        {"content": "working", "message_type": "thought"}
    ) == {"status": "event-sent"}
    tools.send_event.assert_awaited_once_with("working", "thought", None)

    assert await wrapped["thenvoi_add_contact"].ainvoke(
        {"handle": "@bob", "message": "hi"}
    ) == {"id": "contact-1"}
    tools.add_contact.assert_awaited_once_with("@bob", "hi")

    assert await wrapped["thenvoi_store_memory"].ainvoke(
        {
            "content": "prefers concise answers",
            "system": "thenvoi",
            "type": "preference",
            "segment": "user",
            "thought": "user stated preference",
        }
    ) == {"id": "memory-1"}
    tools.store_memory.assert_awaited_once()


@pytest.mark.asyncio
async def test_wrapper_errors_are_returned_to_model() -> None:
    tools = make_tools()
    tools.send_message.side_effect = RuntimeError("platform unavailable")
    wrapped = by_name(agent_tools_to_langchain(tools))

    result = await wrapped["thenvoi_send_message"].ainvoke(
        {"content": "hello", "mentions": []}
    )

    assert result == "Error sending message: platform unavailable"
