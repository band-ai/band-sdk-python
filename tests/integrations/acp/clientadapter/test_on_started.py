"""Tests for ACPClientAdapter.on_started() and lazy room runtime connection."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from band.integrations.acp.client_adapter import (
    ACPClientAdapter,
    ACPClientAdapterConfig,
)
from band.integrations.mcp import BandMCPTransport
from tests.integrations.acp.acp_toolkit.harness import (
    inject_acp_spawn,
    started_runtime,
)
from tests.integrations.acp.clientadapter.kit import CODEX


class TestACPClientAdapterOnStarted:
    """Tests for ACPClientAdapter.on_started() and lazy room runtime connection.

    Spawn/transport tests inject :class:`FakeSpawn` via ``inject_acp_spawn`` and
    start the room runtime explicitly — ``on_started`` no longer spawns.
    """

    @pytest.mark.asyncio
    async def test_room_runtime_start_spawns_and_initializes(
        self, make_acp_transport
    ) -> None:
        """A room runtime's own start() spawns the process and initializes it."""
        transport = make_acp_transport()
        adapter = ACPClientAdapter(CODEX)
        inject_acp_spawn(adapter, transport)
        await adapter.on_started("Codex Bridge", "Bridge to Codex")
        runtime = await started_runtime(adapter)

        assert runtime._conn is transport.conn
        transport.conn.initialize.assert_awaited_once_with(protocol_version=1)

    @pytest.mark.asyncio
    async def test_on_started_skips_builtin_transport_options_for_injected_spawn(
        self, make_acp_transport
    ) -> None:
        """Injected ``spawn_process`` factories must not receive stdio transport knobs.

        ``transport_kwargs`` / ``use_unstable_protocol`` are only meaningful for
        the built-in stdio/TCP constructors; an injected factory owns its own
        connection options.
        """
        transport = make_acp_transport()
        adapter = ACPClientAdapter(
            ACPClientAdapterConfig(command=["npx", "@zed-industries/codex-acp"])
        )
        inject_acp_spawn(adapter, transport)
        await adapter.on_started("Codex Bridge", "Bridge to Codex")
        await started_runtime(adapter)

        assert "transport_kwargs" not in transport.last_kwargs
        assert "use_unstable_protocol" not in transport.last_kwargs

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
                ACPClientAdapterConfig(command=["npx", "@zed-industries/codex-acp"])
            )
            inject_acp_spawn(adapter, transport)
            await adapter.on_started("Codex Bridge", "Bridge to Codex")
            await started_runtime(adapter)

            # spawn(client, *command, ...) — command splatted as positional args.
            args, _ = transport.last_call
            assert args == ("npx", "@zed-industries/codex-acp")

    @pytest.mark.asyncio
    async def test_on_started_stores_agent_info(self, make_acp_transport) -> None:
        """Should store agent name and description."""
        adapter = ACPClientAdapter(CODEX)

        await adapter.on_started("Test Agent", "A test agent")

        assert adapter.agent_name == "Test Agent"
        assert adapter.agent_description == "A test agent"

    @pytest.mark.asyncio
    async def test_on_started_prefers_http_mcp_when_supported(
        self, make_acp_transport
    ) -> None:
        """Should select HTTP MCP when the ACP agent advertises it."""
        adapter = ACPClientAdapter(CODEX)
        inject_acp_spawn(adapter, make_acp_transport(http=True, sse=True))
        await adapter.on_started("Test Agent", "A test agent")
        runtime = await started_runtime(adapter)

        assert runtime._agent_mcp_transport is BandMCPTransport.HTTP

    @pytest.mark.asyncio
    async def test_on_started_uses_sse_mcp_when_http_missing(
        self, make_acp_transport
    ) -> None:
        """Should fall back to SSE MCP when that's all the ACP agent supports."""
        adapter = ACPClientAdapter(CODEX)
        inject_acp_spawn(adapter, make_acp_transport(http=False, sse=True))
        await adapter.on_started("Test Agent", "A test agent")
        runtime = await started_runtime(adapter)

        assert runtime._agent_mcp_transport is BandMCPTransport.SSE
