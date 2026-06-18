"""Tests for LangGraph platform tool conversion."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.core.types import AdapterFeatures, Capability
from band.integrations.langgraph.langchain_tools import agent_tools_to_langchain
from band.runtime.tools import get_band_tool_category, iter_tool_definitions


def _mock_agent_tools() -> MagicMock:
    tools = MagicMock()
    tools.execute_tool_call = AsyncMock(return_value={"ok": True})
    return tools


def _tool_names(tools: list) -> set[str]:
    return {tool.name for tool in tools}


class TestLangGraphToolFilters:
    def test_include_tools_filters_structured_tool_surface(self) -> None:
        tools = agent_tools_to_langchain(
            _mock_agent_tools(),
            features=AdapterFeatures(include_tools=("band_send_message",)),
        )

        assert _tool_names(tools) == {"band_send_message"}

    def test_exclude_tools_filters_structured_tool_surface(self) -> None:
        tools = agent_tools_to_langchain(
            _mock_agent_tools(),
            features=AdapterFeatures(exclude_tools=("band_send_event",)),
        )

        names = _tool_names(tools)
        assert "band_send_message" in names
        assert "band_send_event" not in names

    def test_include_contacts_category_filters_structured_tool_surface(self) -> None:
        tools = agent_tools_to_langchain(
            _mock_agent_tools(),
            features=AdapterFeatures(
                capabilities=frozenset({Capability.CONTACTS, Capability.MEMORY}),
                include_categories=("contacts",),
            ),
        )

        names = _tool_names(tools)
        assert "band_list_contacts" in names
        assert "band_add_contact" in names
        assert "band_send_message" not in names
        assert "band_list_memories" not in names

    def test_include_memory_category_filters_structured_tool_surface(self) -> None:
        tools = agent_tools_to_langchain(
            _mock_agent_tools(),
            features=AdapterFeatures(
                capabilities=frozenset({Capability.CONTACTS, Capability.MEMORY}),
                include_categories=("memory",),
            ),
        )

        names = _tool_names(tools)
        assert "band_list_memories" in names
        assert "band_store_memory" in names
        assert "band_send_message" not in names
        assert "band_list_contacts" not in names

    def test_include_chat_category_filters_structured_tool_surface(self) -> None:
        tools = agent_tools_to_langchain(
            _mock_agent_tools(),
            features=AdapterFeatures(
                capabilities=frozenset({Capability.CONTACTS, Capability.MEMORY}),
                include_categories=("chat",),
            ),
        )

        names = _tool_names(tools)
        assert "band_send_message" in names
        assert "band_send_event" in names
        assert "band_list_contacts" not in names
        assert "band_list_memories" not in names

    def test_every_agent_tool_has_shared_category(self) -> None:
        missing = [
            definition.name
            for definition in iter_tool_definitions(
                include_memory=True,
                include_contacts=True,
            )
            if get_band_tool_category(definition.name) is None
        ]

        assert missing == []


class TestLangGraphSendMessageTool:
    def _send_message_tool(self):
        tools = agent_tools_to_langchain(_mock_agent_tools())
        return next(tool for tool in tools if tool.name == "band_send_message")

    def test_send_message_schema_requires_mentions(self) -> None:
        send_message = self._send_message_tool()

        schema = send_message.args_schema.model_json_schema()
        assert "mentions" in schema["required"]

    @pytest.mark.asyncio
    async def test_valid_mentions_dispatch_to_agent_tools_boundary(self) -> None:
        agent_tools = _mock_agent_tools()
        send_message = next(
            tool
            for tool in agent_tools_to_langchain(agent_tools)
            if tool.name == "band_send_message"
        )

        result = await send_message.ainvoke(
            {"content": "hello", "mentions": ["00000000-0000-0000-0000-000000000001"]}
        )

        assert result == {"ok": True}
        agent_tools.execute_tool_call.assert_awaited_once_with(
            "band_send_message",
            {"content": "hello", "mentions": ["00000000-0000-0000-0000-000000000001"]},
        )
