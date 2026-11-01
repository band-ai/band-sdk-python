"""Local MCP listeners exclusively own their address until confirmed shutdown."""

from __future__ import annotations

import pytest

from band.integrations.mcp.local_server import LOCAL_MCP_HOST, LocalMCPServer
from tests.integrations.mcp.localserver.kit import (
    AUTH_TOKEN,
    call_echo,
    echo_tool_registration,
)
from tests.lifecycle import running
from tests.mcpclient import mcp_session
from tests.ports import HeldAdjacentPorts

CONTENDER_AUTH_TOKEN = f"{AUTH_TOKEN}-contender"


class TestPortOwnership:
    async def test_occupied_port_refuses_a_second_server_until_incumbent_stops(
        self,
    ) -> None:
        incumbent = LocalMCPServer(
            auth_token=AUTH_TOKEN,
            name="incumbent",
            tool_registrations=[echo_tool_registration()],
            port_min=0,
            port_max=0,
        )
        async with running(incumbent):
            contender = LocalMCPServer(
                auth_token=CONTENDER_AUTH_TOKEN,
                name="contender",
                tool_registrations=[echo_tool_registration()],
                port_min=incumbent.port,
                port_max=incumbent.port,
            )
            with pytest.raises(RuntimeError, match="Could not find a free"):
                await contender.start()
            async with mcp_session(
                incumbent.http_url, headers=incumbent.auth_headers
            ) as session:
                await call_echo(session, "incumbent still owns this address")

        async with (
            running(contender),
            mcp_session(contender.http_url, headers=contender.auth_headers) as session,
        ):
            await call_echo(session, "replacement owns the released address")

    async def test_scan_skips_an_occupied_candidate_and_serves_on_the_other(
        self,
    ) -> None:
        with HeldAdjacentPorts(LOCAL_MCP_HOST) as held:
            incumbent = LocalMCPServer(
                auth_token=AUTH_TOKEN,
                name="incumbent",
                tool_registrations=[echo_tool_registration()],
                port_min=held.port,
                port_max=held.port,
            )
            assert held.base is not None and held.neighbor is not None
            held.base.release()
            async with running(incumbent):
                contender = LocalMCPServer(
                    auth_token=CONTENDER_AUTH_TOKEN,
                    name="contender",
                    tool_registrations=[echo_tool_registration()],
                    port_min=held.port,
                    port_max=held.port + 1,
                    avoid_port=held.port + 1,
                )
                held.neighbor.release()
                async with running(contender):
                    assert contender.port == held.port + 1
                    async with mcp_session(
                        contender.http_url, headers=contender.auth_headers
                    ) as session:
                        await call_echo(session, "served by the available candidate")
