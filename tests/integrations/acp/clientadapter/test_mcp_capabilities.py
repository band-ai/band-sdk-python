"""Remote MCP support comes only from explicit initialize advertisements."""

from __future__ import annotations

import pytest
from acp.schema import AgentCapabilities, InitializeResponse, McpCapabilities

from band.integrations.acp.client_runtime import ACPRuntime
from tests.integrations.acp.acp_toolkit.harness import FakeSpawn


class TestMcpCapabilities:
    """Band's preferred transport is not an agent's advertised support."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("mcp", "http", "sse"),
        [
            (None, False, False),
            (McpCapabilities(), False, False),
            (McpCapabilities(http=None, sse=None), False, False),
            (McpCapabilities(http=False, sse=False), False, False),
            (McpCapabilities(http=True), True, False),
            (McpCapabilities(sse=True), False, True),
            (McpCapabilities(http=True, sse=True), True, True),
        ],
    )
    async def test_support_is_only_what_the_agent_advertises(
        self, mcp: McpCapabilities | None, http: bool, sse: bool
    ) -> None:
        spawn = FakeSpawn()
        spawn.conn.initialize.return_value = InitializeResponse(
            protocol_version=1,
            agent_capabilities=AgentCapabilities(mcp_capabilities=mcp),
        )
        runtime = ACPRuntime(command=["agent"], spawn_process=spawn)
        try:
            await runtime.start()
            assert (runtime.agent_mcp_http, runtime.agent_mcp_sse) == (http, sse)
        finally:
            await runtime.stop()

        assert (runtime.agent_mcp_http, runtime.agent_mcp_sse) == (False, False)
