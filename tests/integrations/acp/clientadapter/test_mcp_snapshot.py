"""Each ACP session setup consumes one owned MCP resolution snapshot."""

from __future__ import annotations

from typing import Any

import pytest
from acp.schema import LoadSessionResponse

from band.integrations.acp.client_types import ACPClientSessionState
from tests.integrations.acp.acp_toolkit import (
    DEFAULT_ROOM,
    FakeACPAgent,
    acp_adapter,
    fake_agent_config,
)
from tests.integrations.acp.acp_toolkit.mcpresolver import (
    SECRET,
    Resolver,
    http_server,
    server_names,
    stdio_server,
)
from tests.integrations.acp.roomownership.kit import recover, woken
from tests.mcpclient import crash_backend


class TestMcpSnapshot:
    """Reused sessions keep their servers; new setups resolve a fresh snapshot."""

    @pytest.mark.asyncio
    async def test_resolved_servers_replace_configured_ones_once_per_session(
        self,
    ) -> None:
        agent = FakeACPAgent(http=True)
        resolver = Resolver(http_server(), stdio_server())
        config = fake_agent_config(mcp_servers=[http_server(name="static")])

        async with acp_adapter(agent, config, resolve_mcp_servers=resolver) as acp:
            await acp.send("one")
            await acp.send("two")
            workspace = acp.room().workspace

        assert resolver.workspaces == [workspace]
        assert len(agent.sessions) == 1
        sent = agent.sessions[0]["mcp_servers"]
        assert server_names(sent) == ["repo", "tools"]
        assert sent[0].headers[0].value == f"Bearer {SECRET}"
        assert sent[1].env[0].value == SECRET

    @pytest.mark.asyncio
    async def test_each_fresh_connection_resolves_anew(self) -> None:
        agent = FakeACPAgent(http=True)
        resolver = Resolver(http_server(url="http://127.0.0.1:9/grant-a"))

        async with acp_adapter(agent, resolve_mcp_servers=resolver) as acp:
            await acp.send("one")
            await acp.adapter.on_cleanup(DEFAULT_ROOM)
            resolver.servers = [http_server(url="http://127.0.0.1:9/grant-b")]
            await acp.send("two")
            await acp.send("three")

        assert len(resolver.workspaces) == 2
        assert [s["mcp_servers"][0].url for s in agent.sessions] == [
            "http://127.0.0.1:9/grant-a",
            "http://127.0.0.1:9/grant-b",
        ]

    @pytest.mark.asyncio
    async def test_load_and_fresh_fallback_share_one_deep_snapshot(self) -> None:
        agent = FakeACPAgent(http=True, supports_session_load=True)
        remote = http_server(url="http://127.0.0.1:9/original")
        local = stdio_server()
        resolver = Resolver(remote, local)
        loaded_with: list[Any] = []
        load = agent.load_session

        async def load_session(
            cwd: str, session_id: str, mcp_servers: Any = None, **kwargs: Any
        ) -> LoadSessionResponse:
            loaded_with.extend(mcp_servers or [])
            remote.url = "http://127.0.0.1:9/changed"
            remote.headers[0].value = "Bearer rotated"
            local.args.append("--changed")
            local.env[0].value = "rotated"
            return await load(cwd, session_id, mcp_servers, **kwargs)

        agent.load_session = load_session  # type: ignore[method-assign]
        history = ACPClientSessionState(room_to_session={DEFAULT_ROOM: "gone"})

        async with acp_adapter(agent, resolve_mcp_servers=resolver) as acp:
            await acp.send("hello", history=history, bootstrap=True)

        assert len(resolver.workspaces) == 1
        loaded_remote, loaded_local = loaded_with
        assert loaded_remote.url == "http://127.0.0.1:9/original"
        assert loaded_remote.headers[0].value == f"Bearer {SECRET}"
        assert loaded_local.args == ["mcp"]
        assert loaded_local.env[0].value == SECRET
        assert agent.sessions[0]["mcp_servers"] == loaded_with

    @pytest.mark.asyncio
    @pytest.mark.parametrize("wake", [False, True], ids=["recovery", "deliberate-wake"])
    async def test_a_reloaded_retained_session_resolves_once_on_its_new_connection(
        self, wake: bool
    ) -> None:
        agent = FakeACPAgent(http=True, supports_session_load=True)
        resolver = Resolver(http_server(url="http://127.0.0.1:9/grant-a"))
        loaded_with: list[Any] = []
        load = agent.load_session

        async def load_session(
            cwd: str, session_id: str, mcp_servers: Any = None, **kwargs: Any
        ) -> LoadSessionResponse:
            loaded_with.extend(mcp_servers or [])
            return await load(cwd, session_id, mcp_servers, **kwargs)

        agent.load_session = load_session  # type: ignore[method-assign]
        async with acp_adapter(agent, resolve_mcp_servers=resolver) as acp:
            await acp.send("one")
            retained_id = acp.session_id(DEFAULT_ROOM)
            agent.knows_session(retained_id)
            room = await recover(acp)
            resolver.servers = [http_server(url="http://127.0.0.1:9/grant-b")]
            if wake:
                async with woken(acp.adapter, room):
                    assert room.session is not None and room.session.live
                    assert len(resolver.workspaces) == 2
            else:
                await acp.send("two")
                await acp.send("three")
                assert acp.session_id(DEFAULT_ROOM) == retained_id

        assert len(resolver.workspaces) == 2
        assert loaded_with[0].url == "http://127.0.0.1:9/grant-b"
        assert agent.session_load_requests == [retained_id]
        assert len(agent.sessions) == 1

    @pytest.mark.asyncio
    async def test_a_replaced_band_endpoint_resolves_a_new_session_snapshot(
        self,
    ) -> None:
        agent = FakeACPAgent(http=True)
        resolver = Resolver(http_server(url="http://127.0.0.1:9/grant-a"))
        async with acp_adapter(
            agent,
            fake_agent_config(inject_band_tools=True),
            resolve_mcp_servers=resolver,
        ) as acp:
            await acp.send("one")
            resolver.servers = [http_server(url="http://127.0.0.1:9/grant-b")]
            await crash_backend(acp.adapter._mcp)
            await acp.send("two")
            await acp.send("three")

        assert len(resolver.workspaces) == 2
        assert [s["mcp_servers"][0].url for s in agent.sessions] == [
            "http://127.0.0.1:9/grant-a",
            "http://127.0.0.1:9/grant-b",
        ]
        assert (
            agent.sessions[0]["mcp_servers"][-1].url
            != agent.sessions[1]["mcp_servers"][-1].url
        )
