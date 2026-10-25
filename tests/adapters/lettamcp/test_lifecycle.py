"""Self-hosted Letta MCP registration and backend lifecycle."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from band.adapters.letta import (
    LettaAdapter,
    LettaAdapterConfig,
    LettaMCPConfig,
    RoomContext,
)
from band.converters.letta import LettaSessionState
from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE
from band.testing import FakeAgentTools, reported_failures
from tests.adapters.audiences import ROOM_MEMBERS
from tests.adapters.lettakit import (
    make_assistant_message,
    make_letta_response,
    make_mock_agent,
    make_mock_mcp_server,
    make_mock_mcp_tool,
    make_mock_tool_page,
    make_platform_message,
)
from tests.adapters.lettamcp.kit import _stale_tool_error
from tests.mcpbackends import FakeBandMCPBackend, backends_created_by, hold_backend


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
        fake_backend = FakeBandMCPBackend()
        await hold_backend(adapter._mcp.backend, fake_backend)
        adapter._rooms["room-1"] = RoomContext(agent_id="agent-1")

        await adapter.on_cleanup("room-1")

        assert fake_backend.stop_calls == 0
        mock_client.mcp_servers.delete.assert_not_called()
        assert adapter._mcp.backend.current is fake_backend
        assert adapter._mcp.server_id == "mcp-server-1"

    @pytest.mark.asyncio
    async def test_message_after_stop_reregisters_same_backend(self) -> None:
        """After cleanup_all released the registration, a new message
        re-registers the still-running server instead of starting a second."""
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        fake_backend = FakeBandMCPBackend()
        await hold_backend(adapter._mcp.backend, fake_backend)
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

        tools = FakeAgentTools(participants=ROOM_MEMBERS)
        with backends_created_by(then=None):
            await adapter.on_message(
                make_platform_message(),
                tools,
                LettaSessionState(),
                None,
                None,
                is_session_bootstrap=True,
                room_id="room-1",
            )

        assert adapter._mcp.backend.current is fake_backend
        assert adapter._mcp.server_id == "mcp-server-2"
        assert adapter._mcp.tool_ids == ["t9"]

    @pytest.mark.asyncio
    async def test_stale_room_resyncs_tools_on_next_message(self) -> None:
        """A room flagged stale by a runtime re-register keeps its Letta agent
        but re-attaches to the new tool ids on its next message — otherwise its
        platform tool calls die with the old registration's ids."""
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        # A re-register already minted fresh ids and flagged the room stale.
        adapter._mcp.server_id = "mcp-new"
        adapter._mcp.tool_ids = ["t-new"]
        await hold_backend(adapter._mcp.backend, FakeBandMCPBackend())
        adapter._rooms["room-1"] = RoomContext(agent_id="agent-1", stale_tools=True)

        # The agent still carries only the old registration's (dead) tool.
        mock_client.agents.tools.list.return_value = make_mock_tool_page(
            make_mock_mcp_tool("t-old", "band_send_message")
        )
        mock_client.agents.messages.create.return_value = make_letta_response(
            make_assistant_message("Back!")
        )

        await adapter.on_message(
            make_platform_message(),
            FakeAgentTools(participants=ROOM_MEMBERS),
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
        room_ctx = RoomContext(agent_id="agent-1", stale_tools=True)
        adapter._rooms["room-1"] = room_ctx
        mock_client.agents.tools.list.side_effect = ConnectionError("letta hiccup")

        with pytest.raises(RuntimeError, match="MCP tools are not attached"):
            await adapter._ensure_agent(
                "room-1", LettaSessionState(), FakeAgentTools(participants=ROOM_MEMBERS)
            )

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
        adapter._rooms["room-1"] = RoomContext(agent_id="agent-1", stale_tools=True)
        mock_client.agents.tools.list.side_effect = ConnectionError("letta hiccup")

        tools = FakeAgentTools(participants=ROOM_MEMBERS)
        with pytest.raises(RuntimeError, match="not attached"):
            await adapter.on_message(
                make_platform_message(),
                tools,
                LettaSessionState(),
                None,
                None,
                is_session_bootstrap=False,
                room_id="room-1",
            )

        failures = reported_failures(tools)
        assert len(failures) == 1
        assert failures[0]["provider"] == "letta"
        assert failures[0]["message"] == GENERIC_PROVIDER_FAILURE_MESSAGE
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
        adapter._rooms["room-1"] = RoomContext(agent_id="agent-1")

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
        adapter._rooms["room-1"] = RoomContext(agent_id="agent-1")

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
        stale = make_mock_mcp_server(
            "mcp-stale", name="band-compose", url="http://dead:1/sse"
        )
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
        stale = make_mock_mcp_server(
            "mcp-stale", name="band-deadname", url="http://dead:1/sse"
        )
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
        tools = FakeAgentTools(participants=ROOM_MEMBERS)
        adapter._rooms["room-1"] = RoomContext(agent_id="agent-1", tools=tools)
        assert adapter._get_room_tools("room-1") is tools
        assert adapter._get_room_tools("other") is None

    @pytest.mark.asyncio
    async def test_cleanup_all_selfhost_keeps_registration(self) -> None:
        """Ephemeral self-host cleanup must NOT deregister the Letta row.

        Letta shares MCP tool rows by name across the org, so deregistering one
        adapter's server cascade-deletes tools out from under sibling agents
        still running in the same org (the silent-turn failure this guards).
        cleanup_all clears only the local cache; the row and its tools survive.
        """
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._mcp.server_id = "mcp-server-1"
        adapter._mcp.tool_ids = ["t1"]
        fake_backend = FakeBandMCPBackend()
        await hold_backend(adapter._mcp.backend, fake_backend)
        adapter._rooms["room-1"] = RoomContext(agent_id="agent-1")

        await adapter.cleanup_all()

        mock_client.mcp_servers.delete.assert_not_called()
        assert fake_backend.stop_calls == 0
        assert adapter._mcp.backend.current is fake_backend
        assert adapter._mcp.server_id is None
        assert adapter._mcp.tool_ids == []
        # Nothing rotated, so the retained room's attachments stay valid.
        assert adapter._rooms["room-1"].stale_tools is False

    @pytest.mark.asyncio
    async def test_attach_recovers_from_stale_tool_404(self) -> None:
        """A 404 on attach (tool ids died in the org) triggers one re-register
        against a fresh server, then re-attaches the new ids."""
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        await hold_backend(adapter._mcp.backend, FakeBandMCPBackend())
        adapter._mcp.server_id = "mcp-dead"
        adapter._mcp.tool_ids = ["t-dead"]

        # First attach 404s (stale); after re-register it succeeds.
        stale = _stale_tool_error("Tool with id=t-dead not found in organization")
        mock_client.agents.tools.attach.side_effect = [stale, None]
        mock_client.agents.create.return_value = make_mock_agent("agent-1")
        # Re-registration mints a fresh server exposing a live tool id.
        mock_client.mcp_servers.list.return_value = []
        mock_client.mcp_servers.create.return_value = make_mock_mcp_server("mcp-fresh")
        mock_client.mcp_servers.tools.list.return_value = [
            make_mock_mcp_tool("t-live", "band_send_message"),
        ]

        agent_id = await adapter._create_agent()

        assert agent_id == "agent-1"
        assert adapter._mcp.server_id == "mcp-fresh"
        assert adapter._mcp.tool_ids == ["t-live"]
        # Attached the dead id (fails), then the live id after re-register.
        attached = [
            c.kwargs["tool_id"] for c in mock_client.agents.tools.attach.call_args_list
        ]
        assert attached == ["t-dead", "t-live"]

    @pytest.mark.asyncio
    async def test_reregister_marks_other_rooms_stale(self) -> None:
        """When a re-register mints new tool ids, other rooms' agents must be
        flagged to re-verify — their fast path would otherwise run dead tools."""
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        await hold_backend(adapter._mcp.backend, FakeBandMCPBackend())
        adapter._mcp.server_id = "mcp-dead"
        adapter._mcp.tool_ids = ["t-dead"]
        # A sibling room, live before the recovery, wired to the old ids.
        adapter._rooms["other"] = RoomContext(agent_id="agent-other")

        stale = _stale_tool_error("Tool with id=t-dead not found in organization")
        mock_client.agents.tools.attach.side_effect = [stale, None]
        mock_client.agents.create.return_value = make_mock_agent("agent-new")
        mock_client.mcp_servers.list.return_value = []
        mock_client.mcp_servers.create.return_value = make_mock_mcp_server("mcp-fresh")
        mock_client.mcp_servers.tools.list.return_value = [
            make_mock_mcp_tool("t-live", "band_send_message"),
        ]

        await adapter._create_agent()

        assert adapter._rooms["other"].stale_tools is True

    @pytest.mark.asyncio
    async def test_create_conflict_recovers_committed_registration(self) -> None:
        """A create that conflicts with its own timed-out first attempt (the
        client retried) recovers the committed registration by name."""
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"

        committed = make_mock_mcp_server("mcp-committed", name="band-abc12345")
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

        tools = FakeAgentTools(participants=ROOM_MEMBERS)
        with pytest.raises(RuntimeError, match="MCP server registration failed"):
            await adapter.on_message(
                make_platform_message(),
                tools,
                LettaSessionState(),
                None,
                None,
                is_session_bootstrap=True,
                room_id="room-1",
            )

        failures = reported_failures(tools)
        assert len(failures) == 1
        assert failures[0]["provider"] == "letta"
        assert failures[0]["message"] == GENERIC_PROVIDER_FAILURE_MESSAGE
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

        fake_backend = FakeBandMCPBackend()
        with (
            backends_created_by(fake_backend),
            pytest.raises(RuntimeError, match="MCP server registration failed"),
        ):
            await adapter._mcp.ensure_ready(mock_client)

        assert fake_backend.stop_calls == 0
        assert adapter._mcp.backend.current is fake_backend
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
