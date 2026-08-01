"""Tests for A2AGateway host."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from band.agent import Agent
from band.core.exceptions import LifecycleError
from band.core.protocols import Gateway
from band.integrations.a2a.gateway import A2AGateway, A2AGatewayAdapter


def make_gateway_agent() -> tuple[Agent, A2AGatewayAdapter, AsyncMock]:
    runtime = AsyncMock()
    runtime.agent_name = "gw"
    runtime.agent_description = "gateway"
    runtime.stop.return_value = True
    runtime.claim_single_instance = MagicMock()
    runtime.release_single_instance = MagicMock()
    adapter = A2AGatewayAdapter(api_key="test")
    agent = Agent(runtime=runtime, adapter=adapter)
    return agent, adapter, runtime


class TestA2AGateway:
    @pytest.mark.asyncio
    async def test_start_prepares_server_without_background_http(self) -> None:
        agent, adapter, runtime = make_gateway_agent()
        mock_response = MagicMock()
        mock_response.data = []
        adapter._rest.agent_api_peers.list_agent_peers = AsyncMock(
            return_value=mock_response
        )

        with patch(
            "band.integrations.a2a.gateway.adapter.GatewayServer"
        ) as mock_server_cls:
            mock_server = MagicMock()
            mock_server.start = AsyncMock()
            mock_server.serve = AsyncMock()
            mock_server.stop = AsyncMock()
            mock_server_cls.return_value = mock_server

            gateway = A2AGateway(agent=agent)
            await gateway.start()

        assert adapter.manage_http_server is False
        assert gateway._server is mock_server
        mock_server.start.assert_not_called()
        runtime.start.assert_awaited()

    @pytest.mark.asyncio
    async def test_serve_delegates_to_http_server(self) -> None:
        agent, adapter, _runtime = make_gateway_agent()
        mock_response = MagicMock()
        mock_response.data = []
        adapter._rest.agent_api_peers.list_agent_peers = AsyncMock(
            return_value=mock_response
        )

        with patch(
            "band.integrations.a2a.gateway.adapter.GatewayServer"
        ) as mock_server_cls:
            mock_server = MagicMock()
            mock_server.start = AsyncMock()
            mock_server.serve = AsyncMock()
            mock_server.stop = AsyncMock()
            mock_server_cls.return_value = mock_server

            gateway = A2AGateway(agent=agent)
            await gateway.start()
            await gateway.serve()

        mock_server.serve.assert_awaited_once()

    def test_wrong_adapter_type_raises(self) -> None:
        runtime = AsyncMock()
        adapter = AsyncMock()
        agent = Agent(runtime=runtime, adapter=adapter)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="A2AGatewayAdapter"):
            A2AGateway(agent=agent)

    @pytest.mark.asyncio
    async def test_missing_http_server_raises(self) -> None:
        agent, adapter, _runtime = make_gateway_agent()
        gateway = A2AGateway(agent=agent)

        with patch.object(agent, "start", AsyncMock()):
            adapter._server = None
            with pytest.raises(LifecycleError, match="did not prepare HTTP server"):
                await gateway.start()

    def test_static_gateway_protocol(self) -> None:
        agent, _, _ = make_gateway_agent()
        gateway: Gateway = A2AGateway(agent=agent)
        assert isinstance(gateway, A2AGateway)
