"""Tests for ClaudeSDKAdapter.

Tests for shared adapter behavior (initialization defaults, custom kwargs,
history_converter, on_message callable, cleanup safety) live in
tests/framework_conformance/test_adapter_conformance.py.
This file contains ClaudeSDK-specific behavior: MCP server/session manager
creation, room tools storage, SDK query invocation, custom tools,
session persistence, and the chat-based approval flow.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from thenvoi.adapters.claude_sdk import (
    ClaudeSDKAdapter,
    _CLAUDE_SDK_AVAILABLE,
    _PendingApproval,
    _pre_tool_use_continue_hook,
    THENVOI_ALL_TOOLS,
    THENVOI_BASE_TOOLS,
    THENVOI_MEMORY_TOOLS,
)
from thenvoi.converters.claude_sdk import ClaudeSDKSessionState
from thenvoi.runtime.tools import ALL_TOOL_NAMES
from thenvoi.core.types import PlatformMessage

pytestmark = pytest.mark.skipif(
    not _CLAUDE_SDK_AVAILABLE,
    reason="claude-agent-sdk not installed (pip install thenvoi-sdk[claude_sdk])",
)


@pytest.fixture
def sample_message():
    """Create a sample platform message."""
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
    tools.send_message = AsyncMock(return_value={"status": "sent"})
    tools.send_event = AsyncMock(return_value={"status": "sent"})
    tools.add_participant = AsyncMock(return_value={"id": "user-1"})
    tools.remove_participant = AsyncMock(return_value={"status": "removed"})
    tools.lookup_peers = AsyncMock(return_value={"peers": []})
    tools.get_participants = AsyncMock(return_value=[])
    return tools


class TestInitialization:
    """Tests for adapter initialization (memory tools specific)."""

    def test_default_initialization(self):
        """Should initialize with no memory capability by default."""
        adapter = ClaudeSDKAdapter()

        from thenvoi.core.types import Capability

        assert Capability.MEMORY not in adapter.features.capabilities

    def test_enable_memory_tools(self):
        """Should accept enable_memory_tools parameter (deprecated)."""
        adapter = ClaudeSDKAdapter(enable_memory_tools=True)

        from thenvoi.core.types import Capability

        assert Capability.MEMORY in adapter.features.capabilities


class TestOnStarted:
    """Tests for on_started() method."""

    @pytest.mark.asyncio
    async def test_creates_mcp_server_and_session_manager(self):
        """Should create MCP server and session manager on start."""
        adapter = ClaudeSDKAdapter()

        # Mock the session manager
        with patch(
            "thenvoi.adapters.claude_sdk.ClaudeSessionManager"
        ) as mock_manager_class:
            mock_manager = MagicMock()
            mock_manager_class.return_value = mock_manager

            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            assert adapter.agent_name == "TestBot"
            assert adapter.agent_description == "A test bot"
            assert adapter._session_manager is not None
            assert adapter._mcp_server is not None

    @pytest.mark.asyncio
    async def test_default_options_have_no_model(self):
        """Default ClaudeSDKAdapter() should pass model=None and fallback_model=None."""
        adapter = ClaudeSDKAdapter()

        with patch(
            "thenvoi.adapters.claude_sdk.ClaudeSessionManager"
        ) as mock_manager_class:
            mock_manager_class.return_value = MagicMock()

            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            sdk_options = mock_manager_class.call_args[0][0]
            assert sdk_options.model is None
            assert sdk_options.fallback_model is None

    @pytest.mark.asyncio
    async def test_explicit_model_is_forwarded(self):
        """Explicit model= should land in ClaudeAgentOptions.model."""
        adapter = ClaudeSDKAdapter(model="opus")

        with patch(
            "thenvoi.adapters.claude_sdk.ClaudeSessionManager"
        ) as mock_manager_class:
            mock_manager_class.return_value = MagicMock()

            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            sdk_options = mock_manager_class.call_args[0][0]
            assert sdk_options.model == "opus"
            assert sdk_options.fallback_model is None

    @pytest.mark.asyncio
    async def test_fallback_model_is_forwarded(self):
        """Both model and fallback_model must reach ClaudeAgentOptions.

        Regression guard: catches a missed `fallback_model=self.fallback_model`
        in the ClaudeAgentOptions construction in on_started().
        """
        adapter = ClaudeSDKAdapter(model="opus", fallback_model="sonnet")

        with patch(
            "thenvoi.adapters.claude_sdk.ClaudeSessionManager"
        ) as mock_manager_class:
            mock_manager_class.return_value = MagicMock()

            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            sdk_options = mock_manager_class.call_args[0][0]
            assert sdk_options.model == "opus"
            assert sdk_options.fallback_model == "sonnet"


class TestOnMessage:
    """Tests for on_message() method (bootstrap, history, invoke and response)."""

    @pytest.mark.asyncio
    async def test_initializes_history_on_bootstrap(self, sample_message, mock_tools):
        """First message in a room initializes session context and triggers invoke."""
        adapter = ClaudeSDKAdapter()
        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)

        with (
            patch(
                "thenvoi.adapters.claude_sdk.ClaudeSessionManager",
                return_value=mock_manager,
            ),
            patch.object(
                adapter, "_process_response", new_callable=AsyncMock
            ) as mock_process,
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=ClaudeSDKSessionState(text=""),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            assert adapter._room_tools["room-123"] is mock_tools
            assert adapter._session_context["room-123"] == ""
            mock_manager.get_or_create_session.assert_awaited_once_with(
                "room-123", resume_session_id=None
            )
            mock_client.query.assert_awaited_once()
            mock_process.assert_awaited_once_with(mock_client, "room-123", mock_tools)

    @pytest.mark.asyncio
    async def test_loads_existing_history_on_bootstrap(
        self, sample_message, mock_tools
    ):
        """When history is provided on bootstrap, it is loaded and used for the next invoke."""
        adapter = ClaudeSDKAdapter()
        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)
        prior_context = "[Alice]: Hello\n[Bot]: Hi there."

        with (
            patch(
                "thenvoi.adapters.claude_sdk.ClaudeSessionManager",
                return_value=mock_manager,
            ),
            patch.object(adapter, "_process_response", new_callable=AsyncMock),
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=ClaudeSDKSessionState(text=prior_context),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            assert adapter._session_context["room-123"] == prior_context
            call_args = mock_client.query.call_args[0][0]
            assert "[Previous conversation context:]" in call_args
            assert prior_context in call_args

    @pytest.mark.asyncio
    async def test_invoke_and_response(self, sample_message, mock_tools):
        """Adapter invokes the SDK client and processes response."""
        adapter = ClaudeSDKAdapter()
        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)

        with (
            patch(
                "thenvoi.adapters.claude_sdk.ClaudeSessionManager",
                return_value=mock_manager,
            ),
            patch.object(
                adapter, "_process_response", new_callable=AsyncMock
            ) as mock_process,
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=ClaudeSDKSessionState(text=""),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            mock_client.query.assert_awaited_once()
            full_message = mock_client.query.call_args[0][0]
            assert "room-123" in full_message
            assert "Hello, agent!" in full_message
            mock_process.assert_awaited_once()


class TestErrorHandling:
    """Tests for error handling when SDK or tools raise."""

    @pytest.mark.asyncio
    async def test_reports_error_on_query_failure(self, sample_message, mock_tools):
        """When client.query raises, adapter reports error via send_event and re-raises."""
        adapter = ClaudeSDKAdapter()
        mock_client = MagicMock()
        mock_client.query = AsyncMock(side_effect=Exception("API Error"))
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)

        with patch(
            "thenvoi.adapters.claude_sdk.ClaudeSessionManager",
            return_value=mock_manager,
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            with pytest.raises(Exception, match="API Error"):
                await adapter.on_message(
                    msg=sample_message,
                    tools=mock_tools,
                    history=ClaudeSDKSessionState(text=""),
                    participants_msg=None,
                    contacts_msg=None,
                    is_session_bootstrap=True,
                    room_id="room-123",
                )

            mock_tools.send_event.assert_called()
            call_kwargs = mock_tools.send_event.call_args[1]
            assert call_kwargs.get("message_type") == "error"
            assert "API Error" in call_kwargs.get("content", "")


class TestCLIConnectionError:
    """Tests for dead subprocess recovery via CLIConnectionError."""

    @pytest.mark.asyncio
    async def test_invalidates_session_on_cli_connection_error(
        self, sample_message, mock_tools
    ):
        """CLIConnectionError should invalidate the dead session and re-raise."""
        from claude_agent_sdk._errors import CLIConnectionError

        adapter = ClaudeSDKAdapter()
        mock_client = MagicMock()
        mock_client.query = AsyncMock(
            side_effect=CLIConnectionError("Cannot write to terminated process")
        )
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)
        mock_manager.invalidate_session = AsyncMock()

        with patch(
            "thenvoi.adapters.claude_sdk.ClaudeSessionManager",
            return_value=mock_manager,
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            with pytest.raises(CLIConnectionError):
                await adapter.on_message(
                    msg=sample_message,
                    tools=mock_tools,
                    history=ClaudeSDKSessionState(text=""),
                    participants_msg=None,
                    contacts_msg=None,
                    is_session_bootstrap=True,
                    room_id="room-123",
                )

            # Dead session should be invalidated
            mock_manager.invalidate_session.assert_awaited_once_with("room-123")
            # Cached session ID should be cleared
            assert "room-123" not in adapter._session_ids

    @pytest.mark.asyncio
    async def test_cli_connection_error_reports_error_event(
        self, sample_message, mock_tools
    ):
        """CLIConnectionError should report error event to the user."""
        from claude_agent_sdk._errors import CLIConnectionError

        adapter = ClaudeSDKAdapter()
        mock_client = MagicMock()
        mock_client.query = AsyncMock(side_effect=CLIConnectionError("Process dead"))
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)
        mock_manager.invalidate_session = AsyncMock()

        with patch(
            "thenvoi.adapters.claude_sdk.ClaudeSessionManager",
            return_value=mock_manager,
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            with pytest.raises(CLIConnectionError):
                await adapter.on_message(
                    msg=sample_message,
                    tools=mock_tools,
                    history=ClaudeSDKSessionState(text=""),
                    participants_msg=None,
                    contacts_msg=None,
                    is_session_bootstrap=True,
                    room_id="room-123",
                )

            # Error should be surfaced to the user
            mock_tools.send_event.assert_called()
            call_kwargs = mock_tools.send_event.call_args[1]
            assert call_kwargs.get("message_type") == "error"
            assert "Process dead" in call_kwargs.get("content", "")

    @pytest.mark.asyncio
    async def test_clears_session_id_on_cli_connection_error(
        self, sample_message, mock_tools
    ):
        """CLIConnectionError should clear cached session ID so resume is not attempted."""
        from claude_agent_sdk._errors import CLIConnectionError

        adapter = ClaudeSDKAdapter()
        # Pre-populate a session ID
        adapter._session_ids["room-123"] = "sess-old"

        mock_client = MagicMock()
        mock_client.query = AsyncMock(side_effect=CLIConnectionError("Dead"))
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)
        mock_manager.invalidate_session = AsyncMock()

        with patch(
            "thenvoi.adapters.claude_sdk.ClaudeSessionManager",
            return_value=mock_manager,
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            with pytest.raises(CLIConnectionError):
                await adapter.on_message(
                    msg=sample_message,
                    tools=mock_tools,
                    history=ClaudeSDKSessionState(text=""),
                    participants_msg=None,
                    contacts_msg=None,
                    is_session_bootstrap=False,
                    room_id="room-123",
                )

            assert "room-123" not in adapter._session_ids


class TestRoomToolsStorage:
    """Tests for room tools storage."""

    def test_stores_tools_per_room(self):
        """Should store tools per room for MCP server access."""
        adapter = ClaudeSDKAdapter()

        mock_tools_1 = MagicMock()
        mock_tools_2 = MagicMock()

        adapter._room_tools["room-1"] = mock_tools_1
        adapter._room_tools["room-2"] = mock_tools_2

        assert adapter._room_tools["room-1"] is mock_tools_1
        assert adapter._room_tools["room-2"] is mock_tools_2


class TestOnCleanup:
    """Tests for on_cleanup() method."""

    @pytest.mark.asyncio
    async def test_cleans_up_session_and_tools(self):
        """Should cleanup session and remove room tools."""
        adapter = ClaudeSDKAdapter()

        # Set up mock session manager
        mock_session_manager = AsyncMock()
        adapter._session_manager = mock_session_manager
        adapter._room_tools["room-123"] = MagicMock()

        await adapter.on_cleanup("room-123")

        mock_session_manager.cleanup_session.assert_awaited_once_with("room-123")
        assert "room-123" not in adapter._room_tools

    @pytest.mark.asyncio
    async def test_cleanup_without_session_manager_is_safe(self):
        """Should handle cleanup when session manager not initialized."""
        adapter = ClaudeSDKAdapter()
        adapter._room_tools["room-123"] = MagicMock()

        # Should not raise
        await adapter.on_cleanup("room-123")

        assert "room-123" not in adapter._room_tools


class TestCleanupAll:
    """Tests for cleanup_all() method."""

    @pytest.mark.asyncio
    async def test_cleans_up_all_sessions(self):
        """Should stop session manager and clear room tools."""
        adapter = ClaudeSDKAdapter()

        mock_session_manager = AsyncMock()
        adapter._session_manager = mock_session_manager
        adapter._room_tools["room-1"] = MagicMock()
        adapter._room_tools["room-2"] = MagicMock()

        await adapter.cleanup_all()

        mock_session_manager.stop.assert_awaited_once()
        assert len(adapter._room_tools) == 0


class TestThenvoiTools:
    """Tests for Thenvoi tool names constants."""

    def test_thenvoi_base_tools_list(self):
        """Should define base platform tools (always included)."""
        expected = {
            "mcp__thenvoi__thenvoi_send_message",
            "mcp__thenvoi__thenvoi_send_event",
            "mcp__thenvoi__thenvoi_add_participant",
            "mcp__thenvoi__thenvoi_remove_participant",
            "mcp__thenvoi__thenvoi_get_participants",
            "mcp__thenvoi__thenvoi_lookup_peers",
            "mcp__thenvoi__thenvoi_create_chatroom",
            # Contact management tools
            "mcp__thenvoi__thenvoi_list_contacts",
            "mcp__thenvoi__thenvoi_add_contact",
            "mcp__thenvoi__thenvoi_remove_contact",
            "mcp__thenvoi__thenvoi_list_contact_requests",
            "mcp__thenvoi__thenvoi_respond_contact_request",
        }

        assert set(THENVOI_BASE_TOOLS) == expected
        assert len(THENVOI_BASE_TOOLS) == len(set(THENVOI_BASE_TOOLS)), (
            "duplicate entries in THENVOI_BASE_TOOLS"
        )

    def test_thenvoi_memory_tools_list(self):
        """Should define memory tools (enterprise only - opt-in)."""
        expected = {
            "mcp__thenvoi__thenvoi_list_memories",
            "mcp__thenvoi__thenvoi_store_memory",
            "mcp__thenvoi__thenvoi_get_memory",
            "mcp__thenvoi__thenvoi_supersede_memory",
            "mcp__thenvoi__thenvoi_archive_memory",
        }

        assert set(THENVOI_MEMORY_TOOLS) == expected
        assert len(THENVOI_MEMORY_TOOLS) == len(set(THENVOI_MEMORY_TOOLS)), (
            "duplicate entries in THENVOI_MEMORY_TOOLS"
        )

    def test_thenvoi_all_tools_combines_base_and_memory(self):
        """THENVOI_ALL_TOOLS should combine base and memory tools without duplicates."""
        from thenvoi.runtime.tools import mcp_tool_names

        assert set(THENVOI_ALL_TOOLS) == set(THENVOI_BASE_TOOLS) | set(
            THENVOI_MEMORY_TOOLS
        )
        assert len(THENVOI_ALL_TOOLS) == len(set(THENVOI_ALL_TOOLS)), (
            "duplicate entries"
        )
        assert set(THENVOI_ALL_TOOLS) == set(mcp_tool_names(ALL_TOOL_NAMES)), (
            "THENVOI_ALL_TOOLS content does not match mcp_tool_names(ALL_TOOL_NAMES) — "
            "a tool may have been dropped from the registry"
        )


class TestCustomTools:
    """Tests for custom tool support (CustomToolDef → MCP)."""

    def test_accepts_additional_tools_parameter(self):
        """Adapter accepts list of CustomToolDef tuples."""
        from pydantic import BaseModel, Field

        class EchoInput(BaseModel):
            """Echo the message."""

            message: str = Field(description="Message to echo")

        async def echo(args: EchoInput) -> str:
            return f"Echo: {args.message}"

        adapter = ClaudeSDKAdapter(
            additional_tools=[(EchoInput, echo)],
        )

        assert len(adapter._custom_tools) == 1
        assert adapter._custom_tools[0][0] is EchoInput

    def test_multiple_custom_tools(self):
        """Should accept multiple custom tools."""
        from pydantic import BaseModel

        class Tool1Input(BaseModel):
            """Tool 1."""

            x: int

        class Tool2Input(BaseModel):
            """Tool 2."""

            y: str

        def tool1(args: Tool1Input) -> int:
            return args.x + 1

        def tool2(args: Tool2Input) -> str:
            return args.y.upper()

        adapter = ClaudeSDKAdapter(
            additional_tools=[(Tool1Input, tool1), (Tool2Input, tool2)],
        )

        assert len(adapter._custom_tools) == 2

    @pytest.mark.asyncio
    async def test_custom_tools_added_to_allowed_tools(self):
        """Custom tools should be added to allowed_tools list."""
        from pydantic import BaseModel

        class CalculatorInput(BaseModel):
            """Perform calculations."""

            a: float
            b: float

        def calc(args: CalculatorInput) -> float:
            return args.a + args.b

        adapter = ClaudeSDKAdapter(
            additional_tools=[(CalculatorInput, calc)],
        )

        # Mock the session manager creation
        with patch(
            "thenvoi.adapters.claude_sdk.ClaudeSessionManager"
        ) as mock_manager_class:
            mock_manager = MagicMock()
            mock_manager_class.return_value = mock_manager

            # Capture the ClaudeAgentOptions passed to session manager
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            # Get the options passed to ClaudeSessionManager
            call_args = mock_manager_class.call_args
            sdk_options = call_args[0][0]

            # Verify custom tool is in allowed_tools
            assert "mcp__thenvoi__calculator" in sdk_options.allowed_tools
            # Platform tools should still be there
            assert "mcp__thenvoi__thenvoi_send_message" in sdk_options.allowed_tools

    @pytest.mark.asyncio
    async def test_custom_tools_registered_in_mcp_server(self):
        """Custom tools should be registered in MCP server (memory tools disabled)."""
        from pydantic import BaseModel

        class EchoInput(BaseModel):
            """Echo tool."""

            message: str

        async def echo(args: EchoInput) -> str:
            return f"Echo: {args.message}"

        adapter = ClaudeSDKAdapter(
            additional_tools=[(EchoInput, echo)],
        )

        mock_backend = MagicMock()
        mock_backend.allowed_tools = [f"tool-{i}" for i in range(13)]
        mock_backend.server = MagicMock()

        with patch(
            "thenvoi.adapters.claude_sdk.create_thenvoi_mcp_backend",
            new=AsyncMock(return_value=mock_backend),
        ) as mock_create_backend:
            backend = await adapter._create_mcp_backend()

        assert backend is mock_backend
        mock_create_backend.assert_awaited_once()
        tool_definitions = mock_create_backend.await_args.kwargs["tool_definitions"]
        tool_names = [td.name for td in tool_definitions]
        # Base platform tools registered
        assert "thenvoi_send_message" in tool_names
        assert "thenvoi_send_event" in tool_names
        assert "thenvoi_add_participant" in tool_names
        assert "thenvoi_remove_participant" in tool_names
        assert "thenvoi_get_participants" in tool_names
        assert "thenvoi_lookup_peers" in tool_names
        assert "thenvoi_create_chatroom" in tool_names
        # Memory and contacts excluded (no capabilities set)
        assert "thenvoi_list_contacts" not in tool_names
        assert "thenvoi_list_memories" not in tool_names

    @pytest.mark.asyncio
    async def test_custom_tools_registered_with_memory_tools_enabled(self):
        """Custom tools should be registered in MCP server (memory tools enabled)."""
        from pydantic import BaseModel

        class EchoInput(BaseModel):
            """Echo tool."""

            message: str

        async def echo(args: EchoInput) -> str:
            return f"Echo: {args.message}"

        adapter = ClaudeSDKAdapter(
            additional_tools=[(EchoInput, echo)],
            enable_memory_tools=True,
        )

        mock_backend = MagicMock()
        mock_backend.allowed_tools = [f"tool-{i}" for i in range(18)]
        mock_backend.server = MagicMock()

        with patch(
            "thenvoi.adapters.claude_sdk.create_thenvoi_mcp_backend",
            new=AsyncMock(return_value=mock_backend),
        ) as mock_create_backend:
            backend = await adapter._create_mcp_backend()

        assert backend is mock_backend
        mock_create_backend.assert_awaited_once()
        tool_definitions = mock_create_backend.await_args.kwargs["tool_definitions"]
        tool_names = [td.name for td in tool_definitions]
        # Base platform tools
        assert "thenvoi_send_message" in tool_names
        assert "thenvoi_create_chatroom" in tool_names
        # Memory tools included
        assert "thenvoi_list_memories" in tool_names
        assert "thenvoi_store_memory" in tool_names
        assert "thenvoi_get_memory" in tool_names
        # Contacts excluded (not in capabilities)
        assert "thenvoi_list_contacts" not in tool_names

    def test_tool_name_derived_from_input_model(self):
        """Tool name should be derived from Pydantic model class name."""
        from thenvoi.runtime.custom_tools import get_custom_tool_name
        from pydantic import BaseModel

        class MyCustomToolInput(BaseModel):
            """A custom tool."""

            value: str

        name = get_custom_tool_name(MyCustomToolInput)
        assert name == "mycustomtool"

        class CalculatorInput(BaseModel):
            """Calculator."""

            x: int

        name = get_custom_tool_name(CalculatorInput)
        assert name == "calculator"


class TestSessionPersistence:
    """Tests for session persistence via task events."""

    @pytest.mark.asyncio
    async def test_emits_task_event_after_session_id_capture(self, mock_tools):
        """Should emit task event with session_id after ResultMessage."""
        adapter = ClaudeSDKAdapter()

        # Create a mock ResultMessage with session_id
        mock_result_msg = MagicMock()
        mock_result_msg.session_id = "sess-xyz-789"
        mock_result_msg.duration_ms = 1500
        mock_result_msg.total_cost_usd = 0.01

        # Create mock client that yields the ResultMessage
        mock_client = MagicMock()

        async def mock_receive():
            yield mock_result_msg

        mock_client.receive_response = mock_receive

        # Patch isinstance checks for ResultMessage
        with patch("thenvoi.adapters.claude_sdk.ResultMessage", type(mock_result_msg)):
            await adapter._process_response(mock_client, "room-123", mock_tools)

        # Verify task event was emitted
        mock_tools.send_event.assert_called_once_with(
            content="Claude SDK session",
            message_type="task",
            metadata={"claude_sdk_session_id": "sess-xyz-789"},
        )
        # Verify in-memory cache was updated
        assert adapter._session_ids["room-123"] == "sess-xyz-789"

    @pytest.mark.asyncio
    async def test_uses_history_session_id_for_resume(self, sample_message, mock_tools):
        """Should use history.session_id for resume on bootstrap."""
        adapter = ClaudeSDKAdapter()
        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)

        with (
            patch(
                "thenvoi.adapters.claude_sdk.ClaudeSessionManager",
                return_value=mock_manager,
            ),
            patch.object(adapter, "_process_response", new_callable=AsyncMock),
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=ClaudeSDKSessionState(
                    text="[Alice]: Hello", session_id="sess-from-history"
                ),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            mock_manager.get_or_create_session.assert_awaited_once_with(
                "room-123", resume_session_id="sess-from-history"
            )

    @pytest.mark.asyncio
    async def test_no_resume_on_non_bootstrap(self, sample_message, mock_tools):
        """Should not attempt resume on non-bootstrap messages."""
        adapter = ClaudeSDKAdapter()
        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)

        with (
            patch(
                "thenvoi.adapters.claude_sdk.ClaudeSessionManager",
                return_value=mock_manager,
            ),
            patch.object(adapter, "_process_response", new_callable=AsyncMock),
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=ClaudeSDKSessionState(
                    text="", session_id="sess-should-not-use"
                ),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-123",
            )

            mock_manager.get_or_create_session.assert_awaited_once_with(
                "room-123", resume_session_id=None
            )

    @pytest.mark.asyncio
    async def test_falls_back_to_new_session_on_resume_failure(
        self, sample_message, mock_tools
    ):
        """Should create new session if resume fails."""
        adapter = ClaudeSDKAdapter()
        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_manager = AsyncMock()
        # First call (with resume) fails, second call (without) succeeds
        mock_manager.get_or_create_session = AsyncMock(
            side_effect=[Exception("Resume failed"), mock_client]
        )

        with (
            patch(
                "thenvoi.adapters.claude_sdk.ClaudeSessionManager",
                return_value=mock_manager,
            ),
            patch.object(adapter, "_process_response", new_callable=AsyncMock),
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )
            # Should not raise — falls back to new session
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=ClaudeSDKSessionState(text="", session_id="sess-broken"),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            assert mock_manager.get_or_create_session.await_count == 2
            # Second call should be without resume
            second_call = mock_manager.get_or_create_session.call_args_list[1]
            assert second_call == (("room-123",), {"resume_session_id": None})

    @pytest.mark.asyncio
    async def test_task_event_failure_does_not_break_flow(self, mock_tools):
        """Task event emission failure should not break the message flow."""
        adapter = ClaudeSDKAdapter()
        mock_tools.send_event = AsyncMock(side_effect=Exception("Network error"))

        mock_result_msg = MagicMock()
        mock_result_msg.session_id = "sess-xyz"
        mock_result_msg.duration_ms = 100
        mock_result_msg.total_cost_usd = 0.001

        mock_client = MagicMock()

        async def mock_receive():
            yield mock_result_msg

        mock_client.receive_response = mock_receive

        with patch("thenvoi.adapters.claude_sdk.ResultMessage", type(mock_result_msg)):
            # Should not raise despite send_event failure
            await adapter._process_response(mock_client, "room-123", mock_tools)

        # Session ID should still be captured in-memory
        assert adapter._session_ids["room-123"] == "sess-xyz"


# ======================================================================
# Chat-based approval flow tests
# ======================================================================


class TestApprovalInitialization:
    """Tests for approval-related constructor defaults."""

    def test_approval_mode_defaults_to_none(self):
        adapter = ClaudeSDKAdapter()
        assert adapter.approval_mode is None

    def test_approval_mode_configurable(self):
        adapter = ClaudeSDKAdapter(approval_mode="manual")
        assert adapter.approval_mode == "manual"

    def test_approval_config_defaults(self):
        adapter = ClaudeSDKAdapter(approval_mode="manual")
        assert adapter.approval_text_notifications is True
        assert adapter.approval_wait_timeout_s == 300.0
        assert adapter.approval_timeout_decision == "decline"
        assert adapter.max_pending_approvals_per_room == 50


class TestCommandExtraction:
    """Tests for _extract_command()."""

    def test_extracts_approve_command(self):
        assert ClaudeSDKAdapter._extract_command("/approve a-1") == ("approve", "a-1")

    def test_extracts_decline_command(self):
        assert ClaudeSDKAdapter._extract_command("/decline a-2") == ("decline", "a-2")

    def test_extracts_approvals_list(self):
        assert ClaudeSDKAdapter._extract_command("/approvals") == ("approvals", "")

    def test_extracts_status_command(self):
        assert ClaudeSDKAdapter._extract_command("/status") == ("status", "")

    def test_returns_none_for_normal_message(self):
        assert ClaudeSDKAdapter._extract_command("Hello, agent!") is None

    def test_case_insensitive(self):
        assert ClaudeSDKAdapter._extract_command("/Approve a-1") == ("approve", "a-1")

    def test_bare_word_not_matched(self):
        """Bare words like 'approve' without / prefix should not match."""
        assert ClaudeSDKAdapter._extract_command("approve a-1") is None

    def test_command_not_matched_mid_sentence(self):
        """Commands embedded in natural text should not be intercepted."""
        assert ClaudeSDKAdapter._extract_command("hey /approve a-1") is None

    def test_command_with_leading_whitespace(self):
        """Leading whitespace should be ignored."""
        assert ClaudeSDKAdapter._extract_command("  /approve a-1") == ("approve", "a-1")

    def test_approve_without_token(self):
        assert ClaudeSDKAdapter._extract_command("/approve") == ("approve", "")

    def test_multiple_slashes_not_matched(self):
        """///approve should not be treated as /approve."""
        assert ClaudeSDKAdapter._extract_command("///approve a-1") is None


class TestApprovalTokenCounter:
    """Tests for per-room approval token counters."""

    def test_tokens_are_per_room(self):
        """Each room should have its own incrementing counter."""
        adapter = ClaudeSDKAdapter(approval_mode="manual")
        assert adapter._next_approval_token("room-1") == "a-1"
        assert adapter._next_approval_token("room-1") == "a-2"
        assert adapter._next_approval_token("room-2") == "a-1"  # separate counter
        assert adapter._next_approval_token("room-1") == "a-3"

    def test_counter_persists_after_room_cleanup(self):
        """Counter should NOT reset on cleanup to avoid token collisions."""
        adapter = ClaudeSDKAdapter(approval_mode="manual")
        adapter._next_approval_token("room-1")
        adapter._next_approval_token("room-1")
        adapter._clear_pending_approvals_for_room("room-1")
        # Counter continues from where it left off
        assert adapter._next_approval_token("room-1") == "a-3"


class TestApprovalSummary:
    """Tests for _approval_summary()."""

    def test_command_tool_shows_command(self):
        summary = ClaudeSDKAdapter._approval_summary("Bash", {"command": "rm -rf /tmp"})
        assert "rm -rf /tmp" in summary

    def test_file_tool_shows_path(self):
        summary = ClaudeSDKAdapter._approval_summary(
            "Edit", {"file_path": "/src/main.py"}
        )
        assert "/src/main.py" in summary

    def test_fallback_to_tool_name(self):
        summary = ClaudeSDKAdapter._approval_summary("SomeTool", {})
        assert summary == "SomeTool"

    def test_redacts_api_key_in_command(self):
        summary = ClaudeSDKAdapter._approval_summary(
            "Bash", {"command": "curl -H token=sk-abc123 https://api.example.com"}
        )
        assert "sk-abc123" not in summary
        assert "***" in summary

    def test_redacts_password_in_command(self):
        summary = ClaudeSDKAdapter._approval_summary(
            "Bash", {"command": "mysql -u root password=s3cret db"}
        )
        assert "s3cret" not in summary
        assert "***" in summary

    def test_preserves_safe_command(self):
        summary = ClaudeSDKAdapter._approval_summary(
            "Bash", {"command": "ls -la /home/user"}
        )
        assert "ls -la /home/user" in summary


class TestApprovalCommandHandling:
    """Tests for /approve, /decline, /approvals command handling."""

    @pytest.fixture
    def adapter_with_approval(self):
        return ClaudeSDKAdapter(approval_mode="manual")

    @pytest.fixture
    def sender(self):
        return {"id": "user-456", "name": "Alice"}

    @pytest.mark.asyncio
    async def test_approvals_empty(self, adapter_with_approval, mock_tools, sender):
        """Should report no pending approvals."""
        await adapter_with_approval._handle_approval_command(
            tools=mock_tools,
            room_id="room-1",
            command="approvals",
            args="",
            sender=sender,
        )
        mock_tools.send_message.assert_awaited_once()
        assert "No pending" in mock_tools.send_message.call_args[0][0]

    @pytest.mark.asyncio
    async def test_approvals_lists_pending(
        self, adapter_with_approval, mock_tools, sender
    ):
        """Should list pending approvals with token, summary, and age."""
        loop = asyncio.get_running_loop()
        adapter_with_approval._pending_approvals["room-1"] = {
            "a-1": _PendingApproval(
                tool_name="Bash",
                tool_input={"command": "ls"},
                summary="Bash: `ls`",
                created_at=datetime.now(timezone.utc),
                future=loop.create_future(),
                requester={"id": "test-user", "name": "Test"},
            ),
        }
        await adapter_with_approval._handle_approval_command(
            tools=mock_tools,
            room_id="room-1",
            command="approvals",
            args="",
            sender=sender,
        )
        msg = mock_tools.send_message.call_args[0][0]
        assert "a-1" in msg
        assert "Bash" in msg

    @pytest.mark.asyncio
    async def test_approve_resolves_future(
        self, adapter_with_approval, mock_tools, sender
    ):
        """Should resolve the pending future with 'accept'."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        adapter_with_approval._pending_approvals["room-1"] = {
            "a-1": _PendingApproval(
                tool_name="Bash",
                tool_input={},
                summary="Bash",
                created_at=datetime.now(timezone.utc),
                future=future,
                requester={"id": "test-user", "name": "Test"},
            ),
        }
        await adapter_with_approval._handle_approval_command(
            tools=mock_tools,
            room_id="room-1",
            command="approve",
            args="a-1",
            sender=sender,
        )
        assert future.done()
        assert future.result() == "accept"

    @pytest.mark.asyncio
    async def test_decline_resolves_future(
        self, adapter_with_approval, mock_tools, sender
    ):
        """Should resolve the pending future with 'decline'."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        adapter_with_approval._pending_approvals["room-1"] = {
            "a-1": _PendingApproval(
                tool_name="Bash",
                tool_input={},
                summary="Bash",
                created_at=datetime.now(timezone.utc),
                future=future,
                requester={"id": "test-user", "name": "Test"},
            ),
        }
        await adapter_with_approval._handle_approval_command(
            tools=mock_tools,
            room_id="room-1",
            command="decline",
            args="a-1",
            sender=sender,
        )
        assert future.done()
        assert future.result() == "decline"

    @pytest.mark.asyncio
    async def test_approve_single_pending_no_token(
        self, adapter_with_approval, mock_tools, sender
    ):
        """When only 1 pending, /approve without token should resolve it."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        adapter_with_approval._pending_approvals["room-1"] = {
            "a-1": _PendingApproval(
                tool_name="Bash",
                tool_input={},
                summary="Bash",
                created_at=datetime.now(timezone.utc),
                future=future,
                requester={"id": "test-user", "name": "Test"},
            ),
        }
        await adapter_with_approval._handle_approval_command(
            tools=mock_tools,
            room_id="room-1",
            command="approve",
            args="",
            sender=sender,
        )
        assert future.result() == "accept"

    @pytest.mark.asyncio
    async def test_approve_multiple_pending_no_token(
        self, adapter_with_approval, mock_tools, sender
    ):
        """When multiple pending, /approve without token should ask for token."""
        loop = asyncio.get_running_loop()
        adapter_with_approval._pending_approvals["room-1"] = {
            "a-1": _PendingApproval(
                tool_name="Bash",
                tool_input={},
                summary="Bash",
                created_at=datetime.now(timezone.utc),
                future=loop.create_future(),
                requester={"id": "test-user", "name": "Test"},
            ),
            "a-2": _PendingApproval(
                tool_name="Edit",
                tool_input={},
                summary="Edit",
                created_at=datetime.now(timezone.utc),
                future=loop.create_future(),
                requester={"id": "test-user", "name": "Test"},
            ),
        }
        await adapter_with_approval._handle_approval_command(
            tools=mock_tools,
            room_id="room-1",
            command="approve",
            args="",
            sender=sender,
        )
        msg = mock_tools.send_message.call_args[0][0]
        assert "specify" in msg.lower()

    @pytest.mark.asyncio
    async def test_unknown_token(self, adapter_with_approval, mock_tools, sender):
        """Should report unknown token with available tokens."""
        loop = asyncio.get_running_loop()
        adapter_with_approval._pending_approvals["room-1"] = {
            "a-1": _PendingApproval(
                tool_name="Bash",
                tool_input={},
                summary="Bash",
                created_at=datetime.now(timezone.utc),
                future=loop.create_future(),
                requester={"id": "test-user", "name": "Test"},
            ),
        }
        await adapter_with_approval._handle_approval_command(
            tools=mock_tools,
            room_id="room-1",
            command="approve",
            args="bad-token",
            sender=sender,
        )
        msg = mock_tools.send_message.call_args[0][0]
        assert "Unknown" in msg
        assert "a-1" in msg


class TestApprovalAuthorization:
    """Tests for approval_authorized_senders access control."""

    @pytest.fixture
    def authorized_sender(self):
        return {"id": "admin-1", "name": "Admin"}

    @pytest.fixture
    def unauthorized_sender(self):
        return {"id": "user-99", "name": "Stranger"}

    @pytest.mark.asyncio
    async def test_authorized_sender_can_approve(self, mock_tools, authorized_sender):
        adapter = ClaudeSDKAdapter(
            approval_mode="manual",
            approval_authorized_senders={"admin-1"},
        )
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        adapter._pending_approvals["room-1"] = {
            "a-1": _PendingApproval(
                tool_name="Bash",
                tool_input={},
                summary="Bash",
                created_at=datetime.now(timezone.utc),
                future=future,
                requester={"id": "test-user", "name": "Test"},
            ),
        }
        await adapter._handle_approval_command(
            tools=mock_tools,
            room_id="room-1",
            command="approve",
            args="a-1",
            sender=authorized_sender,
        )
        assert future.done()
        assert future.result() == "accept"

    @pytest.mark.asyncio
    async def test_unauthorized_sender_rejected(self, mock_tools, unauthorized_sender):
        adapter = ClaudeSDKAdapter(
            approval_mode="manual",
            approval_authorized_senders={"admin-1"},
        )
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        adapter._pending_approvals["room-1"] = {
            "a-1": _PendingApproval(
                tool_name="Bash",
                tool_input={},
                summary="Bash",
                created_at=datetime.now(timezone.utc),
                future=future,
                requester={"id": "test-user", "name": "Test"},
            ),
        }
        await adapter._handle_approval_command(
            tools=mock_tools,
            room_id="room-1",
            command="approve",
            args="a-1",
            sender=unauthorized_sender,
        )
        assert not future.done()  # Future should NOT be resolved
        msg = mock_tools.send_message.call_args[0][0]
        assert "not authorized" in msg.lower()

    @pytest.mark.asyncio
    async def test_unauthorized_sender_can_list_approvals(
        self, mock_tools, unauthorized_sender
    ):
        """/approvals should be available to all participants regardless of auth."""
        adapter = ClaudeSDKAdapter(
            approval_mode="manual",
            approval_authorized_senders={"admin-1"},
        )
        await adapter._handle_approval_command(
            tools=mock_tools,
            room_id="room-1",
            command="approvals",
            args="",
            sender=unauthorized_sender,
        )
        assert "No pending" in mock_tools.send_message.call_args[0][0]

    @pytest.mark.asyncio
    async def test_no_restriction_when_authorized_senders_is_none(self, mock_tools):
        """When approval_authorized_senders is None, any sender can approve."""
        adapter = ClaudeSDKAdapter(approval_mode="manual")
        sender = {"id": "anyone", "name": "Anyone"}
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        adapter._pending_approvals["room-1"] = {
            "a-1": _PendingApproval(
                tool_name="Bash",
                tool_input={},
                summary="Bash",
                created_at=datetime.now(timezone.utc),
                future=future,
                requester={"id": "test-user", "name": "Test"},
            ),
        }
        await adapter._handle_approval_command(
            tools=mock_tools,
            room_id="room-1",
            command="approve",
            args="a-1",
            sender=sender,
        )
        assert future.done()
        assert future.result() == "accept"


class TestCanUseToolCallback:
    """Tests for the can_use_tool callback (auto and manual modes)."""

    @pytest.mark.asyncio
    async def test_auto_accept_returns_allow(self, mock_tools):
        """auto_accept mode should return PermissionResultAllow."""
        from claude_agent_sdk.types import (
            PermissionResultAllow,
            ToolPermissionContext,
        )

        adapter = ClaudeSDKAdapter(approval_mode="auto_accept")
        adapter._room_tools["room-1"] = mock_tools
        adapter._room_last_sender["room-1"] = {"id": "u1", "name": "Bob"}

        callback = adapter._make_can_use_tool("room-1")
        result = await callback("Bash", {"command": "ls"}, ToolPermissionContext())

        assert isinstance(result, PermissionResultAllow)

    @pytest.mark.asyncio
    async def test_auto_accept_sends_notification(self, mock_tools):
        """auto_accept should send policy notification when enabled."""
        from claude_agent_sdk.types import ToolPermissionContext

        adapter = ClaudeSDKAdapter(
            approval_mode="auto_accept", approval_text_notifications=True
        )
        adapter._room_tools["room-1"] = mock_tools
        adapter._room_last_sender["room-1"] = {"id": "u1", "name": "Bob"}

        callback = adapter._make_can_use_tool("room-1")
        await callback("Bash", {"command": "ls"}, ToolPermissionContext())

        mock_tools.send_message.assert_awaited_once()
        msg = mock_tools.send_message.call_args[0][0]
        assert "accept" in msg.lower()

    @pytest.mark.asyncio
    async def test_auto_decline_returns_deny(self, mock_tools):
        """auto_decline mode should return PermissionResultDeny."""
        from claude_agent_sdk.types import (
            PermissionResultDeny,
            ToolPermissionContext,
        )

        adapter = ClaudeSDKAdapter(approval_mode="auto_decline")
        adapter._room_tools["room-1"] = mock_tools
        adapter._room_last_sender["room-1"] = {"id": "u1", "name": "Bob"}

        callback = adapter._make_can_use_tool("room-1")
        result = await callback("Bash", {"command": "ls"}, ToolPermissionContext())

        assert isinstance(result, PermissionResultDeny)

    @pytest.mark.asyncio
    async def test_auto_accept_no_notification_when_disabled(self, mock_tools):
        """Should not send notification when approval_text_notifications=False."""
        from claude_agent_sdk.types import ToolPermissionContext

        adapter = ClaudeSDKAdapter(
            approval_mode="auto_accept", approval_text_notifications=False
        )
        adapter._room_tools["room-1"] = mock_tools
        adapter._room_last_sender["room-1"] = {"id": "u1", "name": "Bob"}

        callback = adapter._make_can_use_tool("room-1")
        await callback("Bash", {"command": "ls"}, ToolPermissionContext())

        mock_tools.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_manual_mode_sends_approval_request(self, mock_tools):
        """Manual mode should send approval message and wait on future."""
        from claude_agent_sdk.types import (
            PermissionResultAllow,
            ToolPermissionContext,
        )

        adapter = ClaudeSDKAdapter(approval_mode="manual", approval_wait_timeout_s=1.0)
        adapter._room_tools["room-1"] = mock_tools
        adapter._room_last_sender["room-1"] = {"id": "u1", "name": "Bob"}

        callback = adapter._make_can_use_tool("room-1")

        # Simulate user approving shortly after request
        async def approve_soon():
            await asyncio.sleep(0.05)
            pending = adapter._pending_approvals.get("room-1", {})
            for token, item in pending.items():
                if not item.future.done():
                    item.future.set_result("accept")

        asyncio.get_running_loop().create_task(approve_soon())

        result = await callback("Bash", {"command": "ls"}, ToolPermissionContext())

        assert isinstance(result, PermissionResultAllow)
        # Should have sent an approval request message
        assert mock_tools.send_message.await_count >= 1

    @pytest.mark.asyncio
    async def test_manual_mode_timeout_declines(self, mock_tools):
        """Manual mode should decline on timeout when timeout_decision='decline'."""
        from claude_agent_sdk.types import (
            PermissionResultDeny,
            ToolPermissionContext,
        )

        adapter = ClaudeSDKAdapter(
            approval_mode="manual",
            approval_wait_timeout_s=0.05,
            approval_timeout_decision="decline",
        )
        adapter._room_tools["room-1"] = mock_tools
        adapter._room_last_sender["room-1"] = {"id": "u1", "name": "Bob"}

        callback = adapter._make_can_use_tool("room-1")
        result = await callback("Bash", {"command": "ls"}, ToolPermissionContext())

        assert isinstance(result, PermissionResultDeny)

    @pytest.mark.asyncio
    async def test_manual_mode_timeout_accepts(self, mock_tools):
        """Manual mode should accept on timeout when timeout_decision='accept'."""
        from claude_agent_sdk.types import (
            PermissionResultAllow,
            ToolPermissionContext,
        )

        adapter = ClaudeSDKAdapter(
            approval_mode="manual",
            approval_wait_timeout_s=0.05,
            approval_timeout_decision="accept",
        )
        adapter._room_tools["room-1"] = mock_tools
        adapter._room_last_sender["room-1"] = {"id": "u1", "name": "Bob"}

        callback = adapter._make_can_use_tool("room-1")
        result = await callback("Bash", {"command": "ls"}, ToolPermissionContext())

        assert isinstance(result, PermissionResultAllow)

    @pytest.mark.asyncio
    async def test_manual_mode_notification_failure_declines(self, mock_tools):
        """If the approval notification can't be delivered, decline immediately."""
        from claude_agent_sdk.types import (
            PermissionResultDeny,
            ToolPermissionContext,
        )

        adapter = ClaudeSDKAdapter(
            approval_mode="manual",
            approval_wait_timeout_s=5.0,
        )
        mock_tools.send_message = AsyncMock(side_effect=RuntimeError("network down"))
        adapter._room_tools["room-1"] = mock_tools
        adapter._room_last_sender["room-1"] = {"id": "u1", "name": "Bob"}

        callback = adapter._make_can_use_tool("room-1")
        result = await callback("Bash", {"command": "ls"}, ToolPermissionContext())

        assert isinstance(result, PermissionResultDeny)
        # Should not leave a dangling pending approval
        assert len(adapter._pending_approvals.get("room-1", {})) == 0


class TestOnMessageCommandInterception:
    """Tests for command interception in on_message()."""

    @pytest.mark.asyncio
    async def test_approve_command_intercepted(self, mock_tools):
        """Messages with /approve should not be sent to Claude."""
        adapter = ClaudeSDKAdapter(approval_mode="manual")
        loop = asyncio.get_running_loop()

        # Pre-populate a pending approval
        future: asyncio.Future[str] = loop.create_future()
        adapter._pending_approvals["room-1"] = {
            "a-1": _PendingApproval(
                tool_name="Bash",
                tool_input={},
                summary="Bash",
                created_at=datetime.now(timezone.utc),
                future=future,
                requester={"id": "test-user", "name": "Test"},
            ),
        }

        msg = PlatformMessage(
            id="msg-1",
            room_id="room-1",
            content="/approve a-1",
            sender_id="user-1",
            sender_type="User",
            sender_name="Alice",
            message_type="text",
            metadata={},
            created_at=datetime.now(timezone.utc),
        )

        mock_manager = AsyncMock()
        adapter._session_manager = mock_manager

        await adapter.on_message(
            msg=msg,
            tools=mock_tools,
            history=ClaudeSDKSessionState(text=""),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        # Should not have called get_or_create_session (no query sent)
        mock_manager.get_or_create_session.assert_not_awaited()
        # Future should be resolved
        assert future.result() == "accept"

    @pytest.mark.asyncio
    async def test_status_command_intercepted(self, mock_tools):
        """Messages with /status should be handled locally."""
        adapter = ClaudeSDKAdapter(approval_mode="manual")
        mock_manager = MagicMock()
        mock_manager.get_session_count.return_value = 2
        mock_manager.get_or_create_session = AsyncMock()
        adapter._session_manager = mock_manager

        msg = PlatformMessage(
            id="msg-1",
            room_id="room-1",
            content="/status",
            sender_id="user-1",
            sender_type="User",
            sender_name="Alice",
            message_type="text",
            metadata={},
            created_at=datetime.now(timezone.utc),
        )

        await adapter.on_message(
            msg=msg,
            tools=mock_tools,
            history=ClaudeSDKSessionState(text=""),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        mock_manager.get_or_create_session.assert_not_awaited()
        mock_tools.send_message.assert_awaited_once()
        status_msg = mock_tools.send_message.call_args[0][0]
        assert "Claude SDK Status" in status_msg
        assert "manual" in status_msg

    @pytest.mark.asyncio
    async def test_approve_not_intercepted_when_approval_disabled(self, mock_tools):
        """Approval commands should be forwarded to Claude when approval_mode is None."""
        adapter = ClaudeSDKAdapter()  # approval_mode=None
        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)

        msg = PlatformMessage(
            id="msg-1",
            room_id="room-1",
            content="/approve a-1",
            sender_id="user-1",
            sender_type="User",
            sender_name="Alice",
            message_type="text",
            metadata={},
            created_at=datetime.now(timezone.utc),
        )

        with (
            patch(
                "thenvoi.adapters.claude_sdk.ClaudeSessionManager",
                return_value=mock_manager,
            ),
            patch.object(adapter, "_process_response", new_callable=AsyncMock),
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )
            await adapter.on_message(
                msg=msg,
                tools=mock_tools,
                history=ClaudeSDKSessionState(text=""),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )

            # Should have queried Claude (not intercepted)
            mock_client.query.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_status_not_intercepted_when_approval_disabled(self, mock_tools):
        """/status should be forwarded to Claude when approval_mode is None."""
        adapter = ClaudeSDKAdapter()  # approval_mode=None
        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)

        msg = PlatformMessage(
            id="msg-1",
            room_id="room-1",
            content="/status",
            sender_id="user-1",
            sender_type="User",
            sender_name="Alice",
            message_type="text",
            metadata={},
            created_at=datetime.now(timezone.utc),
        )

        with (
            patch(
                "thenvoi.adapters.claude_sdk.ClaudeSessionManager",
                return_value=mock_manager,
            ),
            patch.object(adapter, "_process_response", new_callable=AsyncMock),
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )
            await adapter.on_message(
                msg=msg,
                tools=mock_tools,
                history=ClaudeSDKSessionState(text=""),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )

            # Should have queried Claude (not intercepted)
            mock_client.query.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_normal_message_not_intercepted(self, sample_message, mock_tools):
        """Normal messages should proceed to Claude query as usual."""
        adapter = ClaudeSDKAdapter(approval_mode="manual")
        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)

        with (
            patch(
                "thenvoi.adapters.claude_sdk.ClaudeSessionManager",
                return_value=mock_manager,
            ),
            patch.object(adapter, "_process_response", new_callable=AsyncMock),
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=ClaudeSDKSessionState(text=""),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            # Should proceed to query Claude
            mock_client.query.assert_awaited_once()


class TestApprovalOnStarted:
    """Tests that on_started passes can_use_tool_factory to session manager."""

    @pytest.mark.asyncio
    async def test_passes_factory_when_approval_enabled(self):
        adapter = ClaudeSDKAdapter(approval_mode="manual")

        with patch("thenvoi.adapters.claude_sdk.ClaudeSessionManager") as mock_cls:
            mock_cls.return_value = MagicMock()
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs.get("can_use_tool_factory") is not None

    @pytest.mark.asyncio
    async def test_no_factory_when_approval_disabled(self):
        adapter = ClaudeSDKAdapter()  # approval_mode=None

        with patch("thenvoi.adapters.claude_sdk.ClaudeSessionManager") as mock_cls:
            mock_cls.return_value = MagicMock()
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs.get("can_use_tool_factory") is None

    @pytest.mark.asyncio
    async def test_sets_pre_tool_use_hook_when_approval_enabled(self):
        """PreToolUse hook must be set so the SDK delegates to can_use_tool."""
        adapter = ClaudeSDKAdapter(approval_mode="auto_accept")

        with patch("thenvoi.adapters.claude_sdk.ClaudeSessionManager") as mock_cls:
            mock_cls.return_value = MagicMock()
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            # The first positional arg is the ClaudeAgentOptions
            sdk_options = mock_cls.call_args.args[0]
            assert sdk_options.hooks is not None
            assert "PreToolUse" in sdk_options.hooks
            assert len(sdk_options.hooks["PreToolUse"]) == 1

    @pytest.mark.asyncio
    async def test_no_hooks_when_approval_disabled(self):
        adapter = ClaudeSDKAdapter()  # approval_mode=None

        with patch("thenvoi.adapters.claude_sdk.ClaudeSessionManager") as mock_cls:
            mock_cls.return_value = MagicMock()
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            sdk_options = mock_cls.call_args.args[0]
            assert sdk_options.hooks is None


class TestPreToolUseHook:
    """Tests for the PreToolUse hook that enables can_use_tool delegation."""

    @pytest.mark.asyncio
    async def test_hook_returns_continue_true(self):
        result = await _pre_tool_use_continue_hook(None, None, None)
        assert result == {"continue_": True}


class TestApprovalCleanup:
    """Tests for approval cleanup on room/adapter cleanup."""

    @pytest.mark.asyncio
    async def test_on_cleanup_declines_pending_approvals(self):
        """Pending approvals should be declined when room is cleaned up."""
        adapter = ClaudeSDKAdapter(approval_mode="manual")
        adapter._session_manager = AsyncMock()

        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        adapter._pending_approvals["room-1"] = {
            "a-1": _PendingApproval(
                tool_name="Bash",
                tool_input={},
                summary="Bash",
                created_at=datetime.now(timezone.utc),
                future=future,
                requester={"id": "test-user", "name": "Test"},
            ),
        }

        await adapter.on_cleanup("room-1")

        assert future.done()
        assert future.result() == "decline"
        assert "room-1" not in adapter._pending_approvals

    @pytest.mark.asyncio
    async def test_cleanup_all_declines_all_rooms(self):
        """cleanup_all() should decline all pending approvals across rooms."""
        adapter = ClaudeSDKAdapter(approval_mode="manual")
        adapter._session_manager = AsyncMock()

        loop = asyncio.get_running_loop()
        f1: asyncio.Future[str] = loop.create_future()
        f2: asyncio.Future[str] = loop.create_future()
        adapter._pending_approvals["room-1"] = {
            "a-1": _PendingApproval(
                tool_name="Bash",
                tool_input={},
                summary="Bash",
                created_at=datetime.now(timezone.utc),
                future=f1,
                requester={"id": "test-user", "name": "Test"},
            ),
        }
        adapter._pending_approvals["room-2"] = {
            "a-2": _PendingApproval(
                tool_name="Edit",
                tool_input={},
                summary="Edit",
                created_at=datetime.now(timezone.utc),
                future=f2,
                requester={"id": "test-user", "name": "Test"},
            ),
        }

        await adapter.cleanup_all()

        assert f1.result() == "decline"
        assert f2.result() == "decline"
        assert len(adapter._pending_approvals) == 0


class TestPendingApprovalEviction:
    """Tests for LRU eviction of pending approvals."""

    @pytest.mark.asyncio
    async def test_evicts_oldest_when_capacity_reached(self, mock_tools):
        """Should evict oldest pending when max capacity is reached."""
        from claude_agent_sdk.types import ToolPermissionContext

        adapter = ClaudeSDKAdapter(
            approval_mode="manual",
            max_pending_approvals_per_room=1,
            approval_wait_timeout_s=0.05,
            approval_timeout_decision="decline",
        )
        adapter._room_tools["room-1"] = mock_tools
        adapter._room_last_sender["room-1"] = {"id": "u1", "name": "Bob"}

        loop = asyncio.get_running_loop()
        # Pre-populate one pending approval
        old_future: asyncio.Future[str] = loop.create_future()
        adapter._pending_approvals["room-1"] = {
            "a-1": _PendingApproval(
                tool_name="Old",
                tool_input={},
                summary="Old",
                created_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
                future=old_future,
                requester={"id": "test-user", "name": "Test"},
            ),
        }

        # Now trigger a new approval (should evict old one)
        callback = adapter._make_can_use_tool("room-1")
        await callback("New", {}, ToolPermissionContext())

        # Old future should have been evicted and declined
        assert old_future.done()
        assert old_future.result() == "decline"
