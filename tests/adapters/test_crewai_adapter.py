"""Tests for CrewAIAdapter.

Tests for shared adapter behavior (initialization defaults, custom kwargs,
history_converter, on_started agent_name/description, on_message callable,
cleanup safety) live in tests/framework_conformance/test_adapter_conformance.py.
This file contains CrewAI-specific behavior: CrewAI agent creation, role/goal/backstory,
platform tools, tool execution, verbose mode, delegation, and custom tools.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel, Field

from thenvoi.core.types import AdapterFeatures, Capability, PlatformMessage

if TYPE_CHECKING:
    from thenvoi.adapters.crewai import CrewAIAdapter as CrewAIAdapterType


class MockBaseTool:
    name: str = ""
    description: str = ""

    def __init__(self):
        pass


@pytest.fixture
def crewai_mocks(monkeypatch):
    import sys

    mock_crewai_module = MagicMock()
    mock_crewai_tools_module = MagicMock()
    mock_nest_asyncio = MagicMock()

    mock_crewai_module.Agent = MagicMock()
    mock_crewai_module.LLM = MagicMock()
    mock_crewai_tools_module.BaseTool = MockBaseTool

    # Evict any cached integration modules so they pick up the freshly-mocked
    # `nest_asyncio`/`crewai.tools` from sys.modules on next import.
    for mod in (
        "thenvoi.adapters.crewai",
        "thenvoi.integrations.crewai",
        "thenvoi.integrations.crewai.runtime",
        "thenvoi.integrations.crewai.tools",
    ):
        sys.modules.pop(mod, None)

    monkeypatch.setitem(sys.modules, "crewai", mock_crewai_module)
    monkeypatch.setitem(sys.modules, "crewai.tools", mock_crewai_tools_module)
    monkeypatch.setitem(sys.modules, "nest_asyncio", mock_nest_asyncio)

    try:
        yield mock_crewai_module
    finally:
        # Clean up adapter + integration modules to force reimport on next test
        for mod in (
            "thenvoi.adapters.crewai",
            "thenvoi.integrations.crewai",
            "thenvoi.integrations.crewai.runtime",
            "thenvoi.integrations.crewai.tools",
        ):
            sys.modules.pop(mod, None)


@pytest.fixture
def CrewAIAdapter(crewai_mocks) -> type["CrewAIAdapterType"]:
    import importlib

    module = importlib.import_module("thenvoi.adapters.crewai")
    return module.CrewAIAdapter


@pytest.fixture
def sample_message():
    return PlatformMessage(
        id="msg-123",
        room_id="room-123",
        content="Hello, agent!",
        sender_id="user-456",
        sender_type="User",
        sender_name="Alice",
        message_type="text",
        metadata={},
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def mock_tools():
    """Create mock AgentToolsProtocol (MagicMock base, AsyncMock methods)."""
    tools = MagicMock()
    tools.get_tool_schemas = MagicMock(return_value=[])
    tools.get_openai_tool_schemas = MagicMock(return_value=[])
    tools.send_message = AsyncMock(return_value={"status": "sent"})
    tools.send_event = AsyncMock(return_value={"status": "sent"})
    tools.execute_tool_call = AsyncMock(return_value={"status": "success"})
    tools.add_participant = AsyncMock(
        return_value={"id": "123", "name": "Test", "status": "added"}
    )
    tools.remove_participant = AsyncMock(
        return_value={"id": "123", "name": "Test", "status": "removed"}
    )
    tools.get_participants = AsyncMock(
        return_value=[{"id": "123", "name": "Alice", "type": "User"}]
    )
    tools.lookup_peers = AsyncMock(
        return_value={
            "peers": [],
            "metadata": {
                "page": 1,
                "page_size": 50,
                "total_count": 0,
                "total_pages": 1,
            },
        }
    )
    tools.create_chatroom = AsyncMock(return_value="new-room-123")
    tools.list_contacts = AsyncMock(
        return_value={
            "contacts": [{"id": "contact-1", "handle": "@alice", "name": "Alice"}],
            "metadata": {"page": 1, "page_size": 50, "total_count": 1},
        }
    )
    tools.add_contact = AsyncMock(
        return_value={"id": "request-1", "handle": "@alice", "status": "pending"}
    )
    tools.remove_contact = AsyncMock(
        return_value={"id": "contact-1", "handle": "@alice", "status": "removed"}
    )
    tools.list_contact_requests = AsyncMock(
        return_value={
            "received": [{"id": "request-1", "from_handle": "@alice"}],
            "sent": [],
            "metadata": {"page": 1, "page_size": 50, "total_count": 1},
        }
    )
    tools.respond_contact_request = AsyncMock(
        return_value={"id": "request-1", "status": "approved"}
    )
    tools.list_memories = AsyncMock(
        return_value={
            "memories": [{"id": "memory-1", "content": "remember this"}],
            "count": 1,
        }
    )
    tools.store_memory = AsyncMock(return_value={"id": "memory-1", "status": "stored"})
    tools.get_memory = AsyncMock(
        return_value={"id": "memory-1", "content": "remember this"}
    )
    tools.supersede_memory = AsyncMock(
        return_value={"id": "memory-1", "status": "superseded"}
    )
    tools.archive_memory = AsyncMock(
        return_value={"id": "memory-1", "status": "archived"}
    )
    return tools


@pytest.fixture
def mock_crewai_agent():
    mock_result = MagicMock()
    mock_result.raw = "Hello! I'm here to help."

    mock_agent = MagicMock()
    mock_agent.kickoff_async = AsyncMock(return_value=mock_result)
    return mock_agent


@pytest.fixture
def room_context(crewai_mocks, mock_tools):
    """Context manager fixture for setting up room context in tests.

    Usage:
        with room_context("room-123"):
            # tool execution code here
    """
    import contextlib
    import importlib

    module = importlib.import_module("thenvoi.adapters.crewai")

    @contextlib.contextmanager
    def _room_context(room_id: str = "room-123"):
        module._current_room_context.set((room_id, mock_tools))
        try:
            yield
        finally:
            module._current_room_context.set(None)

    return _room_context


class TestCrewAISpecificInitialization:
    """CrewAI-specific initialization tests (shared init tests live in conformance)."""

    def test_system_prompt_deprecation_warning(self, CrewAIAdapter):
        """system_prompt parameter should emit DeprecationWarning."""
        import warnings

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            adapter = CrewAIAdapter(system_prompt="Old style prompt")

            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "system_prompt" in str(w[0].message)
            assert "backstory" in str(w[0].message)
            # system_prompt should be used as backstory when backstory not provided
            assert adapter.backstory == "Old style prompt"

    def test_system_prompt_does_not_override_backstory(self, CrewAIAdapter):
        """If both system_prompt and backstory are provided, backstory takes precedence."""
        import warnings

        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            adapter = CrewAIAdapter(
                system_prompt="Old style prompt",
                backstory="New style backstory",
            )
            # backstory should not be overwritten
            assert adapter.backstory == "New style backstory"


class TestOnStarted:
    @pytest.mark.asyncio
    async def test_creates_crewai_agent(self, CrewAIAdapter, crewai_mocks):
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")

        crewai_mocks.Agent.assert_called_once()

    @pytest.mark.asyncio
    async def test_uses_custom_role_goal_backstory(self, CrewAIAdapter, crewai_mocks):
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter(
            role="Research Analyst",
            goal="Find information",
            backstory="Expert researcher",
        )

        await adapter.on_started(agent_name="TestBot", agent_description="")

        call_kwargs = crewai_mocks.Agent.call_args[1]
        assert call_kwargs["role"] == "Research Analyst"
        assert call_kwargs["goal"] == "Find information"
        assert "Expert researcher" in call_kwargs["backstory"]

    @pytest.mark.asyncio
    async def test_uses_agent_name_as_default_role(self, CrewAIAdapter, crewai_mocks):
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")

        call_kwargs = crewai_mocks.Agent.call_args[1]
        assert call_kwargs["role"] == "TestBot"

    @pytest.mark.asyncio
    async def test_creates_platform_tools(self, CrewAIAdapter, crewai_mocks):
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]

        # Check for required platform tools (don't check exact count to avoid brittleness)
        tool_names = [t.name for t in tools]
        required_tools = [
            "thenvoi_send_message",
            "thenvoi_send_event",
            "thenvoi_add_participant",
            "thenvoi_remove_participant",
            "thenvoi_get_participants",
            "thenvoi_lookup_peers",
            "thenvoi_create_chatroom",
        ]
        for tool_name in required_tools:
            assert tool_name in tool_names, f"Missing required tool: {tool_name}"

    @pytest.mark.asyncio
    async def test_includes_platform_instructions_in_backstory(
        self, CrewAIAdapter, crewai_mocks
    ):
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")

        call_kwargs = crewai_mocks.Agent.call_args[1]
        backstory = call_kwargs["backstory"]

        assert "Multi-participant chat" in backstory
        assert "thenvoi_send_message" in backstory
        assert "thenvoi_lookup_peers" in backstory


class TestOnMessage:
    @pytest.mark.asyncio
    async def test_initializes_history_on_bootstrap(
        self, CrewAIAdapter, sample_message, mock_tools, mock_crewai_agent
    ):
        adapter = CrewAIAdapter()
        await adapter.on_started("TestBot", "Test bot")
        adapter._crewai_agent = mock_crewai_agent

        await adapter.on_message(
            msg=sample_message,
            tools=mock_tools,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        assert "room-123" in adapter._message_history

    @pytest.mark.asyncio
    async def test_loads_existing_history(
        self, CrewAIAdapter, sample_message, mock_tools, mock_crewai_agent
    ):
        adapter = CrewAIAdapter()
        await adapter.on_started("TestBot", "Test bot")
        adapter._crewai_agent = mock_crewai_agent

        existing_history = [
            {"role": "user", "content": "[Bob]: Previous message"},
            {"role": "assistant", "content": "Previous response"},
        ]

        await adapter.on_message(
            msg=sample_message,
            tools=mock_tools,
            history=existing_history,
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        assert len(adapter._message_history["room-123"]) >= 3

    @pytest.mark.asyncio
    async def test_calls_kickoff_async(
        self, CrewAIAdapter, sample_message, mock_tools, mock_crewai_agent
    ):
        adapter = CrewAIAdapter()
        await adapter.on_started("TestBot", "Test bot")
        adapter._crewai_agent = mock_crewai_agent

        await adapter.on_message(
            msg=sample_message,
            tools=mock_tools,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        mock_crewai_agent.kickoff_async.assert_called_once()


class TestOnCleanup:
    @pytest.mark.asyncio
    async def test_cleans_up_room_history(self, CrewAIAdapter, mock_crewai_agent):
        adapter = CrewAIAdapter()
        await adapter.on_started("TestBot", "Test bot")

        adapter._message_history["room-123"] = [{"role": "user", "content": "test"}]
        assert "room-123" in adapter._message_history

        await adapter.on_cleanup("room-123")

        assert "room-123" not in adapter._message_history


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_reports_error_on_kickoff_failure(
        self, CrewAIAdapter, sample_message, mock_tools, mock_crewai_agent
    ):
        mock_crewai_agent.kickoff_async.side_effect = Exception("Agent Error")

        adapter = CrewAIAdapter()
        await adapter.on_started("TestBot", "Test bot")
        adapter._crewai_agent = mock_crewai_agent

        with pytest.raises(Exception, match="Agent Error"):
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

        mock_tools.send_event.assert_called()

    @pytest.mark.asyncio
    async def test_raises_error_when_agent_not_initialized(
        self, CrewAIAdapter, sample_message, mock_tools
    ):
        """on_message raises RuntimeError if on_started was not called."""
        adapter = CrewAIAdapter()
        # Don't call on_started - agent remains uninitialized

        with pytest.raises(RuntimeError, match="CrewAI agent not initialized"):
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )


class TestVerboseMode:
    @pytest.mark.asyncio
    async def test_verbose_mode_passed_to_agent(self, CrewAIAdapter, crewai_mocks):
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter(verbose=True)
        await adapter.on_started("TestBot", "Test bot")

        call_kwargs = crewai_mocks.Agent.call_args[1]
        assert call_kwargs["verbose"] is True


class TestMaxRpm:
    @pytest.mark.asyncio
    async def test_max_rpm_passed_to_agent(self, CrewAIAdapter, crewai_mocks):
        """max_rpm parameter should be passed to CrewAI Agent."""
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter(max_rpm=10)
        await adapter.on_started("TestBot", "Test bot")

        call_kwargs = crewai_mocks.Agent.call_args[1]
        assert call_kwargs["max_rpm"] == 10

    @pytest.mark.asyncio
    async def test_max_rpm_defaults_to_none(self, CrewAIAdapter, crewai_mocks):
        """max_rpm should default to None (no rate limiting)."""
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        await adapter.on_started("TestBot", "Test bot")

        call_kwargs = crewai_mocks.Agent.call_args[1]
        assert call_kwargs["max_rpm"] is None

    def test_max_rpm_stored_on_adapter(self, CrewAIAdapter):
        """max_rpm should be stored on the adapter instance."""
        adapter = CrewAIAdapter(max_rpm=60)
        assert adapter.max_rpm == 60


class TestAllowDelegation:
    @pytest.mark.asyncio
    async def test_allow_delegation_passed_to_agent(self, CrewAIAdapter, crewai_mocks):
        """allow_delegation parameter should be passed to CrewAI Agent."""
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter(allow_delegation=True)
        await adapter.on_started("TestBot", "Test bot")

        call_kwargs = crewai_mocks.Agent.call_args[1]
        assert call_kwargs["allow_delegation"] is True

    @pytest.mark.asyncio
    async def test_allow_delegation_defaults_to_false(
        self, CrewAIAdapter, crewai_mocks
    ):
        """allow_delegation should default to False."""
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        await adapter.on_started("TestBot", "Test bot")

        call_kwargs = crewai_mocks.Agent.call_args[1]
        assert call_kwargs["allow_delegation"] is False

    def test_allow_delegation_stored_on_adapter(self, CrewAIAdapter):
        """allow_delegation should be stored on the adapter instance."""
        adapter = CrewAIAdapter(allow_delegation=True)
        assert adapter.allow_delegation is True


class TestParticipantsUpdate:
    @pytest.mark.asyncio
    async def test_includes_participants_update_in_message(
        self, CrewAIAdapter, sample_message, mock_tools, mock_crewai_agent
    ):
        adapter = CrewAIAdapter()
        await adapter.on_started("TestBot", "Test bot")
        adapter._crewai_agent = mock_crewai_agent

        await adapter.on_message(
            msg=sample_message,
            tools=mock_tools,
            history=[],
            participants_msg="Alice joined the room",
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        call_args = mock_crewai_agent.kickoff_async.call_args
        messages = call_args[0][0]

        found = any("Alice joined" in str(m.get("content", "")) for m in messages)
        assert found


class TestContactsUpdate:
    @pytest.mark.asyncio
    async def test_includes_contacts_update_in_message(
        self, CrewAIAdapter, sample_message, mock_tools, mock_crewai_agent
    ):
        adapter = CrewAIAdapter()
        await adapter.on_started("TestBot", "Test bot")
        adapter._crewai_agent = mock_crewai_agent

        await adapter.on_message(
            msg=sample_message,
            tools=mock_tools,
            history=[],
            participants_msg=None,
            contacts_msg="[Contacts]: @alice is now a contact",
            is_session_bootstrap=True,
            room_id="room-123",
        )

        call_args = mock_crewai_agent.kickoff_async.call_args
        messages = call_args[0][0]

        found = any(
            "@alice is now a contact" in str(m.get("content", "")) for m in messages
        )
        assert found


class TestContactAndMemoryToolRegistration:
    @pytest.mark.asyncio
    async def test_contact_tools_are_excluded_by_default(
        self, CrewAIAdapter, crewai_mocks
    ):
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        await adapter.on_started("TestBot", "Test bot")

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        tool_names = {tool.name for tool in tools}

        assert "thenvoi_list_contacts" not in tool_names
        assert "thenvoi_add_contact" not in tool_names
        assert "thenvoi_remove_contact" not in tool_names
        assert "thenvoi_list_contact_requests" not in tool_names
        assert "thenvoi_respond_contact_request" not in tool_names

    @pytest.mark.asyncio
    async def test_contact_tools_are_included_when_enabled(
        self, CrewAIAdapter, crewai_mocks
    ):
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter(
            features=AdapterFeatures(capabilities={Capability.CONTACTS}),
        )
        await adapter.on_started("TestBot", "Test bot")

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        tool_names = {tool.name for tool in tools}

        assert "thenvoi_list_contacts" in tool_names
        assert "thenvoi_add_contact" in tool_names
        assert "thenvoi_remove_contact" in tool_names
        assert "thenvoi_list_contact_requests" in tool_names
        assert "thenvoi_respond_contact_request" in tool_names

    @pytest.mark.asyncio
    async def test_memory_tools_are_excluded_by_default(
        self, CrewAIAdapter, crewai_mocks
    ):
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        await adapter.on_started("TestBot", "Test bot")

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        tool_names = {tool.name for tool in tools}

        assert "thenvoi_list_memories" not in tool_names
        assert "thenvoi_store_memory" not in tool_names
        assert "thenvoi_get_memory" not in tool_names
        assert "thenvoi_supersede_memory" not in tool_names
        assert "thenvoi_archive_memory" not in tool_names

    @pytest.mark.asyncio
    async def test_memory_tools_are_included_when_enabled(
        self, CrewAIAdapter, crewai_mocks
    ):
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter(enable_memory_tools=True)
        await adapter.on_started("TestBot", "Test bot")

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        tool_names = {tool.name for tool in tools}

        assert "thenvoi_list_memories" in tool_names
        assert "thenvoi_store_memory" in tool_names
        assert "thenvoi_get_memory" in tool_names
        assert "thenvoi_supersede_memory" in tool_names
        assert "thenvoi_archive_memory" in tool_names


class TestCacheDisabling:
    """Regression tests for CrewAI CacheHandler bypass.

    CrewAI's CacheHandler caches by (tool_name, input_string) globally — not
    per-room.  Since room_id lives in a ContextVar, the same tool+input across
    two rooms would return stale cached results.  The fix sets
    ``cache_function = lambda *a, **kw: False`` on every tool so the handler
    never caches.
    """

    @pytest.mark.asyncio
    async def test_all_crewai_platform_tools_disable_cache(
        self, CrewAIAdapter, crewai_mocks
    ):
        """Every thenvoi_* platform tool must have cache_function returning False."""
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter(
            features=AdapterFeatures(
                capabilities={Capability.CONTACTS, Capability.MEMORY}
            ),
        )
        await adapter.on_started("TestBot", "Test bot")

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        platform_tools = [t for t in tools if t.name.startswith("thenvoi_")]

        assert len(platform_tools) > 0, "Expected at least one thenvoi_* tool"

        for tool in platform_tools:
            assert callable(tool.cache_function), (
                f"Tool {tool.name}: cache_function is not callable"
            )
            assert tool.cache_function({"arg": "val"}, "result") is False, (
                f"Tool {tool.name}: cache_function should return False"
            )

    @pytest.mark.asyncio
    async def test_custom_crewai_tools_disable_cache(self, CrewAIAdapter, crewai_mocks):
        """Custom tools passed via additional_tools must also disable cache."""
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter(
            additional_tools=[(EchoInput, echo_message)],
        )
        await adapter.on_started("TestBot", "Test bot")

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        echo_tool = next((t for t in tools if t.name == "echo"), None)
        assert echo_tool is not None, "Expected 'echo' tool in tool list"

        assert callable(echo_tool.cache_function), (
            "Custom tool cache_function is not callable"
        )
        assert echo_tool.cache_function({"message": "hi"}, "Echo: hi") is False, (
            "Custom tool cache_function should return False"
        )


class TestContactToolExecution:
    def _make_adapter(self, CrewAIAdapter: type) -> Any:
        return CrewAIAdapter(
            features=AdapterFeatures(capabilities={Capability.CONTACTS}),
        )

    def test_list_contacts_tool_executes(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):
        import asyncio

        adapter = self._make_adapter(CrewAIAdapter)
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        list_contacts_tool = next(t for t in tools if t.name == "thenvoi_list_contacts")

        with room_context("room-123"):
            result = list_contacts_tool._run(page=2, page_size=25)

        result_data = json.loads(result)
        assert result_data["status"] == "success"
        assert result_data["contacts"][0]["handle"] == "@alice"
        mock_tools.list_contacts.assert_awaited_once_with(2, 25)

    def test_add_contact_tool_executes(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):
        import asyncio

        adapter = self._make_adapter(CrewAIAdapter)
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        add_contact_tool = next(t for t in tools if t.name == "thenvoi_add_contact")

        with room_context("room-123"):
            result = add_contact_tool._run(handle="@alice", message="Hi Alice")

        result_data = json.loads(result)
        assert result_data["status"] == "success"
        assert result_data["result_status"] == "pending"
        assert result_data["handle"] == "@alice"
        mock_tools.add_contact.assert_awaited_once_with("@alice", "Hi Alice")

    def test_remove_contact_tool_executes(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):
        import asyncio

        adapter = self._make_adapter(CrewAIAdapter)
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        remove_contact_tool = next(
            t for t in tools if t.name == "thenvoi_remove_contact"
        )

        with room_context("room-123"):
            result = remove_contact_tool._run(handle="@alice", contact_id="contact-1")

        result_data = json.loads(result)
        assert result_data["status"] == "success"
        assert result_data["result_status"] == "removed"
        mock_tools.remove_contact.assert_awaited_once_with("@alice", "contact-1")

    def test_list_contact_requests_tool_executes(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):
        import asyncio

        adapter = self._make_adapter(CrewAIAdapter)
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        list_requests_tool = next(
            t for t in tools if t.name == "thenvoi_list_contact_requests"
        )

        with room_context("room-123"):
            result = list_requests_tool._run(
                page=3, page_size=10, sent_status="approved"
            )

        result_data = json.loads(result)
        assert result_data["status"] == "success"
        assert result_data["received"][0]["id"] == "request-1"
        mock_tools.list_contact_requests.assert_awaited_once_with(3, 10, "approved")

    def test_respond_contact_request_tool_executes(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):
        import asyncio

        adapter = self._make_adapter(CrewAIAdapter)
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        respond_request_tool = next(
            t for t in tools if t.name == "thenvoi_respond_contact_request"
        )

        with room_context("room-123"):
            result = respond_request_tool._run(
                action="approve",
                handle="@alice",
                request_id="request-1",
            )

        result_data = json.loads(result)
        assert result_data["status"] == "success"
        assert result_data["result_status"] == "approved"
        assert result_data["id"] == "request-1"
        mock_tools.respond_contact_request.assert_awaited_once_with(
            "approve",
            "@alice",
            "request-1",
        )


class TestMemoryToolExecution:
    def test_list_memories_tool_executes(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):
        import asyncio

        adapter = CrewAIAdapter(enable_memory_tools=True)
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        list_memories_tool = next(t for t in tools if t.name == "thenvoi_list_memories")

        with room_context("room-123"):
            result = list_memories_tool._run(
                subject_id="subject-1",
                scope="subject",
                system="working",
                memory_type="fact",
                segment="user",
                content_query="remember",
                page_size=5,
                status="active",
            )

        result_data = json.loads(result)
        assert result_data["status"] == "success"
        assert result_data["memories"][0]["id"] == "memory-1"
        mock_tools.list_memories.assert_awaited_once_with(
            subject_id="subject-1",
            scope="subject",
            system="working",
            type="fact",
            segment="user",
            content_query="remember",
            page_size=5,
            status="active",
        )

    def test_store_memory_tool_executes(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):
        import asyncio

        adapter = CrewAIAdapter(enable_memory_tools=True)
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        store_memory_tool = next(t for t in tools if t.name == "thenvoi_store_memory")

        with room_context("room-123"):
            result = store_memory_tool._run(
                content="remember this",
                system="working",
                memory_type="fact",
                segment="user",
                thought="important for follow-up",
                scope="subject",
                subject_id="subject-1",
            )

        result_data = json.loads(result)
        assert result_data["status"] == "success"
        assert result_data["result_status"] == "stored"
        assert result_data["id"] == "memory-1"
        mock_tools.store_memory.assert_awaited_once_with(
            content="remember this",
            system="working",
            type="fact",
            segment="user",
            thought="important for follow-up",
            scope="subject",
            subject_id="subject-1",
        )

    def test_get_memory_tool_executes(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):
        import asyncio

        adapter = CrewAIAdapter(enable_memory_tools=True)
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        get_memory_tool = next(t for t in tools if t.name == "thenvoi_get_memory")

        with room_context("room-123"):
            result = get_memory_tool._run(memory_id="memory-1")

        result_data = json.loads(result)
        assert result_data["status"] == "success"
        assert result_data["id"] == "memory-1"
        mock_tools.get_memory.assert_awaited_once_with("memory-1")

    def test_supersede_memory_tool_executes(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):
        import asyncio

        adapter = CrewAIAdapter(enable_memory_tools=True)
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        supersede_memory_tool = next(
            t for t in tools if t.name == "thenvoi_supersede_memory"
        )

        with room_context("room-123"):
            result = supersede_memory_tool._run(memory_id="memory-1")

        result_data = json.loads(result)
        assert result_data["status"] == "success"
        assert result_data["result_status"] == "superseded"
        assert result_data["id"] == "memory-1"
        mock_tools.supersede_memory.assert_awaited_once_with("memory-1")

    def test_archive_memory_tool_executes(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):
        import asyncio

        adapter = CrewAIAdapter(enable_memory_tools=True)
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        archive_memory_tool = next(
            t for t in tools if t.name == "thenvoi_archive_memory"
        )

        with room_context("room-123"):
            result = archive_memory_tool._run(memory_id="memory-1")

        result_data = json.loads(result)
        assert result_data["status"] == "success"
        assert result_data["result_status"] == "archived"
        assert result_data["id"] == "memory-1"
        mock_tools.archive_memory.assert_awaited_once_with("memory-1")


class TestToolExecution:
    def test_tool_returns_error_without_room_context(self, CrewAIAdapter, crewai_mocks):
        """Tools return error when called outside message handling (no context set)."""
        import asyncio

        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]
        send_message_tool = next(t for t in tools if t.name == "thenvoi_send_message")

        # Call tool without setting context variable (simulates call outside message handling)
        result = send_message_tool._run(content="Hello!", mentions="[]")

        result_data = json.loads(result)
        assert result_data["status"] == "error"
        assert "No room context available" in result_data["message"]

    @pytest.mark.asyncio
    async def test_all_tools_have_correct_schemas(self, CrewAIAdapter, crewai_mocks):
        """Tools no longer require room_id - context is managed via context variable."""
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        await adapter.on_started("TestBot", "Test bot")

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]

        # thenvoi_send_message should have content and mentions, but NOT room_id
        send_message = next(t for t in tools if t.name == "thenvoi_send_message")
        assert send_message.args_schema is not None
        schema_fields = send_message.args_schema.model_fields
        assert "room_id" not in schema_fields
        assert "content" in schema_fields
        assert "mentions" in schema_fields

        # thenvoi_add_participant should have identifier and role, but NOT room_id
        add_participant = next(t for t in tools if t.name == "thenvoi_add_participant")
        schema_fields = add_participant.args_schema.model_fields
        assert "room_id" not in schema_fields
        assert "identifier" in schema_fields
        assert "role" in schema_fields

        # thenvoi_lookup_peers should expose pagination, but NOT room_id
        lookup_peers = next(t for t in tools if t.name == "thenvoi_lookup_peers")
        schema_fields = lookup_peers.args_schema.model_fields
        assert "room_id" not in schema_fields
        assert "page" in schema_fields
        assert "page_size" in schema_fields

    @pytest.mark.asyncio
    async def test_send_event_message_type_validation(
        self, CrewAIAdapter, crewai_mocks
    ):
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        await adapter.on_started("TestBot", "Test bot")

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]

        send_event = next(t for t in tools if t.name == "thenvoi_send_event")
        schema_fields = send_event.args_schema.model_fields

        assert "message_type" in schema_fields
        message_type_field = schema_fields["message_type"]
        assert message_type_field.default == "thought"

    def test_successful_tool_execution_with_room_context(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):
        """Tools work when context variable is set (simulates call during message handling)."""
        import asyncio

        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]
        get_participants_tool = next(
            t for t in tools if t.name == "thenvoi_get_participants"
        )

        with room_context("room-123"):
            result = get_participants_tool._run()

        result_data = json.loads(result)
        assert result_data["status"] == "success"
        assert "participants" in result_data
        assert result_data["count"] == 1

    def test_tool_execution_handles_exception(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        mock_tools.get_participants.side_effect = Exception("Connection failed")

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]
        get_participants_tool = next(
            t for t in tools if t.name == "thenvoi_get_participants"
        )

        with room_context("room-123"):
            result = get_participants_tool._run()

        result_data = json.loads(result)
        assert result_data["status"] == "error"
        assert "Connection failed" in result_data["message"]

    @pytest.mark.asyncio
    async def test_lookup_peers_uses_adapter_loop_when_tool_runs_in_worker_thread(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        await adapter.on_started("TestBot", "Test bot")

        expected_loop = asyncio.get_running_loop()

        async def lookup_peers(page: int, page_size: int) -> dict[str, object]:
            assert asyncio.get_running_loop() is expected_loop
            return {
                "peers": [],
                "metadata": {
                    "page": page,
                    "page_size": page_size,
                    "total_count": 0,
                    "total_pages": 1,
                },
            }

        mock_tools.lookup_peers = AsyncMock(side_effect=lookup_peers)

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]
        lookup_peers_tool = next(t for t in tools if t.name == "thenvoi_lookup_peers")

        with room_context("room-123"):
            result = await asyncio.to_thread(lookup_peers_tool._run)

        result_data = json.loads(result)
        assert result_data["status"] == "success"
        mock_tools.lookup_peers.assert_awaited_once_with(1, 50)


class TestExecutionReporting:
    @pytest.mark.asyncio
    async def test_execution_reporting_flag_stored(self, CrewAIAdapter, crewai_mocks):
        adapter_enabled = CrewAIAdapter(enable_execution_reporting=True)
        adapter_disabled = CrewAIAdapter(enable_execution_reporting=False)

        from thenvoi.core.types import Emit

        assert Emit.EXECUTION in adapter_enabled.features.emit
        assert Emit.EXECUTION not in adapter_disabled.features.emit

    def test_reports_tool_call_when_enabled(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):
        import asyncio

        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter(enable_execution_reporting=True)
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]
        send_message_tool = next(t for t in tools if t.name == "thenvoi_send_message")

        with room_context("room-123"):
            send_message_tool._run(content="Hello!", mentions="[]")

        assert mock_tools.send_event.call_count >= 2

    @pytest.mark.asyncio
    async def test_report_tool_call_403_does_not_crash(
        self, CrewAIAdapter, crewai_mocks, mock_tools
    ):
        """send_event 403 in EmitExecutionReporter.report_call should not propagate."""
        from thenvoi.integrations.crewai import EmitExecutionReporter

        adapter = CrewAIAdapter(enable_execution_reporting=True)
        reporter = EmitExecutionReporter(adapter.features)
        mock_tools.send_event.side_effect = Exception("403 Forbidden")

        # Should not raise
        await reporter.report_call(mock_tools, "search", {"q": "test"})

    @pytest.mark.asyncio
    async def test_report_tool_result_403_does_not_crash(
        self, CrewAIAdapter, crewai_mocks, mock_tools
    ):
        """send_event 403 in EmitExecutionReporter.report_result should not propagate."""
        from thenvoi.integrations.crewai import EmitExecutionReporter

        adapter = CrewAIAdapter(enable_execution_reporting=True)
        reporter = EmitExecutionReporter(adapter.features)
        mock_tools.send_event.side_effect = Exception("403 Forbidden")

        # Should not raise
        await reporter.report_result(mock_tools, "search", "some result")
        await reporter.report_result(mock_tools, "search", "some error", is_error=True)


class TestLazyNestAsyncio:
    def test_nest_asyncio_not_applied_on_import(self, crewai_mocks):
        import importlib
        import sys

        sys.modules.pop("thenvoi.adapters.crewai", None)
        sys.modules.pop("thenvoi.integrations.crewai", None)
        sys.modules.pop("thenvoi.integrations.crewai.runtime", None)

        crewai_mocks_nest = sys.modules["nest_asyncio"]
        crewai_mocks_nest.reset_mock()

        importlib.import_module("thenvoi.adapters.crewai")

        crewai_mocks_nest.apply.assert_not_called()

    def test_ensure_nest_asyncio_applies_once(self, CrewAIAdapter, crewai_mocks):
        import importlib
        import sys

        module = importlib.import_module("thenvoi.integrations.crewai.runtime")

        module._nest_asyncio_applied = False
        nest_mock = sys.modules["nest_asyncio"]
        nest_mock.reset_mock()

        module._ensure_nest_asyncio()
        module._ensure_nest_asyncio()

        assert nest_mock.apply.call_count == 1

    def test_nest_asyncio_lock_exists(self, CrewAIAdapter, crewai_mocks):
        """The integrations.crewai.runtime module owns the threading lock."""
        import importlib
        import threading

        module = importlib.import_module("thenvoi.integrations.crewai.runtime")

        assert hasattr(module, "_nest_asyncio_lock")
        assert isinstance(module._nest_asyncio_lock, type(threading.Lock()))

    def test_ensure_nest_asyncio_is_thread_safe(self, CrewAIAdapter, crewai_mocks):
        """Multiple threads calling _ensure_nest_asyncio should only apply patch once."""
        import concurrent.futures
        import importlib
        import sys

        module = importlib.import_module("thenvoi.integrations.crewai.runtime")

        module._nest_asyncio_applied = False
        nest_mock = sys.modules["nest_asyncio"]
        nest_mock.reset_mock()

        # Run multiple threads concurrently calling _ensure_nest_asyncio
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(module._ensure_nest_asyncio) for _ in range(10)]
            concurrent.futures.wait(futures)

        # Should only have been called once despite multiple concurrent threads
        assert nest_mock.apply.call_count == 1


class TestRunAsync:
    def test_run_async_with_running_loop(self, crewai_mocks):
        import importlib
        import sys

        module = importlib.import_module("thenvoi.integrations.crewai.runtime")
        module._nest_asyncio_applied = False

        nest_mock = sys.modules["nest_asyncio"]
        nest_mock.reset_mock()

        async def test_coro() -> str:
            return "result"

        result = module.run_async(test_coro())

        assert result == "result"
        nest_mock.apply.assert_called_once()

    def test_run_async_without_running_loop(self, crewai_mocks):
        import importlib
        import sys

        module = importlib.import_module("thenvoi.integrations.crewai.runtime")
        module._nest_asyncio_applied = True

        nest_mock = sys.modules["nest_asyncio"]
        nest_mock.reset_mock()

        async def test_coro() -> str:
            return "result"

        result = module.run_async(test_coro())

        assert result == "result"


class TestMentionsValidator:
    @pytest.mark.asyncio
    async def test_mentions_list_converted_to_json(self, CrewAIAdapter, crewai_mocks):
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        await adapter.on_started("TestBot", "Test bot")

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]
        send_message_tool = next(t for t in tools if t.name == "thenvoi_send_message")

        input_model = send_message_tool.args_schema

        instance = input_model(
            content="Hello!",
            mentions=["Alice", "Bob"],
        )

        assert instance.mentions == '["Alice", "Bob"]'

    @pytest.mark.asyncio
    async def test_mentions_string_kept_as_is(self, CrewAIAdapter, crewai_mocks):
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        await adapter.on_started("TestBot", "Test bot")

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]
        send_message_tool = next(t for t in tools if t.name == "thenvoi_send_message")

        input_model = send_message_tool.args_schema

        instance = input_model(
            content="Hello!",
            mentions='["Alice"]',
        )

        assert instance.mentions == '["Alice"]'

    @pytest.mark.asyncio
    async def test_mentions_none_converted_to_empty_array(
        self, CrewAIAdapter, crewai_mocks
    ):
        """None mentions should be normalized to empty JSON array string."""
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        await adapter.on_started("TestBot", "Test bot")

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]
        send_message_tool = next(t for t in tools if t.name == "thenvoi_send_message")

        input_model = send_message_tool.args_schema

        instance = input_model(
            content="Hello!",
            mentions=None,
        )

        assert instance.mentions == "[]"


class TestPromptRendering:
    def test_backstory_uses_render_system_prompt(self, CrewAIAdapter):
        """CrewAI backstory is now built via render_system_prompt."""
        from thenvoi.runtime.prompts import render_system_prompt

        prompt = render_system_prompt(
            agent_name="TestAgent",
            agent_description="A test agent",
        )
        # Verify the rendered prompt contains key sections
        assert "Environment" in prompt
        assert "thenvoi_send_message" in prompt
        assert "thenvoi_lookup_peers" in prompt


# Custom tool input models for testing


class EchoInput(BaseModel):
    """Echo back the provided message."""

    message: str = Field(description="Message to echo")


class CalculatorInput(BaseModel):
    """Perform math calculations."""

    operation: str = Field(description="add, subtract, multiply, divide")
    left: float = Field(description="Left operand")
    right: float = Field(description="Right operand")


async def echo_message(args: EchoInput) -> str:
    """Async echo tool."""
    return f"Echo: {args.message}"


def calculate(args: CalculatorInput) -> str:
    """Sync calculator tool."""
    ops = {
        "add": lambda a, b: a + b,
        "subtract": lambda a, b: a - b,
        "multiply": lambda a, b: a * b,
        "divide": lambda a, b: a / b,
    }
    return str(ops[args.operation](args.left, args.right))


async def failing_tool(args: EchoInput) -> str:
    """Tool that always fails."""
    raise ValueError("Service unavailable")


class TestCustomTools:
    def test_accepts_additional_tools_parameter(self, CrewAIAdapter):
        """Adapter should accept list of (Model, func) tuples."""
        adapter = CrewAIAdapter(
            additional_tools=[(EchoInput, echo_message)],
        )

        assert len(adapter._custom_tools) == 1
        assert adapter._custom_tools[0][0] is EchoInput

    def test_accepts_multiple_custom_tools(self, CrewAIAdapter):
        """Adapter should accept multiple custom tools."""
        adapter = CrewAIAdapter(
            additional_tools=[
                (EchoInput, echo_message),
                (CalculatorInput, calculate),
            ],
        )

        assert len(adapter._custom_tools) == 2

    @pytest.mark.asyncio
    async def test_custom_tools_converted_to_crewai_format(
        self, CrewAIAdapter, crewai_mocks
    ):
        """Custom tools should be converted to CrewAI BaseTool instances."""
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter(
            additional_tools=[(EchoInput, echo_message)],
        )
        await adapter.on_started("TestBot", "Test bot")

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]

        # Check that custom tool is included alongside platform tools
        tool_names = [t.name for t in tools]
        assert "thenvoi_send_message" in tool_names  # Platform tool should exist
        assert "echo" in tool_names  # Custom tool should exist

        # Find the echo tool
        echo_tool = next((t for t in tools if t.name == "echo"), None)
        assert echo_tool is not None
        assert echo_tool.description == "Echo back the provided message."
        assert echo_tool.args_schema is EchoInput

    @pytest.mark.asyncio
    async def test_multiple_custom_tools_in_agent(self, CrewAIAdapter, crewai_mocks):
        """Multiple custom tools should all be available to the agent."""
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter(
            additional_tools=[
                (EchoInput, echo_message),
                (CalculatorInput, calculate),
            ],
        )
        await adapter.on_started("TestBot", "Test bot")

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]

        # Check that both custom tools are included alongside platform tools
        tool_names = [t.name for t in tools]
        assert "thenvoi_send_message" in tool_names  # Platform tool should exist
        assert "echo" in tool_names  # Custom tool should exist
        assert "calculator" in tool_names  # Custom tool should exist

    def test_custom_tool_execution_async(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):
        """Async custom tool should execute correctly."""
        import asyncio

        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter(
            additional_tools=[(EchoInput, echo_message)],
        )
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]
        echo_tool = next(t for t in tools if t.name == "echo")

        with room_context("room-123"):
            result = echo_tool._run(message="Hello world")

        result_data = json.loads(result)
        assert result_data["status"] == "success"
        assert "Echo: Hello world" in result_data["result"]

    def test_custom_tool_execution_sync(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):
        """Sync custom tool should execute correctly."""
        import asyncio

        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter(
            additional_tools=[(CalculatorInput, calculate)],
        )
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]
        calc_tool = next(t for t in tools if t.name == "calculator")

        with room_context("room-123"):
            result = calc_tool._run(operation="add", left=5.0, right=3.0)

        result_data = json.loads(result)
        assert result_data["status"] == "success"
        assert "8.0" in result_data["result"]

    def test_custom_tool_error_handling(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):
        """Custom tool exception should result in error response."""
        import asyncio

        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter(
            additional_tools=[(EchoInput, failing_tool)],
        )
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]
        echo_tool = next(t for t in tools if t.name == "echo")

        with room_context("room-123"):
            result = echo_tool._run(message="test")

        result_data = json.loads(result)
        assert result_data["status"] == "error"
        assert "Service unavailable" in result_data["message"]

    def test_custom_tool_reports_execution_when_enabled(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):
        """Custom tool should report tool_call and tool_result events when enabled."""
        import asyncio

        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter(
            enable_execution_reporting=True,
            additional_tools=[(EchoInput, echo_message)],
        )
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]
        echo_tool = next(t for t in tools if t.name == "echo")

        with room_context("room-123"):
            echo_tool._run(message="Hello!")

        # Should have called send_event for tool_call and tool_result
        assert mock_tools.send_event.call_count >= 2

    def test_custom_tool_without_room_context(self, CrewAIAdapter, crewai_mocks):
        """Custom tool should return error when called without room context."""
        import asyncio

        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter(
            additional_tools=[(EchoInput, echo_message)],
        )
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]
        echo_tool = next(t for t in tools if t.name == "echo")

        # Call without setting context
        result = echo_tool._run(message="Hello!")

        result_data = json.loads(result)
        assert result_data["status"] == "error"
        assert "No room context available" in result_data["message"]
