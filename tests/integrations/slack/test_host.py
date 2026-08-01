"""Tests for SlackGateway host."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from band.agent import Agent
from band.core.protocols import Gateway
from band.core.simple_adapter import SimpleAdapter
from band.integrations.slack.adapter import SlackAdapter
from band.integrations.slack.host import SlackGateway
from band.integrations.slack.types import SlackApp


class _SlackReplyBrain(SimpleAdapter[Any]):
    async def on_message(self, *args: Any, **kwargs: Any) -> None:
        return None


def _socket_app(slug: str = "dev") -> SlackApp:
    return SlackApp(
        slug=slug,
        bot_token="xoxb-test",
        signing_secret="",
        app_token="xapp-test",
    )


def _http_app(slug: str = "dev") -> SlackApp:
    return SlackApp(
        slug=slug,
        bot_token="xoxb-test",
        signing_secret="secret",
        app_token="",
    )


def make_gateway_agent(
    *,
    transport: str = "socket",
    manage_ingress: bool = True,
) -> tuple[Agent, SlackAdapter, AsyncMock]:
    runtime = AsyncMock()
    runtime.agent_name = "gw"
    runtime.agent_description = "gateway"
    runtime.stop.return_value = True
    runtime.claim_single_instance = MagicMock()
    runtime.release_single_instance = MagicMock()
    app = _http_app() if transport == "http" else _socket_app()
    adapter = SlackAdapter(
        inner=_SlackReplyBrain(),  # type: ignore[arg-type]
        apps=[app],
        api_key="test",
        transport=transport,  # type: ignore[arg-type]
        manage_ingress=manage_ingress,
        rest_client=MagicMock(),
    )
    adapter.start_ingress = AsyncMock()  # type: ignore[method-assign]
    adapter.close = AsyncMock()  # type: ignore[method-assign]
    agent = Agent(runtime=runtime, adapter=adapter)
    return agent, adapter, runtime


class TestSlackGateway:
    def test_static_gateway_protocol(self) -> None:
        agent, _, _ = make_gateway_agent()
        gateway: Gateway = SlackGateway(agent=agent)
        assert isinstance(gateway, SlackGateway)

    def test_wrong_adapter_type_raises_in_init(self) -> None:
        runtime = AsyncMock()
        adapter = AsyncMock()
        agent = Agent(runtime=runtime, adapter=adapter)  # type: ignore[arg-type]

        with pytest.raises(TypeError, match="SlackAdapter"):
            SlackGateway(agent=agent)

    @pytest.mark.asyncio
    async def test_start_defers_ingress_and_starts_agent(self) -> None:
        agent, adapter, runtime = make_gateway_agent()
        gateway = SlackGateway(agent=agent)

        await gateway.start()

        assert adapter.manage_ingress is False
        adapter.start_ingress.assert_not_awaited()
        runtime.start.assert_awaited_once()
        assert gateway.state == "started"

    @pytest.mark.asyncio
    async def test_serve_socket_starts_ingress_and_blocks_until_stop(self) -> None:
        agent, adapter, _runtime = make_gateway_agent()
        gateway = SlackGateway(agent=agent)

        await gateway.start()
        serve_task = asyncio.create_task(gateway.serve())
        await asyncio.sleep(0)

        adapter.start_ingress.assert_awaited_once()
        assert not serve_task.done()

        await gateway.stop()
        await serve_task

        adapter.close.assert_awaited()
        assert gateway.state == "stopped"

    @pytest.mark.asyncio
    async def test_serve_http_runs_uvicorn_on_router(self) -> None:
        agent, adapter, _runtime = make_gateway_agent(transport="http")
        gateway = SlackGateway(agent=agent)

        mock_server = MagicMock()
        mock_server.serve = AsyncMock()
        mock_config = MagicMock()

        with (
            patch("uvicorn.Config", return_value=mock_config) as mock_config_cls,
            patch("uvicorn.Server", return_value=mock_server) as mock_server_cls,
        ):
            await gateway.start()
            await gateway.serve()

        served_app, kwargs = mock_config_cls.call_args
        assert served_app == (adapter.router,)
        assert kwargs["host"] == "0.0.0.0"
        assert kwargs["port"] == adapter.port
        mock_server_cls.assert_called_once_with(mock_config)
        mock_server.serve.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_closes_adapter_and_stops_agent(self) -> None:
        agent, adapter, runtime = make_gateway_agent()
        gateway = SlackGateway(agent=agent)

        await gateway.start()
        await gateway.stop()

        adapter.close.assert_awaited_once()
        runtime.stop.assert_awaited_once()
        assert gateway.state == "stopped"

    @pytest.mark.asyncio
    async def test_manage_ingress_false_skips_listeners_in_on_started(self) -> None:
        adapter = SlackAdapter(
            inner=_SlackReplyBrain(),  # type: ignore[arg-type]
            apps=[_socket_app()],
            api_key="test",
            transport="socket",
            manage_ingress=False,
            rest_client=MagicMock(),
        )
        adapter.start_ingress = AsyncMock()  # type: ignore[method-assign]

        await adapter.on_started("MyBot", "")

        adapter.start_ingress.assert_not_awaited()
        assert adapter._socket_mode_listeners == []

    @pytest.mark.asyncio
    async def test_manage_ingress_true_warns_and_starts_in_on_started(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = SlackAdapter(
            inner=_SlackReplyBrain(),  # type: ignore[arg-type]
            apps=[_socket_app()],
            api_key="test",
            transport="socket",
            manage_ingress=True,
            rest_client=MagicMock(),
        )
        adapter.start_ingress = AsyncMock()  # type: ignore[method-assign]
        warnings: list[str] = []

        def capture(old: str, new: str, **kwargs: object) -> None:
            warnings.append(f"{old} -> {new}")

        monkeypatch.setattr(
            "band.integrations.slack.adapter.warn_deprecated",
            capture,
        )

        await adapter.on_started("MyBot", "")

        assert warnings
        adapter.start_ingress.assert_awaited_once()


@pytest.mark.asyncio
async def test_socket_stop_before_cancel_event_does_not_hang() -> None:
    """stop() during start_ingress must still unblock serve()."""
    from unittest.mock import AsyncMock, MagicMock

    from band.core.gateways import GatewayBase
    from band.integrations.slack.host import SlackGateway

    adapter = MagicMock()
    adapter.transport = "socket"
    started = asyncio.Event()

    async def slow_ingress() -> None:
        started.set()
        await asyncio.sleep(0.05)

    adapter.start_ingress = slow_ingress
    agent = MagicMock()
    agent.adapter = adapter
    agent.is_running = True
    agent.start = AsyncMock()
    agent.stop = AsyncMock()

    gateway = object.__new__(SlackGateway)
    GatewayBase.__init__(gateway, agent)  # type: ignore[arg-type]
    gateway._serve_cancel = None
    gateway._http = None
    gateway._state = "started"

    async def serve_socket() -> None:
        async with gateway._cancellation_gate():
            await adapter.start_ingress()

    serve_task = asyncio.create_task(serve_socket())
    await started.wait()
    await gateway._stop_resources()
    await asyncio.wait_for(serve_task, timeout=1.0)
