"""SDK reconnect work releases borrowed tool resources only after settlement."""

from __future__ import annotations

import asyncio

import pytest

from tests.adaptercleanup import HostKind
from tests.integrations.slack.shutdown.socketresource import SocketResourceHost


class TestSocketResourceSettlement:
    @pytest.mark.parametrize(
        "refresh", [False, True], ids=["connection-loss", "refresh-message"]
    )
    async def test_reconnect_finalizer_keeps_shared_tool_session(
        self, socket_resource_host: SocketResourceHost, refresh: bool
    ) -> None:
        peer, host, client = (
            socket_resource_host.peer,
            socket_resource_host.host,
            socket_resource_host.client,
        )
        assert await peer.read() == "open"
        await peer.reconnect(refresh=refresh)
        stopping = asyncio.create_task(host.stop())
        try:
            await peer.finalizing.wait()
            assert not stopping.done()
            assert await peer.read() == "open"
        finally:
            peer.release.set()
            await stopping
        assert peer.finished.is_set()
        assert peer.session.closed
        await client.connect_to_new_endpoint(force=True)
        assert client.aiohttp_client_session.closed

    async def test_cancelled_waiter_preserves_the_hosts_shutdown_policy(
        self, socket_resource_host: SocketResourceHost
    ) -> None:
        peer, host = socket_resource_host.peer, socket_resource_host.host
        await peer.reconnect(refresh=True)
        stopping = asyncio.create_task(host.stop())
        try:
            await peer.finalizing.wait()
            stopping.cancel()
            with pytest.raises(asyncio.CancelledError):
                await stopping
            assert await peer.read() == "open"
        finally:
            peer.release.set()
            await asyncio.gather(stopping, return_exceptions=True)
        await host.stop()
        assert peer.session.closed
        if socket_resource_host.kind == HostKind.ONESHOT:
            assert peer.finished.is_set()
