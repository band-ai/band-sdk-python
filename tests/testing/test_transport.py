"""Tests for the force_transport_disconnect testing utility."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from band.platform.link import BandLink
from band.testing import fake_phoenix_server, force_transport_disconnect
from tests.conftest import spy_on_reconciliation_drain


def make_link() -> BandLink:
    return BandLink(agent_id="agent-123", api_key="test-key")


@pytest.mark.usefixtures("millisecond_reconnects")
class TestForceTransportDisconnect:
    async def test_the_aborted_link_reconnects(self) -> None:
        """An abort carries no close handshake -- what a real network drop
        looks like -- so the transport's own reconnect runs."""
        async with fake_phoenix_server() as server:
            link = BandLink(
                agent_id="agent-123",
                api_key="test-key",
                ws_url=server.url,
                rest_url="https://test.invalid",
            )
            await link.connect()
            reconnected = spy_on_reconciliation_drain(link)

            await force_transport_disconnect(link)
            await asyncio.wait_for(reconnected.wait(), timeout=5.0)
            await link.disconnect()

            assert server.connection_count == 2

    async def test_raises_when_never_connected(self) -> None:
        """No ``_ws`` at all — a caller-side setup mistake, not a no-op."""
        link = make_link()

        with pytest.raises(RuntimeError, match="no active transport connection"):
            await force_transport_disconnect(link)

    async def test_raises_when_client_not_yet_established(self) -> None:
        """``_ws`` exists but its inner PHXChannelsClient hasn't been created yet."""
        link = make_link()
        ws = MagicMock()
        ws.client = None
        link._ws = ws

        with pytest.raises(RuntimeError, match="no active transport connection"):
            await force_transport_disconnect(link)

    async def test_raises_when_connection_not_yet_open(self) -> None:
        """``client`` exists but hasn't opened its socket yet."""
        link = make_link()
        ws = MagicMock()
        ws.client = MagicMock(connection=None)
        link._ws = ws

        with pytest.raises(RuntimeError, match="no active transport connection"):
            await force_transport_disconnect(link)
