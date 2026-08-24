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
import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from band.adapters.claude_sdk import (
    ClaudeSDKAdapter,
    _CLAUDE_SDK_AVAILABLE,
    _CLAUDE_SDK_MAX_BUFFER_BYTES,
    _DEFAULT_MODEL,
    _FORCED_DECLINE,
    PendingApproval,
    _pre_tool_use_continue_hook,
    BAND_ALL_TOOLS,
    BAND_BASE_TOOLS,
    BAND_MEMORY_TOOLS,
)
from band.converters.claude_sdk import ClaudeSDKSessionState
from band.runtime.tools import (
    ALL_TOOL_NAMES,
    FILE_TOOL_NAMES,
    MAX_INLINE_IMAGE_BYTES,
    missing_reply_error,
)
from band.core.types import Capability, Emit, PlatformMessage, ToolEventKey

pytestmark = pytest.mark.skipif(
    not _CLAUDE_SDK_AVAILABLE,
    reason="claude-agent-sdk not installed (pip install band-sdk[claude_sdk])",
)

if _CLAUDE_SDK_AVAILABLE:
    from claude_agent_sdk._errors import CLIConnectionError
    from claude_agent_sdk import (
        AssistantMessage,
        ResultMessage,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
    )
    from claude_agent_sdk.types import PermissionResultDeny, ToolPermissionContext


# The reply tool as the SDK namespaces it (MCP_TOOL_PREFIX + bare name).
_SEND_MESSAGE_MCP_NAME = "mcp__band__band_send_message"
_ANY_MODEL = "claude-sonnet-4-6"
# What a turn that ended without a reply going out must say — the "Error: "
# prefix is _report_error's own formatting, asserted by substring below
# rather than re-derived here.
_MISSING_REPLY_TEXT = missing_reply_error("Claude SDK")


def _tool_turn(mcp_tool_name: str) -> list:
    """A turn's stream in the protocol shape: the assistant calls a tool, then
    the result comes back in a user-type envelope."""
    return [
        AssistantMessage(
            content=[ToolUseBlock(id="tool-1", name=mcp_tool_name, input={})],
            model=_ANY_MODEL,
        ),
        UserMessage(
            content=[
                ToolResultBlock(tool_use_id="tool-1", content="ok", is_error=False)
            ]
        ),
    ]


def _error_events(mock_tools: MagicMock) -> list[str]:
    """Contents of the error events posted through send_event."""
    return [
        call.kwargs["content"]
        for call in mock_tools.send_event.call_args_list
        if call.kwargs.get("message_type") == "error"
    ]


def _narrated_message_types(mock_tools: MagicMock) -> list[str]:
    """``message_type`` of every event posted through send_event, in order."""
    return [
        call.kwargs["message_type"] for call in mock_tools.send_event.call_args_list
    ]


def _tool_result_payload(mock_tools: MagicMock) -> dict[str, Any]:
    """The parsed content of the sole tool_result event posted through send_event."""
    [result_call] = [
        call
        for call in mock_tools.send_event.call_args_list
        if call.kwargs.get("message_type") == "tool_result"
    ]
    return json.loads(result_call.kwargs["content"])


def register_pending_approval(
    adapter: ClaudeSDKAdapter,
    room_id: str = "room-1",
    token: str = "a-1",
    *,
    tool_name: str = "Bash",
    tool_input: dict[str, Any] | None = None,
    summary: str | None = None,
    created_at: datetime | None = None,
    requester: dict[str, str] | None = None,
) -> asyncio.Future[str]:
    """Register one pending approval on adapter, returning its future."""
    future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    adapter._pending_approvals.setdefault(room_id, {})[token] = PendingApproval(
        tool_name=tool_name,
        tool_input=tool_input if tool_input is not None else {},
        summary=summary or tool_name,
        created_at=created_at or datetime.now(timezone.utc),
        future=future,
        requester=requester or {"id": "test-user", "name": "Test"},
    )
    return future


def _result_message(
    *,
    session_id: str = "sess-xyz",
    is_error: bool = False,
    result: str | None = None,
    errors: list[str] | None = None,
    api_error_status: int | None = None,
    permission_denials: list[dict[str, Any]] | None = None,
) -> ResultMessage:
    """Build a real ``ResultMessage`` with only the fields a test cares about set."""
    return ResultMessage(
        subtype="success",
        duration_ms=100,
        duration_api_ms=100,
        is_error=is_error,
        num_turns=1,
        session_id=session_id,
        result=result,
        errors=errors,
        api_error_status=api_error_status,
        permission_denials=permission_denials,
    )


def _denial(tool_use_id: str, tool_name: str) -> dict[str, Any]:
    """A ``SDKPermissionDenial``-shaped entry for ``ResultMessage.permission_denials``."""
    return {"tool_name": tool_name, "tool_use_id": tool_use_id, "tool_input": {}}


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
        assert Capability.MEMORY not in adapter.features.capabilities

    def test_enable_memory_tools(self):
        """Should accept capabilities=Capability.MEMORY."""
        adapter = ClaudeSDKAdapter(capabilities=Capability.MEMORY)
        assert Capability.MEMORY in adapter.features.capabilities


class TestOnStarted:
    """Tests for on_started() method."""

    @pytest.mark.asyncio
    async def test_creates_mcp_server_and_session_manager(self):
        """Should create MCP server and session manager on start."""
        adapter = ClaudeSDKAdapter()

        # Mock the session manager
        with patch(
            "band.adapters.claude_sdk.ClaudeSessionManager"
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
    async def test_default_options_pin_default_model(self):
        """Default ClaudeSDKAdapter() pins _DEFAULT_MODEL (the npm `claude`
        binary's auto-selection fails under API-key auth), fallback_model=None."""
        adapter = ClaudeSDKAdapter()

        with patch(
            "band.adapters.claude_sdk.ClaudeSessionManager"
        ) as mock_manager_class:
            mock_manager_class.return_value = MagicMock()

            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            sdk_options = mock_manager_class.call_args[0][0]
            assert sdk_options.model == _DEFAULT_MODEL
            assert sdk_options.fallback_model is None

    @pytest.mark.asyncio
    async def test_max_buffer_size_fits_a_full_inline_image(self):
        """claude_agent_sdk's stdio transport defaults to a 1 MiB buffer and
        fatally drops the whole CLI connection if one JSON message exceeds
        it; band_read_room_file can inline an image up to
        MAX_INLINE_IMAGE_BYTES as base64 (~4/3 size increase) inside such a
        message. The configured buffer must comfortably exceed that, not the
        library's unrelated default.
        """
        adapter = ClaudeSDKAdapter()

        with patch(
            "band.adapters.claude_sdk.ClaudeSessionManager"
        ) as mock_manager_class:
            mock_manager_class.return_value = MagicMock()

            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            sdk_options = mock_manager_class.call_args[0][0]
            assert sdk_options.max_buffer_size == _CLAUDE_SDK_MAX_BUFFER_BYTES
            assert sdk_options.max_buffer_size > MAX_INLINE_IMAGE_BYTES * 4 // 3

    @pytest.mark.asyncio
    async def test_explicit_model_is_forwarded(self):
        """Explicit model= should land in ClaudeAgentOptions.model."""
        adapter = ClaudeSDKAdapter(model="opus")

        with patch(
            "band.adapters.claude_sdk.ClaudeSessionManager"
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
            "band.adapters.claude_sdk.ClaudeSessionManager"
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
                "band.adapters.claude_sdk.ClaudeSessionManager",
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

            # By default the adapter wraps tools with DedupingAgentTools so
            # MCP tool calls go through the dedup shim.  The wrapped
            # instance is what gets stored and forwarded.
            from band.integrations.claude_sdk.dedup_tools import (
                DedupingAgentTools,
            )

            stored_tools = adapter._room_tools["room-123"]
            assert isinstance(stored_tools, DedupingAgentTools)
            assert stored_tools._inner is mock_tools
            assert adapter._session_context["room-123"] == ""
            mock_manager.get_or_create_session.assert_awaited_once_with(
                "room-123", resume_session_id=None
            )
            mock_client.query.assert_awaited_once()
            mock_process.assert_awaited_once_with(mock_client, "room-123", stored_tools)

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
                "band.adapters.claude_sdk.ClaudeSessionManager",
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
            # History is framed as the agent's own memory (authoritative), not a
            # passive quote, so the model recalls facts from it under the coding preset.
            assert "memory of this room" in call_args
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
                "band.adapters.claude_sdk.ClaudeSessionManager",
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
            "band.adapters.claude_sdk.ClaudeSessionManager",
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
            "band.adapters.claude_sdk.ClaudeSessionManager",
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
            "band.adapters.claude_sdk.ClaudeSessionManager",
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
            "band.adapters.claude_sdk.ClaudeSessionManager",
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

    @pytest.mark.asyncio
    async def test_stream_ending_without_result_invalidates_session_and_fails_turn(
        self, sample_message, mock_tools
    ):
        """An EOF before ResultMessage is a dead client, not a successful turn."""
        adapter = ClaudeSDKAdapter()
        adapter._session_ids["room-123"] = "sess-old"
        mock_client = MagicMock()

        async def receive():
            if False:
                yield None

        mock_client.query = AsyncMock()
        mock_client.receive_response = MagicMock(return_value=receive())
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)
        mock_manager.invalidate_session = AsyncMock()

        with patch(
            "band.adapters.claude_sdk.ClaudeSessionManager",
            return_value=mock_manager,
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            with pytest.raises(CLIConnectionError, match="ended without a result"):
                await adapter.on_message(
                    msg=sample_message,
                    tools=mock_tools,
                    history=ClaudeSDKSessionState(text=""),
                    participants_msg=None,
                    contacts_msg=None,
                    is_session_bootstrap=False,
                    room_id="room-123",
                )

        mock_manager.invalidate_session.assert_awaited_once_with("room-123")
        assert "room-123" not in adapter._session_ids
        errors = _error_events(mock_tools)
        assert len(errors) == 1
        assert "ended without a result" in errors[0]


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


class TestBandTools:
    """Tests for Band tool names constants."""

    def test_band_base_tools_list(self):
        """Should define base platform tools (always included)."""
        expected = {
            "mcp__band__band_send_message",
            "mcp__band__band_send_event",
            "mcp__band__band_add_participant",
            "mcp__band__band_remove_participant",
            "mcp__band__band_get_participants",
            "mcp__band__band_lookup_peers",
            "mcp__band__band_create_chatroom",
            # Contact management tools
            "mcp__band__band_list_contacts",
            "mcp__band__band_add_contact",
            "mcp__band__band_remove_contact",
            "mcp__band__band_list_contact_requests",
            "mcp__band__band_respond_contact_request",
        }

        assert set(BAND_BASE_TOOLS) == expected
        assert len(BAND_BASE_TOOLS) == len(set(BAND_BASE_TOOLS)), (
            "duplicate entries in BAND_BASE_TOOLS"
        )

    def test_band_memory_tools_list(self):
        """Should define memory tools (enterprise only - opt-in)."""
        expected = {
            "mcp__band__band_list_memories",
            "mcp__band__band_store_memory",
            "mcp__band__band_get_memory",
            "mcp__band__band_supersede_memory",
            "mcp__band__band_archive_memory",
        }

        assert set(BAND_MEMORY_TOOLS) == expected
        assert len(BAND_MEMORY_TOOLS) == len(set(BAND_MEMORY_TOOLS)), (
            "duplicate entries in BAND_MEMORY_TOOLS"
        )

    def test_band_all_tools_combines_base_and_memory(self):
        """BAND_ALL_TOOLS should combine base, memory, and file tools without
        duplicates."""
        from band.runtime.tools import mcp_tool_names

        assert set(BAND_ALL_TOOLS) == (
            set(BAND_BASE_TOOLS)
            | set(BAND_MEMORY_TOOLS)
            | set(mcp_tool_names(FILE_TOOL_NAMES))
        )
        assert len(BAND_ALL_TOOLS) == len(set(BAND_ALL_TOOLS)), "duplicate entries"
        assert set(BAND_ALL_TOOLS) == set(mcp_tool_names(ALL_TOOL_NAMES)), (
            "BAND_ALL_TOOLS content does not match mcp_tool_names(ALL_TOOL_NAMES) — "
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
            "band.adapters.claude_sdk.ClaudeSessionManager"
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
            assert "mcp__band__calculator" in sdk_options.allowed_tools
            # Platform tools should still be there
            assert "mcp__band__band_send_message" in sdk_options.allowed_tools

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
            "band.adapters.claude_sdk.create_band_mcp_backend",
            new=AsyncMock(return_value=mock_backend),
        ) as mock_create_backend:
            backend = await adapter._create_mcp_backend()

        assert backend is mock_backend
        mock_create_backend.assert_awaited_once()
        tool_definitions = mock_create_backend.await_args.kwargs["tool_definitions"]
        tool_names = [td.name for td in tool_definitions]
        # Base platform tools registered
        assert "band_send_message" in tool_names
        assert "band_send_event" in tool_names
        assert "band_add_participant" in tool_names
        assert "band_remove_participant" in tool_names
        assert "band_get_participants" in tool_names
        assert "band_lookup_peers" in tool_names
        assert "band_create_chatroom" in tool_names
        # Memory and contacts excluded (no capabilities set)
        assert "band_list_contacts" not in tool_names
        assert "band_list_memories" not in tool_names

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
            capabilities=Capability.MEMORY,
        )

        mock_backend = MagicMock()
        mock_backend.allowed_tools = [f"tool-{i}" for i in range(18)]
        mock_backend.server = MagicMock()

        with patch(
            "band.adapters.claude_sdk.create_band_mcp_backend",
            new=AsyncMock(return_value=mock_backend),
        ) as mock_create_backend:
            backend = await adapter._create_mcp_backend()

        assert backend is mock_backend
        mock_create_backend.assert_awaited_once()
        tool_definitions = mock_create_backend.await_args.kwargs["tool_definitions"]
        tool_names = [td.name for td in tool_definitions]
        # Base platform tools
        assert "band_send_message" in tool_names
        assert "band_create_chatroom" in tool_names
        # Memory tools included
        assert "band_list_memories" in tool_names
        assert "band_store_memory" in tool_names
        assert "band_get_memory" in tool_names
        # Contacts excluded (not in capabilities)
        assert "band_list_contacts" not in tool_names

    def test_tool_name_derived_from_input_model(self):
        """Tool name should be derived from Pydantic model class name."""
        from band.runtime.custom_tools import get_custom_tool_name
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
        # emit=() isolates the session task event, which posts unconditionally
        # regardless of emit (see _persist_session_id) — narration is opt-out
        # by default and would otherwise add tool_call/tool_result events too.
        adapter = ClaudeSDKAdapter(emit=())

        # A turn that actually replied via band_send_message, so the missing-reply
        # guard stays quiet and the only send_event call is the session task event.
        turn = _tool_turn(_SEND_MESSAGE_MCP_NAME)
        result_msg = _result_message(session_id="sess-xyz-789")

        mock_client = MagicMock()

        async def mock_receive():
            for sdk_message in turn:
                yield sdk_message
            yield result_msg

        mock_client.receive_response = mock_receive

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
                "band.adapters.claude_sdk.ClaudeSessionManager",
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
                "band.adapters.claude_sdk.ClaudeSessionManager",
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
                "band.adapters.claude_sdk.ClaudeSessionManager",
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

        turn = _tool_turn(_SEND_MESSAGE_MCP_NAME)
        result_msg = _result_message(session_id="sess-xyz")

        mock_client = MagicMock()

        async def mock_receive():
            for sdk_message in turn:
                yield sdk_message
            yield result_msg

        mock_client.receive_response = mock_receive

        # Should not raise despite send_event failure
        await adapter._process_response(mock_client, "room-123", mock_tools)

        # Session ID should still be captured in-memory
        assert adapter._session_ids["room-123"] == "sess-xyz"


class TestTurnFailureSurfacing:
    """A failed or silent turn must surface a room-visible error."""

    def test_declined_the_reply_ignores_malformed_denial_entries(self):
        """``permission_denials`` is typed ``list[Any]`` — raw, unvalidated
        CLI JSON, not a structure this SDK guarantees the shape of. A
        malformed entry must not crash turn-completion, just fail to match."""
        adapter = ClaudeSDKAdapter()
        assert (
            adapter._declined_the_reply(["not-a-dict", 42, None], {"tool-1"}) is False
        )

    @staticmethod
    def _client_yielding(*sdk_messages) -> MagicMock:
        mock_client = MagicMock()

        async def mock_receive():
            for message in sdk_messages:
                yield message

        mock_client.receive_response = mock_receive
        return mock_client

    @pytest.mark.asyncio
    async def test_reports_error_on_is_error_result(self, mock_tools):
        """``is_error`` must surface even though ``subtype`` claims success.

        On a hard failure such as a CLI auth error, the CLI reports
        ``is_error=True`` with ``subtype="success"``, so the adapter must gate
        on ``is_error`` and never on ``subtype``.
        """
        adapter = ClaudeSDKAdapter()
        result_msg = _result_message(
            is_error=True,
            result="Not logged in · Please run /login",
            api_error_status=None,
        )
        mock_client = self._client_yielding(result_msg)

        await adapter._process_response(mock_client, "room-123", mock_tools)

        errors = _error_events(mock_tools)
        assert len(errors) == 1
        assert "Not logged in · Please run /login" in errors[0]

    @pytest.mark.asyncio
    async def test_error_detail_includes_api_error_status(self, mock_tools):
        """The HTTP status on a failed API call is surfaced alongside ``result``."""
        adapter = ClaudeSDKAdapter()
        result_msg = _result_message(
            is_error=True,
            result="Failed to authenticate. API Error: 401",
            api_error_status=401,
        )
        mock_client = self._client_yielding(result_msg)

        await adapter._process_response(mock_client, "room-123", mock_tools)

        errors = _error_events(mock_tools)
        assert len(errors) == 1
        assert "401" in errors[0]

    @pytest.mark.asyncio
    async def test_reports_missing_reply_when_no_terminal_tool_ran(self, mock_tools):
        """A clean turn that never called a Band tool must not go silent."""
        adapter = ClaudeSDKAdapter()
        result_msg = _result_message(is_error=False)
        mock_client = self._client_yielding(result_msg)

        await adapter._process_response(mock_client, "room-123", mock_tools)

        errors = _error_events(mock_tools)
        assert len(errors) == 1
        assert _MISSING_REPLY_TEXT in errors[0]

    @pytest.mark.asyncio
    async def test_no_error_reported_when_reply_tool_ran(self, mock_tools):
        """A turn that replied via band_send_message must stay quiet."""
        adapter = ClaudeSDKAdapter()
        turn = _tool_turn(_SEND_MESSAGE_MCP_NAME)
        result_msg = _result_message(is_error=False)
        mock_client = self._client_yielding(*turn, result_msg)

        await adapter._process_response(mock_client, "room-123", mock_tools)

        assert _error_events(mock_tools) == []

    @pytest.mark.asyncio
    async def test_assistant_carried_tool_result_also_counts(self, mock_tools):
        """A tool result arriving inside an assistant message (accepted
        defensively alongside the protocol's user-envelope shape) still counts
        as the turn's reply."""
        adapter = ClaudeSDKAdapter()
        assistant_msg = AssistantMessage(
            content=[
                ToolUseBlock(id="tool-1", name=_SEND_MESSAGE_MCP_NAME, input={}),
                ToolResultBlock(tool_use_id="tool-1", content="ok", is_error=False),
            ],
            model=_ANY_MODEL,
        )
        result_msg = _result_message(is_error=False)
        mock_client = self._client_yielding(assistant_msg, result_msg)

        await adapter._process_response(mock_client, "room-123", mock_tools)

        assert _error_events(mock_tools) == []

    @pytest.mark.asyncio
    async def test_execution_narration_covers_user_envelope_results(self, mock_tools):
        """With Emit.TOOL_CALLS on, a protocol-shaped turn narrates both the
        tool_call and the tool_result (which arrives in a user envelope)."""
        adapter = ClaudeSDKAdapter(emit=Emit.TOOL_CALLS)
        turn = _tool_turn(_SEND_MESSAGE_MCP_NAME)
        mock_client = self._client_yielding(*turn, _result_message())

        await adapter._process_response(mock_client, "room-123", mock_tools)

        narrated_types = _narrated_message_types(mock_tools)
        assert "tool_call" in narrated_types
        assert "tool_result" in narrated_types

    @pytest.mark.asyncio
    async def test_tool_result_payload_includes_name_and_is_error(self, mock_tools):
        """The tool_result event must carry NAME and IS_ERROR: parse_tool_result
        (converters/parsing.py) drops any payload missing a name outright, and
        every sibling adapter's tool_result payload sets both."""
        adapter = ClaudeSDKAdapter(emit=Emit.TOOL_CALLS)
        assistant_msg = AssistantMessage(
            content=[ToolUseBlock(id="tool-1", name=_SEND_MESSAGE_MCP_NAME, input={})],
            model=_ANY_MODEL,
        )
        user_msg = UserMessage(
            content=[
                ToolResultBlock(tool_use_id="tool-1", content="boom", is_error=True)
            ]
        )
        mock_client = self._client_yielding(assistant_msg, user_msg, _result_message())

        await adapter._process_response(mock_client, "room-123", mock_tools)

        payload = _tool_result_payload(mock_tools)
        assert payload[ToolEventKey.NAME] == "band_send_message"
        assert payload[ToolEventKey.IS_ERROR] is True

    @pytest.mark.asyncio
    async def test_no_error_reported_when_only_read_only_tool_ran(self, mock_tools):
        """A read-only lookup (e.g. band_list_contacts) is not a terminal reply."""
        adapter = ClaudeSDKAdapter()
        turn = _tool_turn("mcp__band__band_list_contacts")
        result_msg = _result_message(is_error=False)
        mock_client = self._client_yielding(*turn, result_msg)

        await adapter._process_response(mock_client, "room-123", mock_tools)

        errors = _error_events(mock_tools)
        assert len(errors) == 1
        assert _MISSING_REPLY_TEXT in errors[0]

    @pytest.mark.asyncio
    async def test_tool_result_with_is_error_none_counts_as_success(self, mock_tools):
        """The SDK's own convention: ``ToolResultBlock.is_error`` omitted from
        the CLI's JSON (``None``) means success, same as an explicit ``False``."""
        adapter = ClaudeSDKAdapter()
        assistant_msg = AssistantMessage(
            content=[
                ToolUseBlock(id="tool-1", name=_SEND_MESSAGE_MCP_NAME, input={}),
            ],
            model=_ANY_MODEL,
        )
        user_msg = UserMessage(
            content=[ToolResultBlock(tool_use_id="tool-1", content="ok", is_error=None)]
        )
        result_msg = _result_message(is_error=False)
        mock_client = self._client_yielding(assistant_msg, user_msg, result_msg)

        await adapter._process_response(mock_client, "room-123", mock_tools)

        assert _error_events(mock_tools) == []

    @pytest.mark.asyncio
    async def test_custom_terminal_tool_counts_as_reply(self, mock_tools):
        """A custom tool marked ``band_terminal=True`` must be recognized under
        its actual registered MCP name (get_custom_tool_name), not the Python
        handler's ``__name__`` — those two can differ."""

        class DeployInput(BaseModel):
            target: str

        def run_the_deploy_handler(args: DeployInput) -> str:
            return "deployed"

        run_the_deploy_handler.band_terminal = True
        adapter = ClaudeSDKAdapter(
            additional_tools=[(DeployInput, run_the_deploy_handler)]
        )
        # get_custom_tool_name(DeployInput) == "deploy" — deliberately unlike
        # the handler's own __name__, to prove the fix keys off the former.
        turn = _tool_turn("mcp__band__deploy")
        result_msg = _result_message(is_error=False)
        mock_client = self._client_yielding(*turn, result_msg)

        await adapter._process_response(mock_client, "room-123", mock_tools)

        assert _error_events(mock_tools) == []

    @pytest.mark.asyncio
    async def test_declined_reply_tool_does_not_also_report_missing_reply(
        self, mock_tools
    ):
        """A denied tool call already posts its own decline notice — the
        missing-reply guard must not pile a second, contradictory error on
        top of a turn the approval flow already explained."""
        adapter = ClaudeSDKAdapter(approval_mode="auto_decline")
        adapter._room_tools["room-123"] = mock_tools
        can_use_tool = adapter._make_can_use_tool("room-123")

        decision = await can_use_tool(
            _SEND_MESSAGE_MCP_NAME, {}, ToolPermissionContext(tool_use_id="tool-1")
        )
        assert isinstance(decision, PermissionResultDeny)

        # The declined call's result comes back as an error, same as a real
        # denial would surface through the SDK's own protocol.
        assistant_msg = AssistantMessage(
            content=[ToolUseBlock(id="tool-1", name=_SEND_MESSAGE_MCP_NAME, input={})],
            model=_ANY_MODEL,
        )
        user_msg = UserMessage(
            content=[
                ToolResultBlock(tool_use_id="tool-1", content="denied", is_error=True)
            ]
        )
        result_msg = _result_message(
            is_error=False,
            permission_denials=[_denial("tool-1", _SEND_MESSAGE_MCP_NAME)],
        )
        mock_client = self._client_yielding(assistant_msg, user_msg, result_msg)

        await adapter._process_response(mock_client, "room-123", mock_tools)

        assert _error_events(mock_tools) == []

    @pytest.mark.asyncio
    async def test_declined_side_tool_still_reports_missing_reply(self, mock_tools):
        """Declining a tool that would never have delivered the reply (e.g. a
        read-only lookup) does not explain a subsequent silent turn — only a
        decline notice for what would have been the reply tool does."""
        adapter = ClaudeSDKAdapter(approval_mode="auto_decline")
        adapter._room_tools["room-123"] = mock_tools
        can_use_tool = adapter._make_can_use_tool("room-123")

        decision = await can_use_tool(
            "mcp__band__band_list_contacts",
            {},
            ToolPermissionContext(tool_use_id="tool-1"),
        )
        assert isinstance(decision, PermissionResultDeny)

        assistant_msg = AssistantMessage(
            content=[
                ToolUseBlock(
                    id="tool-1", name="mcp__band__band_list_contacts", input={}
                )
            ],
            model=_ANY_MODEL,
        )
        user_msg = UserMessage(
            content=[
                ToolResultBlock(tool_use_id="tool-1", content="denied", is_error=True)
            ]
        )
        result_msg = _result_message(
            is_error=False,
            permission_denials=[_denial("tool-1", "mcp__band__band_list_contacts")],
        )
        mock_client = self._client_yielding(assistant_msg, user_msg, result_msg)

        await adapter._process_response(mock_client, "room-123", mock_tools)

        errors = _error_events(mock_tools)
        assert len(errors) == 1
        assert _MISSING_REPLY_TEXT in errors[0]

    @pytest.mark.asyncio
    async def test_notified_decline_does_not_leak_past_a_turn_that_replied(
        self, mock_tools
    ):
        """A side tool declined-and-notified in a turn that still replies via
        band_send_message must not leave a stale notified-decline entry for
        this room once the turn completes — otherwise it grows unbounded
        over the life of a room that declines side tools but keeps
        answering normally."""
        adapter = ClaudeSDKAdapter(approval_mode="auto_decline")
        adapter._room_tools["room-123"] = mock_tools
        can_use_tool = adapter._make_can_use_tool("room-123")

        decision = await can_use_tool(
            "mcp__band__band_list_contacts",
            {},
            ToolPermissionContext(tool_use_id="tool-1"),
        )
        assert isinstance(decision, PermissionResultDeny)
        assert "tool-1" in adapter._notified_declines["room-123"]

        turn = _tool_turn(_SEND_MESSAGE_MCP_NAME)
        result_msg = _result_message(
            is_error=False,
            permission_denials=[_denial("tool-1", "mcp__band__band_list_contacts")],
        )
        mock_client = self._client_yielding(*turn, result_msg)

        await adapter._process_response(mock_client, "room-123", mock_tools)

        assert _error_events(mock_tools) == []
        assert "room-123" not in adapter._notified_declines

    @pytest.mark.asyncio
    async def test_user_envelope_tool_use_is_tracked(self, mock_tools):
        """A tool_use block carried in a user-type envelope (e.g. a
        subagent's nested call) must be tracked the same as one carried by
        an assistant message, not silently dropped."""
        adapter = ClaudeSDKAdapter()
        user_msg = UserMessage(
            content=[
                ToolUseBlock(id="tool-1", name=_SEND_MESSAGE_MCP_NAME, input={}),
                ToolResultBlock(tool_use_id="tool-1", content="ok", is_error=False),
            ]
        )
        result_msg = _result_message(is_error=False)
        mock_client = self._client_yielding(user_msg, result_msg)

        await adapter._process_response(mock_client, "room-123", mock_tools)

        assert _error_events(mock_tools) == []

    @pytest.mark.asyncio
    async def test_stream_eof_after_delivered_reply_completes_the_turn(
        self, mock_tools
    ):
        """The CLI dying between a delivered reply and its ResultMessage must
        not fail the turn: the reply already reached the room, and a failed
        turn would make the runtime redeliver the message and answer the user
        twice. No exception, no room-visible error."""
        adapter = ClaudeSDKAdapter()
        turn = _tool_turn(_SEND_MESSAGE_MCP_NAME)
        mock_client = self._client_yielding(*turn)  # no ResultMessage

        await adapter._process_response(mock_client, "room-123", mock_tools)

        assert _error_events(mock_tools) == []

    @pytest.mark.asyncio
    async def test_stream_eof_without_reply_still_fails_the_turn(self, mock_tools):
        """An EOF on a turn that delivered nothing is a dead client: it must
        reach the dead-client recovery path, not return as a success."""
        adapter = ClaudeSDKAdapter()
        assistant_msg = AssistantMessage(
            content=[ToolUseBlock(id="tool-1", name=_SEND_MESSAGE_MCP_NAME, input={})],
            model=_ANY_MODEL,
        )
        mock_client = self._client_yielding(assistant_msg)  # no result, no reply

        with pytest.raises(CLIConnectionError, match="ended without a result"):
            await adapter._process_response(mock_client, "room-123", mock_tools)

    @pytest.mark.asyncio
    async def test_replayed_tool_result_from_previous_turn_counts_as_reply(
        self, mock_tools
    ):
        """A resumed session can replay a tool result whose tool_use streamed
        in an earlier, truncated turn. The pending-call map is room-scoped so
        that result still resolves to its tool name and counts as the turn's
        answer — no spurious missing-reply error on an answered turn."""
        adapter = ClaudeSDKAdapter()
        tool_use_turn = AssistantMessage(
            content=[ToolUseBlock(id="tool-1", name=_SEND_MESSAGE_MCP_NAME, input={})],
            model=_ANY_MODEL,
        )
        dead_client = self._client_yielding(tool_use_turn)  # dies before result
        with pytest.raises(CLIConnectionError, match="ended without a result"):
            await adapter._process_response(dead_client, "room-123", mock_tools)

        replayed_result = UserMessage(
            content=[
                ToolResultBlock(tool_use_id="tool-1", content="ok", is_error=False)
            ]
        )
        resumed_client = self._client_yielding(
            replayed_result, _result_message(is_error=False)
        )
        await adapter._process_response(resumed_client, "room-123", mock_tools)

        assert _error_events(mock_tools) == []

    @pytest.mark.asyncio
    async def test_band_tool_error_string_is_not_terminal_work(self, mock_tools):
        """A Band tool wrapper that caught an exception returns an "Error "
        string without setting is_error; that reply never reached the room,
        so the missing-reply guard must still fire."""
        adapter = ClaudeSDKAdapter()
        assistant_msg = AssistantMessage(
            content=[ToolUseBlock(id="tool-1", name=_SEND_MESSAGE_MCP_NAME, input={})],
            model=_ANY_MODEL,
        )
        failed_result = UserMessage(
            content=[
                ToolResultBlock(
                    tool_use_id="tool-1", content="Error sending message", is_error=None
                )
            ]
        )
        mock_client = self._client_yielding(
            assistant_msg, failed_result, _result_message(is_error=False)
        )

        await adapter._process_response(mock_client, "room-123", mock_tools)

        errors = _error_events(mock_tools)
        assert len(errors) == 1
        assert _MISSING_REPLY_TEXT in errors[0]

    @pytest.mark.asyncio
    async def test_unserializable_tool_payload_does_not_abort_the_turn(
        self, mock_tools
    ):
        """Narration payloads are serialized lazily, past the emit gate and
        inside its try — a tool result the default no-emit adapter can't
        json.dumps must cost nothing and never abort the turn."""
        adapter = ClaudeSDKAdapter()
        turn = _tool_turn(_SEND_MESSAGE_MCP_NAME)
        turn[1].content[0].content = object()  # not JSON-serializable

        mock_client = self._client_yielding(*turn, _result_message(is_error=False))

        await adapter._process_response(mock_client, "room-123", mock_tools)

        assert _error_events(mock_tools) == []

    @pytest.mark.asyncio
    async def test_silent_auto_decline_still_reports_missing_reply(self, mock_tools):
        """``approval_text_notifications=False`` means auto_decline denies a
        tool call without ever telling the room why — so the missing-reply
        guard must still fire; the turn cannot be silently marked as already
        explained when no explanation was actually delivered."""
        adapter = ClaudeSDKAdapter(
            approval_mode="auto_decline", approval_text_notifications=False
        )
        adapter._room_tools["room-123"] = mock_tools
        can_use_tool = adapter._make_can_use_tool("room-123")

        decision = await can_use_tool(
            _SEND_MESSAGE_MCP_NAME, {}, ToolPermissionContext(tool_use_id="tool-1")
        )
        assert isinstance(decision, PermissionResultDeny)
        mock_tools.send_message.assert_not_awaited()

        assistant_msg = AssistantMessage(
            content=[ToolUseBlock(id="tool-1", name=_SEND_MESSAGE_MCP_NAME, input={})],
            model=_ANY_MODEL,
        )
        user_msg = UserMessage(
            content=[
                ToolResultBlock(tool_use_id="tool-1", content="denied", is_error=True)
            ]
        )
        # The CLI still reports the denial (see permission_denials) even
        # though our own notification never reached the room — proves the
        # guard is gated on delivery, not merely on the CLI's own record.
        result_msg = _result_message(
            is_error=False,
            permission_denials=[_denial("tool-1", _SEND_MESSAGE_MCP_NAME)],
        )
        mock_client = self._client_yielding(assistant_msg, user_msg, result_msg)

        await adapter._process_response(mock_client, "room-123", mock_tools)

        errors = _error_events(mock_tools)
        assert len(errors) == 1
        assert _MISSING_REPLY_TEXT in errors[0]

    @pytest.mark.asyncio
    async def test_declined_marker_does_not_leak_into_next_turn(self, mock_tools):
        """A decline from a turn that then dies before its ResultMessage must
        not leave the room's next, unrelated turn looking pre-explained, even
        though nothing ever clears the leftover notified-tool_use_id record
        (a real CLI never reuses a tool_use_id, so the next turn's own
        ``permission_denials`` can never accidentally match it)."""
        adapter = ClaudeSDKAdapter(approval_mode="auto_decline")
        adapter._room_tools["room-123"] = mock_tools
        can_use_tool = adapter._make_can_use_tool("room-123")

        decision = await can_use_tool(
            _SEND_MESSAGE_MCP_NAME, {}, ToolPermissionContext(tool_use_id="tool-1")
        )
        assert isinstance(decision, PermissionResultDeny)

        dead_turn_client = self._client_yielding()  # ends with no ResultMessage
        with pytest.raises(CLIConnectionError, match="ended without a result"):
            await adapter._process_response(dead_turn_client, "room-123", mock_tools)

        # A fresh, unrelated turn: no tool activity, no permission_denials.
        next_turn_client = self._client_yielding(_result_message(is_error=False))
        await adapter._process_response(next_turn_client, "room-123", mock_tools)

        errors = _error_events(mock_tools)
        assert len(errors) == 1
        assert _MISSING_REPLY_TEXT in errors[0]

    @pytest.mark.asyncio
    async def test_undelivered_approval_prompt_still_reports_missing_reply(
        self, mock_tools
    ):
        """Manual mode's approval prompt itself failed to send — the room got
        no explanation at all — so the missing-reply guard must still fire
        even though the tool call was denied."""
        adapter = ClaudeSDKAdapter(approval_mode="manual", approval_wait_timeout_s=5.0)
        mock_tools.send_message = AsyncMock(side_effect=RuntimeError("network down"))
        adapter._room_tools["room-123"] = mock_tools
        can_use_tool = adapter._make_can_use_tool("room-123")

        decision = await can_use_tool(
            _SEND_MESSAGE_MCP_NAME, {}, ToolPermissionContext()
        )
        assert isinstance(decision, PermissionResultDeny)

        result_msg = _result_message(is_error=False)
        mock_client = self._client_yielding(result_msg)

        await adapter._process_response(mock_client, "room-123", mock_tools)

        errors = _error_events(mock_tools)
        assert len(errors) == 1
        assert _MISSING_REPLY_TEXT in errors[0]

    @pytest.mark.asyncio
    async def test_undelivered_timeout_notice_still_reports_missing_reply(
        self, mock_tools
    ):
        """An approval that times out into a decline suppresses the
        missing-reply guard only when its timeout notice actually reached the
        room; here the prompt sends fine but the timeout notice fails, so the
        guard must still fire."""
        adapter = ClaudeSDKAdapter(
            approval_mode="manual",
            approval_wait_timeout_s=0.05,
            approval_timeout_decision="decline",
        )
        mock_tools.send_message = AsyncMock(
            side_effect=[{"status": "sent"}, RuntimeError("network down")]
        )
        adapter._room_tools["room-123"] = mock_tools
        can_use_tool = adapter._make_can_use_tool("room-123")

        decision = await can_use_tool(
            _SEND_MESSAGE_MCP_NAME, {}, ToolPermissionContext()
        )
        assert isinstance(decision, PermissionResultDeny)

        result_msg = _result_message(is_error=False)
        mock_client = self._client_yielding(result_msg)

        await adapter._process_response(mock_client, "room-123", mock_tools)

        errors = _error_events(mock_tools)
        assert len(errors) == 1
        assert _MISSING_REPLY_TEXT in errors[0]


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

    def test_command_after_leading_mention_block(self):
        """A delivered reply arrives with the platform's ``@handle`` mention
        prepended (a reply must mention the agent), so the command follows it.
        The block is stripped so the command still matches -- without this the
        chat-approval reply was silently forwarded to the model as a prompt."""
        assert ClaudeSDKAdapter._extract_command("@alex/claude /approve a-1") == (
            "approve",
            "a-1",
        )
        # A human typing an inline mention doubles the token; still recognized.
        assert ClaudeSDKAdapter._extract_command(
            "@alex/claude @alex/claude /decline a-2"
        ) == (
            "decline",
            "a-2",
        )

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
        register_pending_approval(
            adapter_with_approval, tool_input={"command": "ls"}, summary="Bash: `ls`"
        )
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
        future = register_pending_approval(adapter_with_approval)
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
        future = register_pending_approval(adapter_with_approval)
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
    async def test_decline_resolution_notice_failure_does_not_claim_delivery(
        self, adapter_with_approval, mock_tools, sender
    ):
        """When the '/decline resolved as **decline**' notice itself fails to
        send, the future must resolve to _FORCED_DECLINE, not plain
        "decline" — otherwise _resolve_manual_approval's decision_raw ==
        "decline" check would wrongly treat the tool call as having been
        explained to the room and suppress the missing-reply guard."""
        future = register_pending_approval(adapter_with_approval)
        mock_tools.send_message = AsyncMock(side_effect=RuntimeError("network down"))
        await adapter_with_approval._handle_approval_command(
            tools=mock_tools,
            room_id="room-1",
            command="decline",
            args="a-1",
            sender=sender,
        )
        assert future.done()
        assert future.result() == _FORCED_DECLINE

    @pytest.mark.asyncio
    async def test_approve_resolution_notice_failure_still_accepts(
        self, adapter_with_approval, mock_tools, sender
    ):
        """An approve's confirmation notice is best-effort: a failed send must
        not turn an approved tool call into a decline."""
        future = register_pending_approval(adapter_with_approval)
        mock_tools.send_message = AsyncMock(side_effect=RuntimeError("network down"))
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
    async def test_approve_single_pending_no_token(
        self, adapter_with_approval, mock_tools, sender
    ):
        """When only 1 pending, /approve without token should resolve it."""
        future = register_pending_approval(adapter_with_approval)
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
        register_pending_approval(adapter_with_approval, token="a-1", tool_name="Bash")
        register_pending_approval(adapter_with_approval, token="a-2", tool_name="Edit")
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
        register_pending_approval(adapter_with_approval)
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
        future = register_pending_approval(adapter)
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
        future = register_pending_approval(adapter)
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
        future = register_pending_approval(adapter)
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

        # Pre-populate a pending approval
        future = register_pending_approval(adapter)

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
                "band.adapters.claude_sdk.ClaudeSessionManager",
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
                "band.adapters.claude_sdk.ClaudeSessionManager",
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
                "band.adapters.claude_sdk.ClaudeSessionManager",
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

        with patch("band.adapters.claude_sdk.ClaudeSessionManager") as mock_cls:
            mock_cls.return_value = MagicMock()
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs.get("can_use_tool_factory") is not None

    @pytest.mark.asyncio
    async def test_no_factory_when_approval_disabled(self):
        adapter = ClaudeSDKAdapter()  # approval_mode=None

        with patch("band.adapters.claude_sdk.ClaudeSessionManager") as mock_cls:
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

        with patch("band.adapters.claude_sdk.ClaudeSessionManager") as mock_cls:
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

        with patch("band.adapters.claude_sdk.ClaudeSessionManager") as mock_cls:
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

        future = register_pending_approval(adapter)

        await adapter.on_cleanup("room-1")

        assert future.done()
        assert future.result() == _FORCED_DECLINE
        assert "room-1" not in adapter._pending_approvals

    @pytest.mark.asyncio
    async def test_cleanup_all_declines_all_rooms(self):
        """cleanup_all() should decline all pending approvals across rooms."""
        adapter = ClaudeSDKAdapter(approval_mode="manual")
        adapter._session_manager = AsyncMock()

        f1 = register_pending_approval(adapter, room_id="room-1", tool_name="Bash")
        f2 = register_pending_approval(
            adapter, room_id="room-2", token="a-2", tool_name="Edit"
        )

        await adapter.cleanup_all()

        assert f1.result() == _FORCED_DECLINE
        assert f2.result() == _FORCED_DECLINE
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

        # Pre-populate one pending approval
        old_future = register_pending_approval(
            adapter,
            tool_name="Old",
            created_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )

        # Now trigger a new approval (should evict old one)
        callback = adapter._make_can_use_tool("room-1")
        await callback("New", {}, ToolPermissionContext())

        # Old future should have been evicted and declined
        assert old_future.done()
        assert old_future.result() == _FORCED_DECLINE

    @pytest.mark.asyncio
    async def test_evicted_approval_is_not_recorded_as_notified(self, mock_tools):
        """Eviction force-resolves the oldest pending approval, but never
        posts a room-visible notice for that specific call — only the
        original 'Approval requested' prompt, sent when it was first created.
        If the evicted call were recorded as notified and it happened to be
        the reply tool, the turn would end completely silent: no reply (the
        tool was declined) and no error (the guard wrongly suppressed)."""
        adapter = ClaudeSDKAdapter(
            approval_mode="manual",
            max_pending_approvals_per_room=1,
            approval_wait_timeout_s=0.1,
        )
        adapter._room_tools["room-1"] = mock_tools
        adapter._room_last_sender["room-1"] = {"id": "u1", "name": "Bob"}
        callback = adapter._make_can_use_tool("room-1")

        async def request_first():
            return await callback(
                _SEND_MESSAGE_MCP_NAME, {}, ToolPermissionContext(tool_use_id="tool-1")
            )

        first_task = asyncio.create_task(request_first())
        await asyncio.sleep(0.02)  # let the first approval register + prompt

        second_result = await callback(
            "Bash", {"command": "ls"}, ToolPermissionContext(tool_use_id="tool-2")
        )
        first_result = await first_task

        assert isinstance(first_result, PermissionResultDeny)
        assert isinstance(second_result, PermissionResultDeny)
        assert "tool-1" not in adapter._notified_declines.get("room-1", set())


class TestSendMessageDedupWiring:
    """Tests that on_message wires the dedup shim correctly."""

    @pytest.mark.asyncio
    async def test_wraps_tools_by_default(self, sample_message, mock_tools):
        """By default, on_message stores a DedupingAgentTools wrapper."""
        from band.integrations.claude_sdk.dedup_tools import DedupingAgentTools

        adapter = ClaudeSDKAdapter()
        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)

        with (
            patch(
                "band.adapters.claude_sdk.ClaudeSessionManager",
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
                room_id="room-1",
            )

        stored = adapter._room_tools["room-1"]
        assert isinstance(stored, DedupingAgentTools)
        assert stored._inner is mock_tools

    @pytest.mark.asyncio
    async def test_ttl_zero_disables_wrapping(self, sample_message, mock_tools):
        """ttl=0 keeps the raw tools — no shim — for operators who opt out."""
        from band.integrations.claude_sdk.dedup_tools import DedupingAgentTools

        adapter = ClaudeSDKAdapter(send_message_dedup_ttl_seconds=0)
        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)

        with (
            patch(
                "band.adapters.claude_sdk.ClaudeSessionManager",
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
                room_id="room-1",
            )

        stored = adapter._room_tools["room-1"]
        assert stored is mock_tools
        assert not isinstance(stored, DedupingAgentTools)

    def test_negative_ttl_rejected(self):
        with pytest.raises(ValueError):
            ClaudeSDKAdapter(send_message_dedup_ttl_seconds=-1)

    @pytest.mark.asyncio
    async def test_duplicate_mcp_calls_collapse_via_room_tools(
        self, sample_message, mock_tools
    ):
        """End-to-end: two MCP-style calls through the stored wrapper hit
        the inner send_message exactly once."""
        adapter = ClaudeSDKAdapter()
        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)

        with (
            patch(
                "band.adapters.claude_sdk.ClaudeSessionManager",
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
                room_id="room-1",
            )

        # Simulate the MCP backend resolving room_tools.get("room-1") on
        # each tool call (exactly what _create_mcp_backend wires up).
        stored = adapter._room_tools["room-1"]
        await stored.send_message("hello", ["alice"])
        await stored.send_message("hello", ["alice"])

        assert mock_tools.send_message.await_count == 1

    @pytest.mark.asyncio
    async def test_wrapper_persists_across_on_message_calls(self, sample_message):
        """Cross-turn regression: the dominant pattern is a duplicate
        tool call arriving *after* the original turn's Complete event. Since
        SimpleAdapter constructs a fresh AgentTools per inbound message, a
        wrapper rebuilt per on_message would drop the cache and let the
        post-Complete duplicate through.

        Drive two on_message calls with different inner tools (mirroring
        AgentTools.from_context being called per message), then fire two
        identical send_message calls against the stored wrapper — one before
        and one after the second on_message — and assert the duplicate is
        suppressed across the turn boundary.
        """
        from band.integrations.claude_sdk.dedup_tools import DedupingAgentTools

        adapter = ClaudeSDKAdapter()
        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)

        tools_turn1 = MagicMock()
        tools_turn1.send_message = AsyncMock(return_value={"id": "m1"})
        tools_turn2 = MagicMock()
        tools_turn2.send_message = AsyncMock(return_value={"id": "m2"})

        with (
            patch(
                "band.adapters.claude_sdk.ClaudeSessionManager",
                return_value=mock_manager,
            ),
            patch.object(adapter, "_process_response", new_callable=AsyncMock),
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )
            await adapter.on_message(
                msg=sample_message,
                tools=tools_turn1,
                history=ClaudeSDKSessionState(text=""),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )
            wrapper_after_turn1 = adapter._room_tools["room-1"]
            assert isinstance(wrapper_after_turn1, DedupingAgentTools)

            # Original send during turn 1 (via the MCP-resolved wrapper).
            await wrapper_after_turn1.send_message("hi", ["alice"])
            assert tools_turn1.send_message.await_count == 1

            # Turn 2 arrives with a distinct platform message id;
            # SimpleAdapter builds a fresh AgentTools.
            turn2_message = PlatformMessage(
                id="msg-456",
                room_id=sample_message.room_id,
                content=sample_message.content,
                sender_id=sample_message.sender_id,
                sender_type=sample_message.sender_type,
                sender_name=sample_message.sender_name,
                message_type=sample_message.message_type,
                metadata=sample_message.metadata,
                created_at=sample_message.created_at,
            )
            await adapter.on_message(
                msg=turn2_message,
                tools=tools_turn2,
                history=ClaudeSDKSessionState(text=""),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-1",
            )

            # SAME wrapper instance must still be stored — only _inner swapped.
            wrapper_after_turn2 = adapter._room_tools["room-1"]
            assert wrapper_after_turn2 is wrapper_after_turn1
            assert wrapper_after_turn2._inner is tools_turn2

            # The lingering duplicate from turn 1 fires now. It must hit
            # the cache and NOT POST through tools_turn2.
            await wrapper_after_turn2.send_message("hi", ["alice"])
            assert tools_turn2.send_message.await_count == 0
            # And tools_turn1 was not called again either.
            assert tools_turn1.send_message.await_count == 1

    @pytest.mark.asyncio
    async def test_on_cleanup_evicts_wrapper(self, sample_message, mock_tools):
        """on_cleanup must remove the wrapper so a re-entered room rebuilds
        fresh state (and so the cache cannot leak across detached sessions)."""
        adapter = ClaudeSDKAdapter()
        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)
        mock_manager.cleanup_session = AsyncMock()

        with (
            patch(
                "band.adapters.claude_sdk.ClaudeSessionManager",
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
                room_id="room-1",
            )
            assert "room-1" in adapter._room_tools

            await adapter.on_cleanup("room-1")
            assert "room-1" not in adapter._room_tools

    @pytest.mark.asyncio
    async def test_distinct_rooms_get_distinct_wrappers(self, sample_message):
        """Per-room isolation: identical ``(content, mentions)`` in two
        different rooms must POST twice — once per room — because rooms
        are independent conversations and the dedup window is a per-room
        guard, not a global one.

        Pins ``_room_tools`` keying behavior so a future refactor (e.g.
        a per-session or singleton tools cache) cannot silently turn the
        dedup wrapper into a tenant-wide message suppressor.
        """
        from band.integrations.claude_sdk.dedup_tools import DedupingAgentTools

        adapter = ClaudeSDKAdapter()
        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)

        tools_a = MagicMock()
        tools_a.send_message = AsyncMock(return_value={"id": "a"})
        tools_b = MagicMock()
        tools_b.send_message = AsyncMock(return_value={"id": "b"})

        with (
            patch(
                "band.adapters.claude_sdk.ClaudeSessionManager",
                return_value=mock_manager,
            ),
            patch.object(adapter, "_process_response", new_callable=AsyncMock),
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )
            await adapter.on_message(
                msg=sample_message,
                tools=tools_a,
                history=ClaudeSDKSessionState(text=""),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-a",
            )
            await adapter.on_message(
                msg=sample_message,
                tools=tools_b,
                history=ClaudeSDKSessionState(text=""),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-b",
            )

        wrapper_a = adapter._room_tools["room-a"]
        wrapper_b = adapter._room_tools["room-b"]
        assert isinstance(wrapper_a, DedupingAgentTools)
        assert isinstance(wrapper_b, DedupingAgentTools)
        assert wrapper_a is not wrapper_b

        # Identical sends in two distinct rooms must both reach their
        # respective inner tools — dedup is per-room, not per-tenant.
        await wrapper_a.send_message("hello", ["alice"])
        await wrapper_b.send_message("hello", ["alice"])
        assert tools_a.send_message.await_count == 1
        assert tools_b.send_message.await_count == 1

    @pytest.mark.asyncio
    async def test_update_inner_skipped_when_tools_identity_unchanged(
        self, sample_message
    ):
        """When the runtime hands the adapter the same tools object twice,
        ``update_inner`` is a no-op and must be skipped — otherwise we'd
        briefly contend on the wrapper's lock for no reason."""
        from band.integrations.claude_sdk.dedup_tools import DedupingAgentTools

        adapter = ClaudeSDKAdapter()
        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)

        tools = MagicMock()
        tools.send_message = AsyncMock(return_value={"id": "x"})

        with (
            patch(
                "band.adapters.claude_sdk.ClaudeSessionManager",
                return_value=mock_manager,
            ),
            patch.object(adapter, "_process_response", new_callable=AsyncMock),
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )
            await adapter.on_message(
                msg=sample_message,
                tools=tools,
                history=ClaudeSDKSessionState(text=""),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )
            wrapper = adapter._room_tools["room-1"]
            assert isinstance(wrapper, DedupingAgentTools)

            with patch.object(
                wrapper, "update_inner", new_callable=AsyncMock
            ) as mock_update:
                await adapter.on_message(
                    msg=sample_message,
                    tools=tools,  # SAME instance
                    history=ClaudeSDKSessionState(text=""),
                    participants_msg=None,
                    contacts_msg=None,
                    is_session_bootstrap=False,
                    room_id="room-1",
                )
                mock_update.assert_not_awaited()
