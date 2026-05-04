"""Tests for HUB_ROOM auto-enable contact tools.

When ContactEventStrategy.HUB_ROOM is active, the runtime auto-enables
contact-management tool schemas for the hub-room execution path,
regardless of the adapter's include_contacts preference. This is required
because the hub-room system prompt instructs the LLM to call contact
tools — those calls would fail if the schemas were not exposed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from thenvoi.runtime.tools import (
    AgentTools,
    CONTACT_TOOL_NAMES,
    iter_tool_definitions,
)


@pytest.fixture
def mock_rest():
    return MagicMock()


class TestIterToolDefinitionsContactsFlag:
    def test_contacts_included_by_default(self) -> None:
        defs = iter_tool_definitions()
        names = {d.name for d in defs}
        assert CONTACT_TOOL_NAMES.issubset(names)

    def test_contacts_excluded_when_flag_false(self) -> None:
        defs = iter_tool_definitions(include_contacts=False)
        names = {d.name for d in defs}
        assert names.isdisjoint(CONTACT_TOOL_NAMES)

    def test_memory_and_contacts_independent(self) -> None:
        defs = iter_tool_definitions(include_memory=True, include_contacts=False)
        names = {d.name for d in defs}
        assert names.isdisjoint(CONTACT_TOOL_NAMES)
        # Memory tools present
        from thenvoi.runtime.tools import MEMORY_TOOL_NAMES

        assert MEMORY_TOOL_NAMES.issubset(names)


class TestAgentToolsHubRoomAutoEnable:
    def test_is_hub_room_false_without_hub_id(self, mock_rest) -> None:
        tools = AgentTools("room-A", mock_rest)
        assert tools.is_hub_room is False

    def test_is_hub_room_false_when_room_does_not_match(self, mock_rest) -> None:
        tools = AgentTools("room-A", mock_rest, hub_room_id="hub-room-id")
        assert tools.is_hub_room is False

    def test_is_hub_room_true_when_room_matches(self, mock_rest) -> None:
        tools = AgentTools("hub-room-id", mock_rest, hub_room_id="hub-room-id")
        assert tools.is_hub_room is True

    def test_non_hub_room_respects_include_contacts_false(self, mock_rest) -> None:
        """Adapters that gate contacts get them excluded in non-hub rooms."""
        tools = AgentTools("room-A", mock_rest, hub_room_id="hub-room-id")
        schemas = tools.get_anthropic_tool_schemas(include_contacts=False)
        names = {s["name"] for s in schemas}
        assert names.isdisjoint(CONTACT_TOOL_NAMES)

    def test_hub_room_force_includes_contacts_even_when_disabled(
        self, mock_rest
    ) -> None:
        """Hub room ignores include_contacts=False and exposes contact tools anyway."""
        tools = AgentTools("hub-room-id", mock_rest, hub_room_id="hub-room-id")
        schemas = tools.get_anthropic_tool_schemas(include_contacts=False)
        names = {s["name"] for s in schemas}
        assert CONTACT_TOOL_NAMES.issubset(names), (
            "Hub room must auto-enable contact tools even when adapter "
            "passes include_contacts=False"
        )

    def test_hub_room_includes_contacts_in_openai_format(self, mock_rest) -> None:
        tools = AgentTools("hub-room-id", mock_rest, hub_room_id="hub-room-id")
        schemas = tools.get_openai_tool_schemas(include_contacts=False)
        names = {s["function"]["name"] for s in schemas}
        assert CONTACT_TOOL_NAMES.issubset(names)

    def test_default_include_contacts_true_in_normal_room(self, mock_rest) -> None:
        """Backward compat: contact tools included by default outside hub room."""
        tools = AgentTools("room-A", mock_rest)
        schemas = tools.get_anthropic_tool_schemas()
        names = {s["name"] for s in schemas}
        assert CONTACT_TOOL_NAMES.issubset(names)


class TestRuntimeHubRoomWiring:
    def test_set_hub_room_id_propagates_to_new_executions(self) -> None:
        """AgentRuntime.set_hub_room_id is forwarded to ExecutionContext."""
        from thenvoi.runtime.execution import ExecutionContext
        from thenvoi.runtime.runtime import AgentRuntime

        link = MagicMock()
        link.rest = MagicMock()
        on_execute = AsyncMock()
        runtime = AgentRuntime(link=link, agent_id="agent-1", on_execute=on_execute)

        runtime.set_hub_room_id("hub-room-xyz")
        assert runtime._hub_room_id == "hub-room-xyz"

        # Build an ExecutionContext the way _create_execution does
        ctx = ExecutionContext(
            room_id="hub-room-xyz",
            link=link,
            on_execute=on_execute,
            hub_room_id=runtime._hub_room_id,
        )
        assert ctx.hub_room_id == "hub-room-xyz"

        # AgentTools.from_context picks it up
        tools = AgentTools.from_context(ctx)
        assert tools.is_hub_room is True
