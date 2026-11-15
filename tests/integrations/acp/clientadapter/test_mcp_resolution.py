"""Resolved native MCP descriptors replace external configuration safely."""

from __future__ import annotations

from typing import Any

import pytest
from acp.schema import AcpMcpServer

from band.adapters.copilot_acp import CopilotACPAdapter, CopilotACPAdapterConfig
from band.adapters.cursor_acp import CursorACPAdapter, CursorACPAdapterConfig
from band.integrations.acp.client_adapter import ACPMcpServerResolutionError
from band.runtime.tools import BAND_MCP_SERVER_NAME
from tests.integrations.acp.acp_toolkit import (
    FakeACPAgent,
    acp_adapter,
    fake_agent_config,
    started_acp_adapter,
)
from tests.integrations.acp.acp_toolkit.mcpresolver import (
    SECRET,
    Resolver,
    http_server,
    server_names,
    sse_server,
    stdio_server,
)
from tests.mcpbackends import backends_created_by


class TestMcpResolution:
    """Only validated external descriptors replace the configured servers."""

    @pytest.mark.asyncio
    async def test_empty_result_is_authoritative(self) -> None:
        agent = FakeACPAgent(http=True)
        config = fake_agent_config(mcp_servers=[http_server(name="static")])

        async with acp_adapter(agent, config, resolve_mcp_servers=Resolver()) as acp:
            await acp.send("hello")

        assert agent.sessions[0]["mcp_servers"] == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("resolved", [[], [stdio_server()]], ids=["empty", "one"])
    async def test_configured_band_routing_is_kept(self, resolved: list[Any]) -> None:
        agent = FakeACPAgent(http=True)
        band = http_server(name=BAND_MCP_SERVER_NAME, url="http://127.0.0.1:9/band")
        config = fake_agent_config(mcp_servers=[http_server(name="static"), band])

        async with acp_adapter(
            agent, config, resolve_mcp_servers=Resolver(*resolved)
        ) as acp:
            await acp.send("hello")

        sent = agent.sessions[0]["mcp_servers"]
        assert server_names(sent) == [*server_names(resolved), BAND_MCP_SERVER_NAME]
        assert sent[-1].url == "http://127.0.0.1:9/band"
        assert sent[-1].headers[0].value == f"Bearer {SECRET}"

    @pytest.mark.asyncio
    async def test_injected_band_server_follows_resolved_servers(self) -> None:
        agent = FakeACPAgent(http=True)
        with backends_created_by():
            async with acp_adapter(
                agent,
                fake_agent_config(inject_band_tools=True),
                resolve_mcp_servers=Resolver(http_server()),
            ) as acp:
                await acp.send("hello")

        assert server_names(agent.sessions[0]["mcp_servers"]) == [
            "repo",
            BAND_MCP_SERVER_NAME,
        ]

    @pytest.mark.asyncio
    async def test_no_resolver_preserves_static_configuration(self) -> None:
        agent = FakeACPAgent(http=False, sse=False).will_say("ok")
        config = fake_agent_config(mcp_servers=[http_server(name="static")])

        async with acp_adapter(agent, config) as acp:
            reply = await acp.send("hello")

        assert reply.thoughts == ["ok"]
        assert server_names(agent.sessions[0]["mcp_servers"]) == ["static"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("http", "sse", "server", "transport"),
        [
            (False, False, http_server(), "http"),
            (False, True, http_server(), "http"),
            (True, False, sse_server(), "sse"),
        ],
    )
    async def test_unadvertised_transport_is_refused_without_secrets(
        self,
        http: bool,
        sse: bool,
        server: Any,
        transport: str,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        agent = FakeACPAgent(http=http, sse=sse)
        async with acp_adapter(agent, resolve_mcp_servers=Resolver(server)) as acp:
            with pytest.raises(ACPMcpServerResolutionError) as caught:
                await acp.send("hello")
            reported = acp.last_reply.errors

        message = str(caught.value)
        assert server.name in message and transport in message
        assert SECRET not in message and server.url not in message
        assert SECRET not in caplog.text and server.url not in caplog.text
        assert all(
            SECRET not in error and server.url not in error for error in reported
        )
        assert agent.sessions == []

    @pytest.mark.asyncio
    async def test_advertised_transports_and_stdio_are_accepted(self) -> None:
        agent = FakeACPAgent(http=True, sse=True)
        resolver = Resolver(http_server(), sse_server(), stdio_server())

        async with acp_adapter(agent, resolve_mcp_servers=resolver) as acp:
            await acp.send("hello")

        assert server_names(agent.sessions[0]["mcp_servers"]) == [
            "repo",
            "events",
            "tools",
        ]

    @pytest.mark.asyncio
    async def test_stdio_needs_no_remote_transport_advertisement(self) -> None:
        agent = FakeACPAgent(http=False, sse=False)
        async with acp_adapter(
            agent, resolve_mcp_servers=Resolver(stdio_server())
        ) as acp:
            await acp.send("hello")

        assert server_names(agent.sessions[0]["mcp_servers"]) == ["tools"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "servers",
        [
            [http_server(name="repo"), stdio_server(name="repo")],
            [http_server(name=BAND_MCP_SERVER_NAME)],
            [AcpMcpServer(type="acp", name="peer", server_id="peer-1")],
        ],
        ids=["duplicate", "band", "acp-transport"],
    )
    async def test_unacceptable_servers_fail_setup(self, servers: list[Any]) -> None:
        agent = FakeACPAgent(http=True)
        config = fake_agent_config(mcp_servers=[http_server(name="static")])

        async with acp_adapter(
            agent, config, resolve_mcp_servers=Resolver(*servers)
        ) as acp:
            with pytest.raises(ACPMcpServerResolutionError):
                await acp.send("hello")

        assert agent.sessions == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "returned", ["repo", b"repo", [{"name": "repo"}], None, iter(())]
    )
    async def test_non_descriptor_results_are_type_errors(self, returned: Any) -> None:
        async def resolver(workspace: str) -> Any:
            return returned

        agent = FakeACPAgent(http=True)
        async with acp_adapter(agent, resolve_mcp_servers=resolver) as acp:
            with pytest.raises(TypeError):
                await acp.send("hello")

        assert agent.sessions == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("adapter", ["copilot", "cursor"])
    async def test_backend_constructors_use_the_shared_resolver(
        self, adapter: str
    ) -> None:
        resolver = Resolver(http_server())
        agent = FakeACPAgent(http=True)
        match adapter:
            case "copilot":
                client = CopilotACPAdapter(
                    CopilotACPAdapterConfig(
                        command="fake-agent", inject_band_tools=False
                    ),
                    resolve_mcp_servers=resolver,
                )
            case "cursor":
                client = CursorACPAdapter(
                    CursorACPAdapterConfig(
                        command="fake-agent", inject_band_tools=False
                    ),
                    resolve_mcp_servers=resolver,
                )
            case _:
                raise AssertionError(adapter)

        async with started_acp_adapter(client, agent) as acp:
            await acp.send("hello")
            workspace = acp.room().workspace

        assert resolver.workspaces == [workspace]
        assert server_names(agent.sessions[0]["mcp_servers"]) == ["repo"]
