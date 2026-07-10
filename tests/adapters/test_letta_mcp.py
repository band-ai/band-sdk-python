"""Tests for LettaAdapter MCP wiring and lifecycle.

Covers MCP server registration at startup (external and self-hosted modes),
MCP tool attachment to Letta agents, and the self-hosted backend lifecycle
(registration reuse, deregistration on stop, conflict/failure recovery).
Kept separate from ``test_letta_adapter.py`` — which covers the message path
and agent lifecycle — so each file stays focused on one concern.
"""

from __future__ import annotations

import asyncio
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from band.adapters.letta import (
    LettaAdapter,
    LettaAdapterConfig,
    LettaMCPConfig,
    _RoomContext,
)
from band.converters.letta import LettaSessionState
from band.testing import FakeAgentTools
from tests.adapters.lettakit import (
    make_assistant_message,
    make_fake_mcp_backend,
    make_letta_response,
    make_mock_agent,
    make_mock_mcp_server,
    make_mock_mcp_tool,
    make_mock_tool_page,
    make_platform_message,
)

# ──────────────────────────────────────────────────────────────────────
# on_started
# ──────────────────────────────────────────────────────────────────────


class TestLettaAdapterOnStarted:
    @pytest.mark.asyncio
    async def test_on_started_registers_external_mcp(self) -> None:
        adapter = LettaAdapter(
            config=LettaAdapterConfig(
                mcp=LettaMCPConfig(
                    mode="external", server_url="http://localhost:8002/sse"
                )
            )
        )

        mock_client = AsyncMock()
        mock_server = make_mock_mcp_server()
        mock_client.mcp_servers.create.return_value = mock_server
        mock_tools = [
            make_mock_mcp_tool("t1", "band_send_message"),
            make_mock_mcp_tool("t2", "band_send_event"),
        ]
        mock_client.mcp_servers.tools.list.return_value = mock_tools

        mock_letta_module = MagicMock()
        mock_letta_module.AsyncLetta = MagicMock(return_value=mock_client)

        with patch.dict("sys.modules", {"letta_client": mock_letta_module}):
            await adapter.on_started("TestBot", "A test bot")

        mock_letta_module.AsyncLetta.assert_called_once_with(
            base_url="https://api.letta.com",
        )
        mock_client.mcp_servers.create.assert_called_once_with(
            server_name="band",
            config={
                "mcp_server_type": "sse",
                "server_url": "http://localhost:8002/sse",
            },
        )
        assert adapter._mcp.server_id == mock_server.id
        assert adapter._mcp.tool_ids == ["t1", "t2"]
        assert adapter._mcp.backend is None  # external mode starts no local server
        assert adapter._system_prompt  # non-empty

    @pytest.mark.asyncio
    async def test_on_started_self_host_registers_advertised_url(self) -> None:
        """Self-host mode starts the local server, binds the configured host,
        and registers its advertised URL under an instance-unique name."""
        adapter = LettaAdapter(
            config=LettaAdapterConfig(
                mcp=LettaMCPConfig(
                    bind_host="0.0.0.0", advertised_host="host.docker.internal"
                )
            )
        )

        mock_client = AsyncMock()
        mock_server = make_mock_mcp_server()
        mock_client.mcp_servers.create.return_value = mock_server
        mock_client.mcp_servers.tools.list.return_value = [
            make_mock_mcp_tool("t1", "band_send_message"),
        ]

        mock_letta_module = MagicMock()
        mock_letta_module.AsyncLetta = MagicMock(return_value=mock_client)

        fake_backend = make_fake_mcp_backend(port=55321)

        with (
            patch.dict("sys.modules", {"letta_client": mock_letta_module}),
            patch(
                "band.integrations.letta.mcp.create_band_mcp_backend",
                AsyncMock(return_value=fake_backend),
            ) as mock_create,
        ):
            await adapter.on_started("TestBot", "A test bot")

        assert mock_create.call_args.kwargs["host"] == "0.0.0.0"
        create_kwargs = mock_client.mcp_servers.create.call_args.kwargs
        # Fresh unique name per registration: Letta soft-deletes registrations,
        # so a name can never be reused once deregistered.
        assert re.fullmatch(r"band-[0-9a-f]{8}", create_kwargs["server_name"])
        assert create_kwargs["config"] == {
            "mcp_server_type": "sse",
            "server_url": "http://host.docker.internal:55321/sse",
        }
        assert adapter._mcp.backend is fake_backend
        assert adapter._mcp.server_id == mock_server.id

    @pytest.mark.asyncio
    async def test_on_started_forwards_cloud_params(self) -> None:
        """provider_key and project are forwarded to AsyncLetta when configured."""
        adapter = LettaAdapter(
            config=LettaAdapterConfig(
                base_url="https://api.letta.com",
                provider_key="letta-key-123",
                project="my-project",
                mcp=LettaMCPConfig(mode="external"),
            )
        )

        mock_client = AsyncMock()
        mock_server = make_mock_mcp_server()
        mock_client.mcp_servers.create.return_value = mock_server
        mock_client.mcp_servers.tools.list.return_value = []

        mock_letta_module = MagicMock()
        mock_letta_module.AsyncLetta = MagicMock(return_value=mock_client)

        with patch.dict("sys.modules", {"letta_client": mock_letta_module}):
            await adapter.on_started("TestBot", "A test bot")

        mock_letta_module.AsyncLetta.assert_called_once_with(
            base_url="https://api.letta.com",
            api_key="letta-key-123",
            project="my-project",
        )

    @pytest.mark.asyncio
    async def test_on_started_mcp_registration_failure_raises(self) -> None:
        adapter = LettaAdapter(
            config=LettaAdapterConfig(mcp=LettaMCPConfig(mode="external"))
        )

        mock_client = AsyncMock()
        mock_client.mcp_servers.create.side_effect = ConnectionError("refused")

        mock_letta_module = MagicMock()
        mock_letta_module.AsyncLetta = MagicMock(return_value=mock_client)

        with patch.dict("sys.modules", {"letta_client": mock_letta_module}):
            with pytest.raises(RuntimeError, match="MCP server registration failed"):
                await adapter.on_started("TestBot", "A test bot")

    @pytest.mark.asyncio
    async def test_on_started_import_error(self) -> None:
        adapter = LettaAdapter()

        with patch.dict("sys.modules", {"letta_client": None}):
            with pytest.raises(ImportError, match="letta-client is required"):
                await adapter.on_started("TestBot", "A test bot")


# ──────────────────────────────────────────────────────────────────────
# MCP tool attachment
# ──────────────────────────────────────────────────────────────────────


class TestMCPToolAttachment:
    @pytest.mark.asyncio
    async def test_mcp_tools_attached_on_agent_creation(self) -> None:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        adapter._mcp.tool_ids = ["tool-1", "tool-2", "tool-3"]

        mock_agent = make_mock_agent("new-agent")
        mock_client.agents.create.return_value = mock_agent

        agent_id = await adapter._create_agent()

        assert agent_id == "new-agent"
        assert mock_client.agents.tools.attach.call_count == 3
        attach_calls = mock_client.agents.tools.attach.call_args_list
        for i, call in enumerate(attach_calls):
            assert call.kwargs["agent_id"] == "new-agent"
            assert call.kwargs["tool_id"] == f"tool-{i + 1}"

    @pytest.mark.asyncio
    async def test_verify_and_reattach_missing_tools(self) -> None:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._mcp.tool_ids = ["t1", "t2", "t3"]

        # Agent has only t1 attached
        existing_tool = MagicMock()
        existing_tool.id = "t1"
        mock_client.agents.tools.list.return_value = make_mock_tool_page(existing_tool)

        await adapter._verify_mcp_tools_attached("agent-1")

        # Exactly the missing tools are re-attached, to the right agent
        reattached = [
            (call.kwargs["agent_id"], call.kwargs["tool_id"])
            for call in mock_client.agents.tools.attach.call_args_list
        ]
        assert reattached == [("agent-1", "t2"), ("agent-1", "t3")]

    @pytest.mark.asyncio
    async def test_attach_failure_logs_warning(self) -> None:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._mcp.tool_ids = ["t1"]

        mock_client.agents.tools.attach.side_effect = Exception("attach failed")
        mock_agent = make_mock_agent()
        mock_client.agents.create.return_value = mock_agent

        # Should not raise — just log warning
        agent_id = await adapter._create_agent()
        assert agent_id == mock_agent.id


# ──────────────────────────────────────────────────────────────────────
# Self-hosted MCP lifecycle
# ──────────────────────────────────────────────────────────────────────


class TestSelfHostedMCPLifecycle:
    @pytest.mark.asyncio
    async def test_cleanup_keeps_backend_and_registration(self) -> None:
        """The MCP server and registration are adapter-scoped: room churn —
        including the last room — must not touch them (Letta wedges on a
        deleted-then-dead server; rejoin flows reuse the live registration)."""
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._mcp.server_id = "mcp-server-1"
        adapter._mcp.tool_ids = ["t1"]
        fake_backend = make_fake_mcp_backend()
        adapter._mcp.backend = fake_backend
        adapter._rooms["room-1"] = _RoomContext(agent_id="agent-1")

        await adapter.on_cleanup("room-1")

        fake_backend.stop.assert_not_awaited()
        mock_client.mcp_servers.delete.assert_not_called()
        assert adapter._mcp.backend is fake_backend
        assert adapter._mcp.server_id == "mcp-server-1"

    @pytest.mark.asyncio
    async def test_message_after_stop_reregisters_same_backend(self) -> None:
        """After cleanup_all released the registration, a new message
        re-registers the still-running server instead of starting a second."""
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        fake_backend = make_fake_mcp_backend(port=55999)
        adapter._mcp.backend = fake_backend  # kept by cleanup_all
        assert adapter._mcp.server_id is None

        mock_server = make_mock_mcp_server("mcp-server-2")
        mock_client.mcp_servers.list.return_value = []
        mock_client.mcp_servers.create.return_value = mock_server
        mock_client.mcp_servers.tools.list.return_value = [
            make_mock_mcp_tool("t9", "band_send_message"),
        ]
        mock_client.agents.create.return_value = make_mock_agent("agent-9")
        mock_client.agents.messages.create.return_value = make_letta_response(
            make_assistant_message("Back online!")
        )

        tools = FakeAgentTools()
        with patch(
            "band.integrations.letta.mcp.create_band_mcp_backend",
            AsyncMock(side_effect=AssertionError("must reuse the running backend")),
        ):
            await adapter.on_message(
                make_platform_message(),
                tools,
                LettaSessionState(),
                None,
                None,
                is_session_bootstrap=True,
                room_id="room-1",
            )

        assert adapter._mcp.backend is fake_backend
        assert adapter._mcp.server_id == "mcp-server-2"
        assert adapter._mcp.tool_ids == ["t9"]

    @pytest.mark.asyncio
    async def test_message_after_stop_resyncs_retained_room_tools(self) -> None:
        """A room retained across cleanup_all keeps its Letta agent, but the
        re-registration mints new tool ids — the agent must be re-attached to
        them, or its platform tool calls die with the old registration."""
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        adapter._mcp.server_id = "mcp-old"
        adapter._mcp.tool_ids = ["t-old"]
        adapter._mcp.backend = make_fake_mcp_backend()
        adapter._rooms["room-1"] = _RoomContext(agent_id="agent-1")

        await adapter.cleanup_all()

        mock_client.mcp_servers.list.return_value = []
        mock_client.mcp_servers.create.return_value = make_mock_mcp_server("mcp-new")
        mock_client.mcp_servers.tools.list.return_value = [
            make_mock_mcp_tool("t-new", "band_send_message"),
        ]
        # The agent still carries only the old registration's (dead) tool.
        mock_client.agents.tools.list.return_value = make_mock_tool_page(
            make_mock_mcp_tool("t-old", "band_send_message")
        )
        mock_client.agents.messages.create.return_value = make_letta_response(
            make_assistant_message("Back!")
        )

        await adapter.on_message(
            make_platform_message(),
            FakeAgentTools(),
            LettaSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        mock_client.agents.tools.attach.assert_awaited_once_with(
            agent_id="agent-1", tool_id="t-new"
        )
        assert adapter._rooms["room-1"].stale_tools is False
        # The retained room resumes its existing agent, not a fresh one.
        mock_client.agents.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_failed_tool_resync_keeps_room_stale_for_retry(self) -> None:
        """A transient Letta failure during the post-rotation tool re-sync
        must not clear the room's stale marker — the next turn retries
        instead of running the agent with dead tools."""
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._mcp.server_id = "mcp-new"
        adapter._mcp.tool_ids = ["t-new"]
        room_ctx = _RoomContext(agent_id="agent-1", stale_tools=True)
        adapter._rooms["room-1"] = room_ctx
        mock_client.agents.tools.list.side_effect = ConnectionError("letta hiccup")

        with pytest.raises(RuntimeError, match="MCP tools are not attached"):
            await adapter._ensure_agent("room-1", LettaSessionState(), FakeAgentTools())

        assert room_ctx.stale_tools is True

    @pytest.mark.asyncio
    async def test_failed_tool_resync_skips_turn(self) -> None:
        """When post-rotation re-sync fails, on_message reports an error and
        does not invoke the Letta agent with stale tool ids."""
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        adapter._mcp.server_id = "mcp-new"
        adapter._mcp.tool_ids = ["t-new"]
        adapter._rooms["room-1"] = _RoomContext(agent_id="agent-1", stale_tools=True)
        mock_client.agents.tools.list.side_effect = ConnectionError("letta hiccup")

        tools = FakeAgentTools()
        await adapter.on_message(
            make_platform_message(),
            tools,
            LettaSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        error_events = [e for e in tools.events_sent if e["message_type"] == "error"]
        assert len(error_events) == 1
        mock_client.agents.messages.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_all_external_keeps_shared_registration(self) -> None:
        """External MCP registrations are shared — stopping one adapter must
        not deregister the server or mark retained rooms stale."""
        adapter = LettaAdapter(
            config=LettaAdapterConfig(
                mcp=LettaMCPConfig(
                    mode="external", server_url="http://localhost:8002/sse"
                )
            )
        )
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._mcp.server_id = "shared-mcp"
        adapter._mcp.tool_ids = ["t1"]
        adapter._rooms["room-1"] = _RoomContext(agent_id="agent-1")

        await adapter.cleanup_all()

        mock_client.mcp_servers.delete.assert_not_called()
        assert adapter._mcp.server_id == "shared-mcp"
        assert adapter._mcp.tool_ids == ["t1"]
        assert adapter._rooms["room-1"].stale_tools is False

    @pytest.mark.asyncio
    async def test_cleanup_all_fixed_server_name_keeps_registration(self) -> None:
        """A configured self-hosted server_name must not be deregistered — Letta
        soft-deletes names, so one stop would poison the fixed name forever."""
        adapter = LettaAdapter(
            config=LettaAdapterConfig(mcp=LettaMCPConfig(server_name="band-compose"))
        )
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._mcp.server_id = "mcp-fixed"
        adapter._mcp.tool_ids = ["t1"]
        adapter._rooms["room-1"] = _RoomContext(agent_id="agent-1")

        await adapter.cleanup_all()

        mock_client.mcp_servers.delete.assert_not_called()
        assert adapter._mcp.server_id is None
        assert adapter._mcp.tool_ids == []
        assert adapter._rooms["room-1"].stale_tools is False

    @pytest.mark.asyncio
    async def test_register_rejects_stale_url_for_fixed_name(self) -> None:
        adapter = LettaAdapter(
            config=LettaAdapterConfig(mcp=LettaMCPConfig(server_name="band-compose"))
        )
        mock_client = AsyncMock()
        stale = make_mock_mcp_server("mcp-stale")
        stale.server_name = "band-compose"
        stale.config = {"server_url": "http://dead:1/sse"}
        mock_client.mcp_servers.list.return_value = [stale]

        with pytest.raises(RuntimeError, match="points at"):
            await adapter._mcp.register(
                mock_client,
                server_name="band-compose",
                server_url="http://live:2/sse",
            )

    @pytest.mark.asyncio
    async def test_register_uses_fresh_name_when_stale_url_is_ephemeral(self) -> None:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        stale = make_mock_mcp_server("mcp-stale")
        stale.server_name = "band-deadname"
        stale.config = {"server_url": "http://dead:1/sse"}
        fresh = make_mock_mcp_server("mcp-fresh")
        mock_client.mcp_servers.list.return_value = [stale]
        mock_client.mcp_servers.create.return_value = fresh
        mock_client.mcp_servers.tools.list.return_value = [
            make_mock_mcp_tool("t1", "band_send_message"),
        ]

        await adapter._mcp.register(
            mock_client,
            server_name="band-deadname",
            server_url="http://live:2/sse",
        )

        create_name = mock_client.mcp_servers.create.call_args.kwargs["server_name"]
        assert create_name != "band-deadname"
        assert adapter._mcp.server_id == "mcp-fresh"

    def test_registration_rotates_on_release(self) -> None:
        external = LettaAdapter(
            config=LettaAdapterConfig(
                mcp=LettaMCPConfig(mode="external", server_url="http://mcp/sse")
            )
        )
        ephemeral = LettaAdapter()
        fixed = LettaAdapter(
            config=LettaAdapterConfig(mcp=LettaMCPConfig(server_name="band-compose"))
        )
        assert external._mcp.registration_rotates_on_release is False
        assert ephemeral._mcp.registration_rotates_on_release is True
        assert fixed._mcp.registration_rotates_on_release is False

    def test_advertised_url_loopback_when_bind_all_interfaces(self) -> None:
        adapter = LettaAdapter(
            config=LettaAdapterConfig(
                mcp=LettaMCPConfig(bind_host="0.0.0.0", transport="sse")
            )
        )
        assert adapter._mcp.advertised_url(50001) == "http://127.0.0.1:50001/sse"

    def test_room_tools_resolver(self) -> None:
        """The MCP resolver reads the room context's current tools."""
        adapter = LettaAdapter()
        tools = FakeAgentTools()
        adapter._rooms["room-1"] = _RoomContext(agent_id="agent-1", tools=tools)
        assert adapter._get_room_tools("room-1") is tools
        assert adapter._get_room_tools("other") is None

    @pytest.mark.asyncio
    async def test_cleanup_all_deregisters_but_keeps_server_running(self) -> None:
        """Agent shutdown releases the Letta registration (it would otherwise
        leak and poison later syncs) but leaves the server serving: Letta
        closes its cached session asynchronously after the delete, and a
        server that dies around that close wedges Letta's sync worker."""
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._mcp.server_id = "mcp-server-1"
        fake_backend = make_fake_mcp_backend()
        adapter._mcp.backend = fake_backend
        adapter._rooms["room-1"] = _RoomContext(agent_id="agent-1")

        await adapter.cleanup_all()

        mock_client.mcp_servers.delete.assert_awaited_once_with("mcp-server-1")
        fake_backend.stop.assert_not_awaited()
        assert adapter._mcp.backend is fake_backend
        assert adapter._mcp.server_id is None
        assert adapter._mcp.tool_ids == []
        assert adapter._rooms["room-1"].stale_tools is True

    @pytest.mark.asyncio
    async def test_cleanup_all_timeout_warns_and_continues(self, caplog) -> None:
        async def stalled_delete(_server_id: str) -> None:
            await asyncio.sleep(1)

        adapter = LettaAdapter(config=LettaAdapterConfig(teardown_timeout_s=0.01))
        mock_client = AsyncMock()
        mock_client.mcp_servers.delete.side_effect = stalled_delete
        adapter._client = mock_client
        adapter._mcp.server_id = "mcp-server-1"

        with caplog.at_level("WARNING", logger="band.adapters.letta"):
            await adapter.cleanup_all()

        mock_client.mcp_servers.delete.assert_awaited_once_with("mcp-server-1")
        assert adapter._mcp.server_id is None
        assert (
            "Timed out after 0.01s while trying to deregister MCP server" in caplog.text
        )

    @pytest.mark.asyncio
    async def test_create_conflict_recovers_committed_registration(self) -> None:
        """A create that conflicts with its own timed-out first attempt (the
        client retried) recovers the committed registration by name."""
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"

        committed = make_mock_mcp_server("mcp-committed")
        committed.server_name = "band-abc12345"
        # Lookup before create sees nothing; the post-conflict lookup finds
        # the row the first (timed-out) attempt committed.
        mock_client.mcp_servers.list.side_effect = [[], [committed]]
        mock_client.mcp_servers.create.side_effect = Exception("409 already exists")
        mock_client.mcp_servers.tools.list.return_value = [
            make_mock_mcp_tool("t1", "band_send_message"),
        ]

        await adapter._mcp.register(
            mock_client,
            server_name="band-abc12345",
            server_url="http://host.docker.internal:50001/sse",
        )

        assert adapter._mcp.server_id == "mcp-committed"
        assert adapter._mcp.tool_ids == ["t1"]

    @pytest.mark.asyncio
    async def test_prepare_failure_reports_error_and_skips_turn(self) -> None:
        """A failed MCP/agent setup surfaces as one error event; no Letta turn
        is attempted."""
        adapter = LettaAdapter(
            config=LettaAdapterConfig(mcp=LettaMCPConfig(mode="external"))
        )
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        mock_client.mcp_servers.list.side_effect = ConnectionError("letta down")

        tools = FakeAgentTools()
        await adapter.on_message(
            make_platform_message(),
            tools,
            LettaSessionState(),
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        error_events = [e for e in tools.events_sent if e["message_type"] == "error"]
        assert len(error_events) == 1
        mock_client.agents.messages.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_registration_failure_keeps_backend_for_retry(self) -> None:
        """A registration failure keeps the just-started server: a half-
        committed registration may point at it (Letta only tolerates live
        servers), and the retry reuses it under a fresh name."""
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        mock_client.mcp_servers.list.side_effect = ConnectionError("letta down")

        fake_backend = make_fake_mcp_backend()
        with patch(
            "band.integrations.letta.mcp.create_band_mcp_backend",
            AsyncMock(return_value=fake_backend),
        ):
            with pytest.raises(RuntimeError, match="MCP server registration failed"):
                await adapter._mcp.ensure_ready(mock_client)

        fake_backend.stop.assert_not_awaited()
        assert adapter._mcp.backend is fake_backend
        assert adapter._mcp.server_id is None

    def test_streamable_http_advertised_url(self) -> None:
        adapter = LettaAdapter(
            config=LettaAdapterConfig(
                mcp=LettaMCPConfig(
                    transport="streamable_http",
                    advertised_host="host.docker.internal",
                )
            )
        )
        assert (
            adapter._mcp.advertised_url(50001)
            == "http://host.docker.internal:50001/mcp"
        )

    @pytest.mark.asyncio
    async def test_discovery_failure_leaves_registration_not_ready(self) -> None:
        """A failed tool discovery must not mark the MCP path ready — the next
        ensure_ready retries instead of running agents with no tools."""
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        mock_client.mcp_servers.list.return_value = []
        mock_client.mcp_servers.create.return_value = make_mock_mcp_server()
        mock_client.mcp_servers.tools.list.side_effect = ConnectionError("boom")

        with pytest.raises(RuntimeError, match="MCP server registration failed"):
            await adapter._mcp.register(
                mock_client, server_name="band-x", server_url="http://h:1/sse"
            )

        assert adapter._mcp.server_id is None
