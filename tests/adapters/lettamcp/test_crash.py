"""Self-hosted Letta MCP recovery after backend crashes."""

from __future__ import annotations

import asyncio

import pytest

from band.adapters.letta import (
    LettaAdapterConfig,
    LettaMCPConfig,
)
from tests.adapters.lettakit import (
    make_mock_mcp_server,
)
from tests.adapters.lettamcp.kit import (
    _stale_tool_error,
    registered_on_a_dead_fake_server,
    registered_on_real_server,
    registered_urls,
    serves_band_tools,
)
from tests.mcpbackends import backends_created_by
from tests.mcpclient import crash_backend


class TestSelfHostedMCPCrash:
    @pytest.mark.asyncio
    async def test_a_crashed_server_is_repointed_in_place(self) -> None:
        """Letta reads a registration's URL on every tool call, so updating the
        row reconnects the agents' attached tools; no new ids, no re-attach."""
        async with registered_on_real_server() as (bridge, client):
            await crash_backend(bridge.backend)

            await bridge.ensure_ready(client)

            live_url = registered_urls(client)[-1]
            assert client.mcp_servers.update.await_args.args == ("mcp-server-1",)
            assert client.mcp_servers.create.await_count == 1
            assert (bridge.server_id, bridge.tool_ids) == ("mcp-server-1", ["t1"])
            assert await serves_band_tools(live_url)

    @pytest.mark.asyncio
    async def test_a_released_crashed_server_is_replaced_before_registering(
        self,
    ) -> None:
        async with registered_on_real_server() as (bridge, client):
            await bridge.release(client)
            await crash_backend(bridge.backend)

            await bridge.ensure_ready(client)

            assert await serves_band_tools(registered_urls(client)[-1])

    @pytest.mark.asyncio
    async def test_a_released_crashed_server_keeps_its_fixed_name_registration(
        self,
    ) -> None:
        """The row still points at this process's own dead URL, so it is
        repointed rather than rejected as another instance's registration."""
        config = LettaAdapterConfig(mcp=LettaMCPConfig(server_name="band-compose"))
        async with registered_on_real_server(config) as (bridge, client):
            (crashed_url,) = registered_urls(client)
            own_row = make_mock_mcp_server(
                "mcp-server-1", name="band-compose", url=crashed_url
            )
            client.mcp_servers.list.return_value = [own_row]
            await bridge.release(client)
            await crash_backend(bridge.backend)

            await bridge.ensure_ready(client)

            assert client.mcp_servers.create.await_count == 1
            assert bridge.server_id == "mcp-server-1"
            assert await serves_band_tools(registered_urls(client)[-1])

    @pytest.mark.asyncio
    async def test_a_transient_repoint_failure_is_retried_on_the_same_row(
        self,
    ) -> None:
        """Registering afresh would leave a second ``band-xxxx`` row and retag
        the org's tool rows, which repointing exists to avoid."""
        bridge, client = await registered_on_a_dead_fake_server()
        client.mcp_servers.update.side_effect = RuntimeError("Letta is down")

        with (
            backends_created_by(),
            pytest.raises(RuntimeError, match="Letta is down"),
        ):
            await bridge.ensure_ready(client)
        assert bridge.server_id == "mcp-server-1"

        client.mcp_servers.update.side_effect = None
        with backends_created_by():
            await bridge.ensure_ready(client)

        assert client.mcp_servers.create.await_count == 1
        assert bridge.ready

    @pytest.mark.asyncio
    async def test_a_repoint_of_a_deleted_row_registers_afresh(self) -> None:
        bridge, client = await registered_on_a_dead_fake_server()
        client.mcp_servers.update.side_effect = _stale_tool_error("no such server")

        with backends_created_by(), pytest.raises(Exception, match="no such server"):
            await bridge.ensure_ready(client)
        assert bridge.server_id is None

        client.mcp_servers.update.side_effect = None
        with backends_created_by():
            await bridge.ensure_ready(client)

        assert client.mcp_servers.create.await_count == 2
        assert bridge.ready

    @pytest.mark.asyncio
    async def test_a_cancelled_repoint_is_retried_on_the_same_row(self) -> None:
        """A turn interrupted mid-repoint keeps its registration, still at the
        dead URL, so the next turn repoints that row instead of trusting it."""
        bridge, client = await registered_on_a_dead_fake_server()
        client.mcp_servers.update.side_effect = [asyncio.CancelledError(), None]

        with backends_created_by():
            with pytest.raises(asyncio.CancelledError):
                await bridge.ensure_ready(client)
            await bridge.ensure_ready(client)

        assert client.mcp_servers.update.await_count == 2
        assert client.mcp_servers.create.await_count == 1
        assert bridge.ready
