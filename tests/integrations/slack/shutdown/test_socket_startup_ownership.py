"""Partial Socket Mode startup retains all clients until they settle."""

from __future__ import annotations

import asyncio

import pytest

from band.agent import Agent
from band.integrations.slack.adapter import SlackAdapter, SlackAdapterConfig
from tests.adaptercleanup import TeardownAdapter
from tests.core.agentlifecycle.kit import make_runtime
from tests.integrations.slack.shutdown.socketstartup import socket_startup_peer
from tests.integrations.slack.socketkit import _socket_app
from tests.integrations.slack.wrappingkit import _make_rest_mock


class TestSocketStartupOwnership:
    async def test_cancelled_second_connect_settles_every_client_before_resources(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async with socket_startup_peer() as peer:
            monkeypatch.setattr(
                "band.integrations.slack.socket._default_client_factory",
                peer.make_client,
            )
            adapter = SlackAdapter(
                SlackAdapterConfig(
                    apps=(_socket_app("first"), _socket_app("second")),
                    transport="socket",
                ),
                inner=TeardownAdapter(),
                rest_client=_make_rest_mock([]),
                web_client_factory=lambda app: peer.web_client,
            )
            releases = 0

            async def release_resource() -> None:
                nonlocal releases
                assert len(peer.clients) == 2
                assert peer.clients_settled
                releases += 1
                await peer.session.close()

            adapter.add_cleanup(release_resource)
            host = Agent(runtime=make_runtime(), adapter=adapter)
            starting = asyncio.create_task(host.start())
            try:
                await peer.second_connection.arrived()
                assert peer.clients[0].current_session is not None
                assert not peer.clients[0].current_session.closed
                starting.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await starting
                assert peer.session.closed
                assert releases == 1
                await host.stop()
                assert releases == 1
            finally:
                starting.cancel()
                await asyncio.gather(starting, return_exceptions=True)
                await peer.close_clients()
                await host.stop()
