"""Tests for ACPClientAdapter."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from band.integrations.acp.client_adapter import ACPClientAdapter, _resolve_launcher
from band.integrations.acp.client_profiles import CursorACPClientProfile
from band.integrations.acp.client_runtime import ACPCollectingClient
from band.integrations.acp.client_types import (
    ACPClientSessionState,
    BandACPClient,
)
from band.integrations.acp.types import CollectedChunk
from band.testing import FakeAgentTools

from .conftest import make_platform_message


class TestACPClientAdapterInit:
    """Tests for ACPClientAdapter initialization."""

    def test_init_string_command(self) -> None:
        """Should accept string command."""
        adapter = ACPClientAdapter(command="codex")
        assert adapter._command == ["codex"]

    def test_init_list_command(self) -> None:
        """Should accept list command."""
        adapter = ACPClientAdapter(command=["gemini", "cli"])
        assert adapter._command == ["gemini", "cli"]

    def test_init_default_values(self) -> None:
        """Should initialize with default values."""
        adapter = ACPClientAdapter(command="codex")
        assert adapter._cwd == os.path.abspath(".")
        assert adapter._env is None
        assert adapter._mcp_servers == []
        assert adapter._runtime._conn is None
        assert adapter._runtime._client is None
        assert adapter._room_to_session == {}
        assert adapter._room_tools == {}
        assert adapter._band_mcp_backend is None
        assert adapter._band_mcp_server is None

    def test_init_codex_acp_uses_absolute_default_cwd(self) -> None:
        """Should normalize codex-acp default cwd to an absolute path."""
        adapter = ACPClientAdapter(command="codex-acp")
        assert adapter._cwd == os.path.abspath(".")

    def test_init_npx_codex_acp_uses_absolute_default_cwd(self) -> None:
        """Should normalize npx codex-acp default cwd to an absolute path."""
        adapter = ACPClientAdapter(command=["npx", "@zed-industries/codex-acp"])
        assert adapter._cwd == os.path.abspath(".")

    def test_init_with_custom_values(self) -> None:
        """Should accept custom configuration."""
        adapter = ACPClientAdapter(
            command="codex",
            env={"API_KEY": "test"},
            cwd="/workspace",
            mcp_servers=[{"type": "stdio", "command": "server"}],
        )
        assert adapter._cwd == os.path.abspath("/workspace")
        assert adapter._env == {"API_KEY": "test"}
        assert len(adapter._mcp_servers) == 1

    def test_init_sets_history_converter(self) -> None:
        """Should set ACPClientHistoryConverter."""
        adapter = ACPClientAdapter(command="codex")
        assert adapter.history_converter is not None

    def test_init_resolves_custom_cwd_to_absolute_path(self) -> None:
        """Should normalize explicit cwd values to absolute paths."""
        adapter = ACPClientAdapter(command="codex", cwd="examples")
        assert adapter._cwd == os.path.abspath("examples")

    def test_init_rejects_invalid_rest_url(self) -> None:
        """Should fail fast on invalid Band base URLs."""
        with pytest.raises(ValueError, match="rest_url"):
            ACPClientAdapter(command="codex", rest_url="ftp://invalid")


class TestACPClientAdapterTransport:
    """Tests for stdio-vs-TCP transport selection and validation."""

    def test_tcp_construction_sets_host_port_and_empty_command(self) -> None:
        """TCP transport records host/port and spawns no subprocess command."""
        adapter = ACPClientAdapter(host="10.0.0.5", port=8080)
        assert adapter._host == "10.0.0.5"
        assert adapter._port == 8080
        assert adapter._command == []

    def test_stdio_construction_leaves_host_port_unset(self) -> None:
        adapter = ACPClientAdapter(command="copilot")
        assert adapter._host is None
        assert adapter._port is None
        assert adapter._command == ["copilot"]

    def test_requires_a_transport(self) -> None:
        """Neither command nor host/port is a misconfiguration."""
        with pytest.raises(ValueError, match="command .*or host"):
            ACPClientAdapter()

    def test_empty_command_is_rejected(self) -> None:
        """An empty command is not a usable transport (would crash at spawn)."""
        with pytest.raises(ValueError, match="command .*or host"):
            ACPClientAdapter(command=[])
        with pytest.raises(ValueError, match="command .*or host"):
            ACPClientAdapter(command="")

    def test_rejects_command_and_tcp_together(self) -> None:
        with pytest.raises(ValueError, match="not both"):
            ACPClientAdapter(command="copilot", host="10.0.0.5", port=8080)

    def test_tcp_requires_both_host_and_port(self) -> None:
        with pytest.raises(ValueError, match="both host and port"):
            ACPClientAdapter(host="10.0.0.5")
        with pytest.raises(ValueError, match="both host and port"):
            ACPClientAdapter(port=8080)

    @pytest.mark.asyncio
    async def test_injected_spawn_process_wins_over_defaults(
        self, make_acp_transport
    ) -> None:
        """An explicit spawn_process is used even for a TCP-configured adapter."""
        transport = make_acp_transport()
        adapter = ACPClientAdapter(host="10.0.0.5", port=8080, spawn_process=transport)

        await adapter.on_started("Copilot", "Copilot over TCP")

        assert adapter._runtime._conn is transport.conn
        # TCP still forwards no positional command.
        args, _ = transport.last_call
        assert args == ()


class TestACPClientAdapterShutdown:
    """Graceful shutdown must release the adapter-wide subprocess/TCP connection.

    ``Agent.stop()`` invokes ``cleanup_all()`` (not ``stop()``), so the teardown has
    to hang off ``cleanup_all`` or the transport spawned in ``on_started`` leaks.
    """

    @pytest.mark.asyncio
    async def test_cleanup_all_tears_down_the_transport(
        self, make_acp_transport
    ) -> None:
        adapter = ACPClientAdapter(
            command="codex", spawn_process=make_acp_transport(), inject_band_tools=False
        )
        await adapter.on_started("Codex", "bridge")
        assert adapter._runtime._ctx is not None  # transport is up

        await adapter.cleanup_all()  # the hook Agent.stop() calls on graceful shutdown

        assert adapter._runtime._ctx is None  # ...and released
        assert adapter._runtime._conn is None


class TestACPClientAdapterLocalMcpConfig:
    """Tests for local Band MCP injection."""

    @pytest.mark.asyncio
    async def test_get_or_start_band_mcp_server_returns_http_config(self) -> None:
        """Should expose a shared local HTTP MCP server for Band tools."""
        adapter = ACPClientAdapter(command="codex")
        mock_server = MagicMock(http_url="http://127.0.0.1:50000/mcp")
        backend = MagicMock(local_server=mock_server)

        with patch(
            "band.integrations.acp.client_adapter.create_band_mcp_backend",
            new=AsyncMock(return_value=backend),
        ):
            server = await adapter._get_or_start_band_mcp_server()

        assert server.name == "band"
        assert server.url == "http://127.0.0.1:50000/mcp"
        assert server.headers == []
        assert server.type == "http"
        assert adapter._band_mcp_backend is backend
        assert adapter._band_mcp_server is mock_server

    @pytest.mark.asyncio
    async def test_get_or_start_band_mcp_server_returns_sse_config(self) -> None:
        """Should expose shared SSE when the ACP agent only supports SSE MCP."""
        adapter = ACPClientAdapter(command="codex")
        adapter._runtime._agent_mcp_transport = "sse"
        mock_server = MagicMock(sse_url="http://127.0.0.1:50000/sse")
        backend = MagicMock(local_server=mock_server)

        with patch(
            "band.integrations.acp.client_adapter.create_band_mcp_backend",
            new=AsyncMock(return_value=backend),
        ):
            server = await adapter._get_or_start_band_mcp_server()

        assert server.name == "band"
        assert server.url == "http://127.0.0.1:50000/sse"
        assert server.headers == []
        assert server.type == "sse"
        assert adapter._band_mcp_backend is backend
        assert adapter._band_mcp_server is mock_server

    @pytest.mark.asyncio
    async def test_get_or_start_band_mcp_server_reuses_shared_server(self) -> None:
        """Should start the shared Band MCP server only once."""
        adapter = ACPClientAdapter(command="codex")
        mock_server = MagicMock(http_url="http://127.0.0.1:50000/mcp")
        backend = MagicMock(local_server=mock_server)

        with patch(
            "band.integrations.acp.client_adapter.create_band_mcp_backend",
            new=AsyncMock(return_value=backend),
        ) as mock_create_backend:
            first = await adapter._get_or_start_band_mcp_server()
            second = await adapter._get_or_start_band_mcp_server()

        assert first.url == second.url
        mock_create_backend.assert_awaited_once()

    def test_build_system_context_mentions_band_tools(self) -> None:
        """Should keep ACP system context minimal and room-aware."""
        adapter = ACPClientAdapter(command="codex")
        adapter.agent_name = "ACP Bridge"
        adapter.agent_description = "Bridge to ACP agents"
        msg = make_platform_message(
            "Hello",
            room_id="room-123",
            sender_id="user-123",
            sender_name="Pat",
        )

        system_context = adapter._build_system_context("room-123", msg)

        assert "Band tools" in system_context
        assert "Current room_id: room-123" in system_context
        assert "Current requester name: Pat" in system_context
        assert "Use each MCP tool's schema" in system_context

    def test_build_system_context_defers_to_external_mcp_tool_schema(self) -> None:
        """The room value is supplied without assuming a remote tool's field name."""
        adapter = ACPClientAdapter(command="codex", inject_band_tools=False)
        adapter.agent_name = "ACP Bridge"
        adapter.agent_description = "Bridge to ACP agents"
        msg = make_platform_message("Hello", room_id="room-123")

        system_context = adapter._build_system_context("room-123", msg)

        assert "Use each MCP tool's schema" in system_context
        assert "must include room_id" not in system_context


class TestACPClientAdapterOnStarted:
    """Tests for ACPClientAdapter.on_started().

    These inject a :class:`FakeSpawn` transport (the ``make_acp_transport`` fixture)
    through the adapter's ``spawn_process`` seam rather than patching module globals,
    so the real ACPRuntime start path runs against a scripted connection.
    """

    @pytest.mark.asyncio
    async def test_on_started_spawns_process(self, make_acp_transport) -> None:
        """Should spawn ACP process and initialize connection."""
        transport = make_acp_transport()
        adapter = ACPClientAdapter(command="codex", spawn_process=transport)

        await adapter.on_started("Codex Bridge", "Bridge to Codex")

        assert adapter._runtime._conn is transport.conn
        transport.conn.initialize.assert_awaited_once_with(protocol_version=1)

    @pytest.mark.asyncio
    async def test_on_started_uses_large_stdio_limit(self, make_acp_transport) -> None:
        """Should raise the stdio reader limit for large ACP JSON frames."""
        transport = make_acp_transport()
        adapter = ACPClientAdapter(
            command=["npx", "@zed-industries/codex-acp"],
            spawn_process=transport,
        )

        await adapter.on_started("Codex Bridge", "Bridge to Codex")

        assert transport.last_kwargs["transport_kwargs"] == {"limit": 16 * 1024 * 1024}

    @pytest.mark.asyncio
    async def test_on_started_forwards_command_positionally(
        self, make_acp_transport
    ) -> None:
        """Should forward the stdio command (executable + args) to the transport."""
        transport = make_acp_transport()
        # Pin the launcher pass-through: with no PATH resolution (which happens at
        # construction, via _resolve_launcher) the command reaches the transport
        # verbatim, so this asserts the positional splat, not _resolve_launcher
        # (covered by TestResolveLauncher) or whether `npx` happens to be installed here.
        with patch(
            "band.integrations.acp.client_adapter.shutil.which", return_value=None
        ):
            adapter = ACPClientAdapter(
                command=["npx", "@zed-industries/codex-acp"],
                spawn_process=transport,
            )

        await adapter.on_started("Codex Bridge", "Bridge to Codex")

        # spawn(client, *command, ...) — command splatted as positional args.
        args, _ = transport.last_call
        assert args == ("npx", "@zed-industries/codex-acp")

    @pytest.mark.asyncio
    async def test_on_started_stores_agent_info(self, make_acp_transport) -> None:
        """Should store agent name and description."""
        adapter = ACPClientAdapter(command="codex", spawn_process=make_acp_transport())

        await adapter.on_started("Test Agent", "A test agent")

        assert adapter.agent_name == "Test Agent"
        assert adapter.agent_description == "A test agent"

    @pytest.mark.asyncio
    async def test_on_started_prefers_http_mcp_when_supported(
        self, make_acp_transport
    ) -> None:
        """Should select HTTP MCP when the ACP agent advertises it."""
        adapter = ACPClientAdapter(
            command="codex",
            spawn_process=make_acp_transport(http=True, sse=True),
        )

        await adapter.on_started("Test Agent", "A test agent")

        assert adapter._runtime._agent_mcp_transport == "http"

    @pytest.mark.asyncio
    async def test_on_started_uses_sse_mcp_when_http_missing(
        self, make_acp_transport
    ) -> None:
        """Should fall back to SSE MCP when that's all the ACP agent supports."""
        adapter = ACPClientAdapter(
            command="codex",
            spawn_process=make_acp_transport(http=False, sse=True),
        )

        await adapter.on_started("Test Agent", "A test agent")

        assert adapter._runtime._agent_mcp_transport == "sse"


class TestACPClientAdapterOnMessage:
    """Tests for ACPClientAdapter.on_message()."""

    @pytest.fixture
    def adapter_with_mocks(self) -> ACPClientAdapter:
        """Create adapter with mocked ACP connection."""
        adapter = ACPClientAdapter(command="codex", inject_band_tools=False)

        # Mock ACP connection
        adapter._runtime._conn = AsyncMock()
        mock_session = MagicMock()
        mock_session.session_id = "acp-session-123"
        adapter._runtime._conn.new_session = AsyncMock(return_value=mock_session)
        adapter._runtime._conn.prompt = AsyncMock()

        # Mock client with response text
        adapter._runtime._client = BandACPClient()

        return adapter

    @pytest.mark.asyncio
    async def test_on_message_creates_session(
        self, adapter_with_mocks: ACPClientAdapter
    ) -> None:
        """Should create ACP session for new room."""
        tools = FakeAgentTools()
        msg = make_platform_message("Hello", room_id="room-123")

        await adapter_with_mocks.on_message(
            msg,
            tools,
            ACPClientSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        adapter_with_mocks._runtime._conn.new_session.assert_called_once()
        assert adapter_with_mocks._room_to_session["room-123"] == "acp-session-123"

    @pytest.mark.asyncio
    async def test_on_message_reuses_session(
        self, adapter_with_mocks: ACPClientAdapter
    ) -> None:
        """Should reuse existing session for same room."""
        adapter_with_mocks._room_to_session["room-123"] = "existing-session"
        tools = FakeAgentTools()
        msg = make_platform_message("Hello", room_id="room-123")

        await adapter_with_mocks.on_message(
            msg,
            tools,
            ACPClientSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        adapter_with_mocks._runtime._conn.new_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_message_sends_prompt(
        self, adapter_with_mocks: ACPClientAdapter
    ) -> None:
        """Should send prompt to remote ACP agent."""
        tools = FakeAgentTools()
        msg = make_platform_message("What is the weather?", room_id="room-123")

        await adapter_with_mocks.on_message(
            msg,
            tools,
            ACPClientSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        adapter_with_mocks._runtime._conn.prompt.assert_called_once()
        call_kwargs = adapter_with_mocks._runtime._conn.prompt.call_args.kwargs
        assert call_kwargs["session_id"] == "acp-session-123"

    @pytest.mark.asyncio
    async def test_on_message_posts_response(
        self, adapter_with_mocks: ACPClientAdapter
    ) -> None:
        """Should post collected response back to Band room."""

        # Make prompt() populate the per-session buffer (simulating session_update)
        async def mock_prompt(**kwargs):
            session_id = kwargs.get("session_id", "acp-session-123")
            adapter_with_mocks._runtime._client._session_chunks[session_id] = [
                CollectedChunk(chunk_type="text", content="The weather is sunny.")
            ]

        adapter_with_mocks._runtime._conn.prompt = AsyncMock(side_effect=mock_prompt)

        tools = FakeAgentTools()
        msg = make_platform_message("Weather?", room_id="room-123")

        await adapter_with_mocks.on_message(
            msg,
            tools,
            ACPClientSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        # Should have sent message back
        assert len(tools.messages_sent) > 0
        assert tools.messages_sent[0]["content"] == "The weather is sunny."

    @pytest.mark.asyncio
    async def test_on_message_posts_thought_event(
        self, adapter_with_mocks: ACPClientAdapter
    ) -> None:
        """Should post thought chunks as thought events."""

        async def mock_prompt(**kwargs):
            session_id = kwargs.get("session_id", "acp-session-123")
            adapter_with_mocks._runtime._client._session_chunks[session_id] = [
                CollectedChunk(chunk_type="thought", content="Let me think...")
            ]

        adapter_with_mocks._runtime._conn.prompt = AsyncMock(side_effect=mock_prompt)

        tools = FakeAgentTools()
        msg = make_platform_message("Question?", room_id="room-123")

        await adapter_with_mocks.on_message(
            msg,
            tools,
            ACPClientSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        thought_events = [
            e for e in tools.events_sent if e.get("message_type") == "thought"
        ]
        assert len(thought_events) == 1
        assert thought_events[0]["content"] == "Let me think..."

    @pytest.mark.asyncio
    async def test_on_message_posts_tool_call_event(
        self, adapter_with_mocks: ACPClientAdapter
    ) -> None:
        """Should post tool_call chunks as tool_call events."""

        async def mock_prompt(**kwargs):
            session_id = kwargs.get("session_id", "acp-session-123")
            adapter_with_mocks._runtime._client._session_chunks[session_id] = [
                CollectedChunk(
                    chunk_type="tool_call",
                    content="search",
                    metadata={"tool_call_id": "tc-1"},
                )
            ]

        adapter_with_mocks._runtime._conn.prompt = AsyncMock(side_effect=mock_prompt)

        tools = FakeAgentTools()
        msg = make_platform_message("Find info", room_id="room-123")

        await adapter_with_mocks.on_message(
            msg,
            tools,
            ACPClientSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        tool_events = [
            e for e in tools.events_sent if e.get("message_type") == "tool_call"
        ]
        assert len(tool_events) == 1
        assert tool_events[0]["metadata"]["tool_call_id"] == "tc-1"

    @pytest.mark.asyncio
    async def test_on_message_posts_plan_as_task_event(
        self, adapter_with_mocks: ACPClientAdapter
    ) -> None:
        """Should post plan chunks as task events."""

        async def mock_prompt(**kwargs):
            session_id = kwargs.get("session_id", "acp-session-123")
            adapter_with_mocks._runtime._client._session_chunks[session_id] = [
                CollectedChunk(chunk_type="plan", content="Step 1: Do stuff")
            ]

        adapter_with_mocks._runtime._conn.prompt = AsyncMock(side_effect=mock_prompt)

        tools = FakeAgentTools()
        msg = make_platform_message("Plan it", room_id="room-123")

        await adapter_with_mocks.on_message(
            msg,
            tools,
            ACPClientSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        task_events = [
            e
            for e in tools.events_sent
            if e.get("message_type") == "task"
            and "acp_client_session_id" not in e.get("metadata", {})
        ]
        assert len(task_events) == 1
        assert task_events[0]["content"] == "Step 1: Do stuff"

    @pytest.mark.asyncio
    async def test_on_message_emits_task_event(
        self, adapter_with_mocks: ACPClientAdapter
    ) -> None:
        """Should emit task event for session rehydration."""
        tools = FakeAgentTools()
        msg = make_platform_message("Hello", room_id="room-123")

        await adapter_with_mocks.on_message(
            msg,
            tools,
            ACPClientSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        # Should have sent task event
        task_events = [e for e in tools.events_sent if e.get("message_type") == "task"]
        assert len(task_events) == 1
        assert task_events[0]["metadata"]["acp_client_session_id"] == "acp-session-123"

    @pytest.mark.asyncio
    async def test_on_message_bootstrap_rehydrates(
        self, adapter_with_mocks: ACPClientAdapter
    ) -> None:
        """Should rehydrate room -> session mappings on bootstrap."""
        tools = FakeAgentTools()
        msg = make_platform_message("Hello", room_id="room-123")

        adapter_with_mocks._runtime._agent_supports_session_load = True
        adapter_with_mocks._runtime._conn.load_session = AsyncMock(
            return_value=object()
        )
        history = ACPClientSessionState(room_to_session={"room-123": "session-abc"})

        await adapter_with_mocks.on_message(
            msg,
            tools,
            history,
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        assert adapter_with_mocks._room_to_session["room-123"] == "session-abc"
        adapter_with_mocks._runtime._conn.load_session.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_on_message_creates_new_session_when_persisted_session_cannot_load(
        self, adapter_with_mocks: ACPClientAdapter
    ) -> None:
        """A rebooted ephemeral ACP agent creates a session before prompting."""
        stale_session = "stale-session"
        fresh_session = MagicMock(session_id="fresh-session")
        adapter_with_mocks._runtime._conn.new_session = AsyncMock(
            return_value=fresh_session
        )
        adapter_with_mocks._runtime._agent_supports_session_load = True
        adapter_with_mocks._runtime._conn.load_session = AsyncMock(return_value=None)

        async def prompt_new_session(**kwargs):
            session_id = kwargs["session_id"]
            adapter_with_mocks._runtime._client._session_chunks[session_id] = [
                CollectedChunk(chunk_type="text", content="Recovered reply")
            ]

        adapter_with_mocks._runtime._conn.prompt = AsyncMock(
            side_effect=prompt_new_session
        )
        tools = FakeAgentTools()
        msg = make_platform_message("Hello", room_id="room-123")

        await adapter_with_mocks.on_message(
            msg,
            tools,
            ACPClientSessionState(room_to_session={"room-123": stale_session}),
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        assert adapter_with_mocks._room_to_session["room-123"] == "fresh-session"
        adapter_with_mocks._runtime._conn.new_session.assert_awaited_once()
        adapter_with_mocks._runtime._conn.load_session.assert_awaited_once()
        prompt_calls = adapter_with_mocks._runtime._conn.prompt.call_args_list
        assert [call.kwargs["session_id"] for call in prompt_calls] == ["fresh-session"]
        assert "[System Context]" in prompt_calls[0].kwargs["prompt"][0].text
        assert tools.messages_sent[0]["content"] == "Recovered reply"

    @pytest.mark.asyncio
    async def test_on_message_error_sends_error_event(
        self, adapter_with_mocks: ACPClientAdapter
    ) -> None:
        """Should send error event when ACP agent fails."""
        adapter_with_mocks._runtime._conn.prompt = AsyncMock(
            side_effect=RuntimeError("Agent crashed")
        )

        tools = FakeAgentTools()
        msg = make_platform_message("Hello", room_id="room-123")

        await adapter_with_mocks.on_message(
            msg,
            tools,
            ACPClientSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        error_events = [
            e for e in tools.events_sent if e.get("message_type") == "error"
        ]
        assert len(error_events) == 1
        assert "Agent crashed" in error_events[0]["content"]

    @pytest.mark.asyncio
    async def test_on_message_not_initialized_raises(self) -> None:
        """Should raise RuntimeError if not initialized."""
        adapter = ACPClientAdapter(command="codex")
        tools = FakeAgentTools()
        msg = make_platform_message("Hello", room_id="room-123")

        with pytest.raises(RuntimeError, match="ACP client not initialized"):
            await adapter.on_message(
                msg,
                tools,
                ACPClientSessionState(),
                None,
                None,
                is_session_bootstrap=False,
                room_id="room-123",
            )


class TestACPClientAdapterPermissionHandler:
    """Tests for bidirectional permission proxying."""

    @pytest.fixture
    def adapter_with_mocks(self) -> ACPClientAdapter:
        """Create adapter with mocked ACP connection."""
        adapter = ACPClientAdapter(command="codex", inject_band_tools=False)

        # Mock ACP connection
        adapter._runtime._conn = AsyncMock()
        mock_session = MagicMock()
        mock_session.session_id = "acp-session-123"
        adapter._runtime._conn.new_session = AsyncMock(return_value=mock_session)
        adapter._runtime._conn.prompt = AsyncMock()

        # Mock client with response text
        adapter._runtime._client = BandACPClient()

        return adapter

    @pytest.mark.asyncio
    async def test_permission_handler_wired_on_message(
        self, adapter_with_mocks: ACPClientAdapter
    ) -> None:
        """Should set permission handler on client before sending prompt."""
        tools = FakeAgentTools()
        msg = make_platform_message("Hello", room_id="room-123")

        await adapter_with_mocks.on_message(
            msg,
            tools,
            ACPClientSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        # Permission handler should have been set for this session
        assert len(adapter_with_mocks._runtime._client._permission_handlers) > 0

    @pytest.mark.asyncio
    async def test_permission_handler_posts_event(
        self, adapter_with_mocks: ACPClientAdapter
    ) -> None:
        """Should post permission request event to platform."""
        tools = FakeAgentTools()
        msg = make_platform_message("Hello", room_id="room-123")

        # Simulate permission request during prompt
        async def mock_prompt(**kwargs):
            # Trigger the permission handler directly
            tool_call = MagicMock()
            tool_call.title = "write_file"
            tool_call.tool_call_id = "tc-perm-1"

            result = await adapter_with_mocks._runtime._client.request_permission(
                options=[
                    {"optionId": "allow-once", "name": "Allow", "kind": "allow_once"}
                ],
                session_id="acp-session-123",
                tool_call=tool_call,
            )
            assert result == {
                "outcome": {"outcome": "selected", "optionId": "allow-once"}
            }

        adapter_with_mocks._runtime._conn.prompt = AsyncMock(side_effect=mock_prompt)

        await adapter_with_mocks.on_message(
            msg,
            tools,
            ACPClientSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        # Should have posted a permission event
        perm_events = [
            e
            for e in tools.events_sent
            if e.get("metadata", {}).get("permission_request")
        ]
        assert len(perm_events) == 1
        assert perm_events[0]["metadata"]["tool_name"] == "write_file"
        assert perm_events[0]["metadata"]["auto_allowed"] is True
        assert perm_events[0]["message_type"] == "tool_call"

    @pytest.mark.asyncio
    async def test_permission_handler_selects_allow_option(
        self, adapter_with_mocks: ACPClientAdapter
    ) -> None:
        """Should auto-approve by selecting an offered allow option (not "allowed")."""
        tools = FakeAgentTools()
        msg = make_platform_message("Hello", room_id="room-123")

        captured_result = {}

        async def mock_prompt(**kwargs):
            tool_call = MagicMock()
            tool_call.title = "read_file"
            tool_call.tool_call_id = "tc-read"

            result = await adapter_with_mocks._runtime._client.request_permission(
                options=[
                    {"optionId": "p-once", "name": "Allow once", "kind": "allow_once"},
                    {"optionId": "p-rej", "name": "Reject", "kind": "reject_once"},
                ],
                session_id="acp-session-123",
                tool_call=tool_call,
            )
            captured_result.update(result)

        adapter_with_mocks._runtime._conn.prompt = AsyncMock(side_effect=mock_prompt)

        await adapter_with_mocks.on_message(
            msg,
            tools,
            ACPClientSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        assert captured_result == {
            "outcome": {"outcome": "selected", "optionId": "p-once"}
        }

    @pytest.mark.asyncio
    async def test_permission_handler_cancels_without_allow_option(
        self, adapter_with_mocks: ACPClientAdapter
    ) -> None:
        """Should cancel (not guess) when the agent offers no allow option."""
        tools = FakeAgentTools()
        msg = make_platform_message("Hello", room_id="room-123")

        captured_result = {}

        async def mock_prompt(**kwargs):
            tool_call = MagicMock()
            tool_call.title = "rm_rf"
            tool_call.tool_call_id = "tc-danger"

            result = await adapter_with_mocks._runtime._client.request_permission(
                options=[
                    {"optionId": "p-rej", "name": "Reject", "kind": "reject_once"},
                ],
                session_id="acp-session-123",
                tool_call=tool_call,
            )
            captured_result.update(result)

        adapter_with_mocks._runtime._conn.prompt = AsyncMock(side_effect=mock_prompt)

        await adapter_with_mocks.on_message(
            msg,
            tools,
            ACPClientSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        assert captured_result == {"outcome": {"outcome": "cancelled"}}

    @pytest.mark.asyncio
    async def test_permission_handler_uses_name_fallback(
        self, adapter_with_mocks: ACPClientAdapter
    ) -> None:
        """Should fall back to 'name' attr if 'title' is not available."""
        tools = FakeAgentTools()
        msg = make_platform_message("Hello", room_id="room-123")

        async def mock_prompt(**kwargs):
            tool_call = MagicMock(spec=[])  # No attributes by default
            tool_call.name = "bash"
            tool_call.tool_call_id = "tc-bash"

            await adapter_with_mocks._runtime._client.request_permission(
                options={},
                session_id="acp-session-123",
                tool_call=tool_call,
            )

        adapter_with_mocks._runtime._conn.prompt = AsyncMock(side_effect=mock_prompt)

        await adapter_with_mocks.on_message(
            msg,
            tools,
            ACPClientSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        perm_events = [
            e
            for e in tools.events_sent
            if e.get("metadata", {}).get("permission_request")
        ]
        assert len(perm_events) == 1
        assert perm_events[0]["metadata"]["tool_name"] == "bash"


class TestACPClientAdapterCleanup:
    """Tests for ACPClientAdapter cleanup."""

    @pytest.mark.asyncio
    async def test_on_cleanup_removes_mapping(self) -> None:
        """Should remove room -> session mapping."""
        adapter = ACPClientAdapter(command="codex")
        adapter._room_to_session["room-123"] = "session-123"
        adapter._room_tools["room-123"] = MagicMock()
        local_server = MagicMock()
        local_server.stop = AsyncMock()
        backend = MagicMock(local_server=local_server)
        backend.stop = AsyncMock()
        adapter._band_mcp_backend = backend
        adapter._band_mcp_server = local_server

        await adapter.on_cleanup("room-123")

        assert "room-123" not in adapter._room_to_session
        assert "room-123" not in adapter._room_tools
        local_server.stop.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_on_cleanup_idempotent(self) -> None:
        """Should handle cleanup of non-existent room."""
        adapter = ACPClientAdapter(command="codex")

        await adapter.on_cleanup("nonexistent-room")

    @pytest.mark.asyncio
    async def test_on_cleanup_twice(self) -> None:
        """Should handle cleanup called twice."""
        adapter = ACPClientAdapter(command="codex")
        adapter._room_to_session["room-123"] = "session-123"

        await adapter.on_cleanup("room-123")
        await adapter.on_cleanup("room-123")

        assert "room-123" not in adapter._room_to_session


class TestACPClientAdapterStop:
    """Tests for ACPClientAdapter.stop()."""

    @pytest.mark.asyncio
    async def test_stop_closes_connection(self) -> None:
        """Should close ACP connection gracefully."""
        adapter = ACPClientAdapter(command="codex")
        mock_ctx = MagicMock()
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        adapter._runtime._ctx = mock_ctx
        adapter._runtime._conn = AsyncMock()
        adapter._runtime._client = BandACPClient()
        adapter._room_to_session["room-123"] = "session-123"
        adapter._room_tools["room-123"] = MagicMock()
        local_server = MagicMock()
        local_server.stop = AsyncMock()
        backend = MagicMock(local_server=local_server)
        backend.stop = AsyncMock()
        adapter._band_mcp_backend = backend
        adapter._band_mcp_server = local_server
        adapter._bootstrapped_sessions.add("session-123")

        await adapter.stop()

        mock_ctx.__aexit__.assert_called_once()
        backend.stop.assert_awaited_once()
        assert adapter._runtime._ctx is None
        assert adapter._runtime._conn is None
        assert adapter._runtime._client is None
        assert adapter._room_to_session == {}
        assert adapter._room_tools == {}
        assert adapter._band_mcp_backend is None
        assert adapter._band_mcp_server is None
        assert adapter._bootstrapped_sessions == set()

    @pytest.mark.asyncio
    async def test_stop_no_connection(self) -> None:
        """Should handle stop when not connected."""
        adapter = ACPClientAdapter(command="codex")
        local_server = MagicMock()
        local_server.stop = AsyncMock()
        backend = MagicMock(local_server=local_server)
        backend.stop = AsyncMock()
        adapter._band_mcp_backend = backend
        adapter._band_mcp_server = local_server

        await adapter.stop()

        backend.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_handles_exit_error(self) -> None:
        """Should handle errors during shutdown."""
        adapter = ACPClientAdapter(command="codex")
        adapter._runtime._ctx = AsyncMock()
        adapter._runtime._ctx.__aexit__ = AsyncMock(
            side_effect=RuntimeError("Cleanup error")
        )

        # Should not raise
        await adapter.stop()
        assert adapter._runtime._ctx is None


class TestACPCollectingClientCursorProfileExtensions:
    """Tests for Cursor-specific extension handling via ACP client profiles."""

    @pytest.mark.asyncio
    async def test_ext_method_cursor_ask_question(self) -> None:
        """Should auto-select first option for cursor/ask_question."""
        client = ACPCollectingClient(profile=CursorACPClientProfile())

        result = await client.ext_method(
            "cursor/ask_question",
            {
                "options": [
                    {"optionId": "a", "name": "Option A"},
                    {"optionId": "b", "name": "Option B"},
                ],
            },
        )

        assert result["outcome"]["type"] == "selected"
        assert result["outcome"]["optionId"] == "a"

    @pytest.mark.asyncio
    async def test_ext_method_cursor_ask_question_empty_options(self) -> None:
        """Should cancel when no options provided."""
        client = ACPCollectingClient(profile=CursorACPClientProfile())

        result = await client.ext_method("cursor/ask_question", {"options": []})

        assert result["outcome"]["type"] == "cancelled"

    @pytest.mark.asyncio
    async def test_ext_method_cursor_create_plan(self) -> None:
        """Should auto-approve cursor/create_plan."""
        client = ACPCollectingClient(profile=CursorACPClientProfile())

        result = await client.ext_method("cursor/create_plan", {"plan": "stuff"})

        assert result["outcome"]["type"] == "approved"

    @pytest.mark.asyncio
    async def test_ext_method_unknown_returns_empty(self) -> None:
        """Should return empty dict for unknown extension methods."""
        client = ACPCollectingClient(profile=CursorACPClientProfile())

        result = await client.ext_method("unknown/method", {})

        assert result == {}

    @pytest.mark.asyncio
    async def test_ext_notification_cursor_update_todos(self) -> None:
        """Should collect todo updates as plan chunks."""
        client = ACPCollectingClient(profile=CursorACPClientProfile())

        await client.ext_notification(
            "cursor/update_todos",
            {
                "sessionId": "sess-1",
                "todos": [
                    {"content": "Read code", "completed": True},
                    {"content": "Write tests", "completed": False},
                ],
            },
        )

        chunks = client.get_collected_chunks("sess-1")
        assert len(chunks) == 1
        assert chunks[0].chunk_type == "plan"
        assert "[x] Read code" in chunks[0].content
        assert "[ ] Write tests" in chunks[0].content

    @pytest.mark.asyncio
    async def test_ext_notification_cursor_task(self) -> None:
        """Should collect task results as text chunks."""
        client = ACPCollectingClient(profile=CursorACPClientProfile())

        await client.ext_notification(
            "cursor/task",
            {"sessionId": "sess-1", "result": "Refactored the module"},
        )

        chunks = client.get_collected_chunks("sess-1")
        assert len(chunks) == 1
        assert chunks[0].chunk_type == "text"
        assert "Refactored the module" in chunks[0].content

    @pytest.mark.asyncio
    async def test_ext_notification_no_session_id_is_noop(self) -> None:
        """Should do nothing when no session_id is present."""
        client = ACPCollectingClient(profile=CursorACPClientProfile())

        await client.ext_notification(
            "cursor/update_todos",
            {"todos": [{"content": "Test", "completed": False}]},
        )

        # No session_id → no chunks collected
        assert client.get_collected_chunks() == []


class TestACPClientAdapterDeadConnectionRecovery:
    """Tests for dead connection recovery after subprocess crash."""

    @pytest.mark.asyncio
    async def test_prompt_error_clears_connection(self) -> None:
        """Should stop connection on prompt error so next message respawns."""
        adapter = ACPClientAdapter(command="codex", inject_band_tools=False)
        adapter._runtime._conn = AsyncMock()
        adapter._runtime._conn.prompt = AsyncMock(
            side_effect=RuntimeError("Process died")
        )
        mock_session = MagicMock()
        mock_session.session_id = "sess-1"
        adapter._runtime._conn.new_session = AsyncMock(return_value=mock_session)
        adapter._runtime._client = BandACPClient()

        mock_ctx = MagicMock()
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        adapter._runtime._ctx = mock_ctx

        tools = FakeAgentTools()
        msg = make_platform_message("Hello", room_id="room-1")

        await adapter.on_message(
            msg,
            tools,
            ACPClientSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        # Connection should be cleared after error
        assert adapter._runtime._conn is None
        assert adapter._runtime._ctx is None

        # Error event should be sent
        error_events = [
            e for e in tools.events_sent if e.get("message_type") == "error"
        ]
        assert len(error_events) == 1


class TestACPClientAdapterInjectToolsConfig:
    """Tests for inject_band_tools configuration."""

    def test_inject_tools_stays_enabled_without_extra_credentials(self) -> None:
        """Should not require adapter-specific credentials to inject tools."""
        adapter = ACPClientAdapter(command="codex", inject_band_tools=True)

        assert adapter._inject_band_tools

    def test_inject_tools_can_be_disabled_explicitly(self) -> None:
        """Should respect inject_band_tools=False."""
        adapter = ACPClientAdapter(command="codex", inject_band_tools=False)

        assert not adapter._inject_band_tools


class TestResolveLauncher:
    """The launcher is resolved to a full path so the subprocess spawns on Windows,
    where an npm launcher (``npx``) is ``npx.cmd`` and bare-name exec lookup fails."""

    def test_resolves_launcher_and_preserves_args(self) -> None:
        """The launcher becomes its resolved path; the arguments are untouched."""
        with patch(
            "band.integrations.acp.client_adapter.shutil.which",
            return_value="/opt/node/bin/npx",
        ):
            assert _resolve_launcher(["npx", "@zed-industries/codex-acp"]) == [
                "/opt/node/bin/npx",
                "@zed-industries/codex-acp",
            ]

    def test_unresolved_name_is_left_as_is(self) -> None:
        """An unresolvable launcher is passed through so spawn fails loudly, not here."""
        with patch(
            "band.integrations.acp.client_adapter.shutil.which", return_value=None
        ):
            assert _resolve_launcher(["mystery-bin", "arg"]) == ["mystery-bin", "arg"]


class TestTurnRepliedInRoom:
    """`_turn_replied_in_room`: detect a room post from the ACP tool-call stream.

    ACP has no structured tool-name field and tools may run out-of-process, so the
    adapter reads the collected chunk stream. These lock the id-correlation edges.
    """

    @staticmethod
    def _chunk(chunk_type: str, content: str, **metadata: object) -> CollectedChunk:
        return CollectedChunk(chunk_type=chunk_type, content=content, metadata=metadata)

    def test_completed_posting_tool_call_counts_as_reply(self) -> None:
        chunks = [
            self._chunk(
                "tool_call",
                "band_send_message",
                tool_call_id="tc-1",
                status="completed",
            )
        ]
        assert ACPClientAdapter._turn_replied_in_room(chunks)

    def test_posting_call_correlated_to_completed_result_counts(self) -> None:
        # The tool_call arrives before its terminal status; the completed result seals it.
        chunks = [
            self._chunk(
                "tool_call",
                "band_send_message",
                tool_call_id="tc-1",
                status="in_progress",
            ),
            self._chunk("tool_result", "", tool_call_id="tc-1", status="completed"),
        ]
        assert ACPClientAdapter._turn_replied_in_room(chunks)

    def test_empty_ids_do_not_cross_match(self) -> None:
        # A not-yet-completed posting call with NO id and a completed NON-posting result
        # with NO id both default to "" — they must not correlate, or the text fallback
        # is falsely suppressed and the turn goes silent.
        chunks = [
            self._chunk("tool_call", "band_send_message", status="in_progress"),
            self._chunk("tool_result", "", status="completed"),
        ]
        assert not ACPClientAdapter._turn_replied_in_room(chunks)

    def test_non_posting_tool_never_counts(self) -> None:
        chunks = [
            self._chunk(
                "tool_call", "get_weather", tool_call_id="tc-1", status="completed"
            )
        ]
        assert not ACPClientAdapter._turn_replied_in_room(chunks)
