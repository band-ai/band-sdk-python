"""Tests for ACPGateway host."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from band.agent import Agent
from band.core.protocols import Gateway
from band.integrations.acp.host import ACPGateway
from band.integrations.acp.server import ACPServer
from band.integrations.acp.server_adapter import BandACPServerAdapter


def make_gateway_agent() -> tuple[Agent, BandACPServerAdapter, AsyncMock]:
    runtime = AsyncMock()
    runtime.agent_name = "gw"
    runtime.agent_description = "gateway"
    runtime.stop.return_value = True
    runtime.claim_single_instance = MagicMock()
    runtime.release_single_instance = MagicMock()
    adapter = BandACPServerAdapter(rest_url="https://app.band.ai", api_key="test")
    adapter.close = AsyncMock()  # type: ignore[method-assign]
    agent = Agent(runtime=runtime, adapter=adapter)
    return agent, adapter, runtime


class TestACPGateway:
    def test_static_gateway_protocol(self) -> None:
        agent, _, _ = make_gateway_agent()
        gateway: Gateway = ACPGateway(agent=agent)
        assert isinstance(gateway, ACPGateway)

    def test_wrong_adapter_type_raises_in_init(self) -> None:
        runtime = AsyncMock()
        adapter = AsyncMock()
        agent = Agent(runtime=runtime, adapter=adapter)  # type: ignore[arg-type]

        with pytest.raises(TypeError, match="BandACPServerAdapter"):
            ACPGateway(agent=agent)

    def test_creates_server_when_not_passed(self) -> None:
        agent, adapter, _ = make_gateway_agent()
        gateway = ACPGateway(agent=agent)
        assert isinstance(gateway._server, ACPServer)
        assert gateway._server._adapter is adapter  # noqa: SLF001

    def test_uses_passed_server(self) -> None:
        agent, adapter, _ = make_gateway_agent()
        server = ACPServer(adapter)
        gateway = ACPGateway(agent=agent, server=server)
        assert gateway._server is server

    @pytest.mark.asyncio
    async def test_start_starts_agent(self) -> None:
        agent, _, runtime = make_gateway_agent()
        gateway = ACPGateway(agent=agent)

        await gateway.start()

        runtime.start.assert_awaited_once()
        assert gateway.state == "started"

    @pytest.mark.asyncio
    async def test_serve_delegates_to_run_agent(self) -> None:
        agent, adapter, _ = make_gateway_agent()
        server = ACPServer(adapter)
        gateway = ACPGateway(agent=agent, server=server)

        with patch("acp.run_agent", new=AsyncMock()) as mock_run:
            await gateway.start()
            await gateway.serve()

        mock_run.assert_awaited_once_with(server)

    @pytest.mark.asyncio
    async def test_stop_closes_adapter_and_stops_agent(self) -> None:
        agent, adapter, runtime = make_gateway_agent()
        gateway = ACPGateway(agent=agent)

        await gateway.start()
        await gateway.stop()

        adapter.close.assert_awaited_once()  # type: ignore[attr-defined]
        runtime.stop.assert_awaited_once()
        assert gateway.state == "stopped"

    @pytest.mark.asyncio
    async def test_context_manager_lifecycle(self) -> None:
        agent, adapter, runtime = make_gateway_agent()
        gateway = ACPGateway(agent=agent)

        with patch("acp.run_agent", new=AsyncMock()):
            async with gateway:
                assert gateway.state == "started"
                await gateway.serve()

        adapter.close.assert_awaited_once()  # type: ignore[attr-defined]
        runtime.stop.assert_awaited_once()
        assert gateway.state == "stopped"
