"""Tests for the force_transport_disconnect testing utility."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.platform.link import BandLink
from band.testing import force_transport_disconnect


def make_link() -> BandLink:
    return BandLink(agent_id="agent-123", api_key="test-key")


async def test_closes_the_live_transport_connection() -> None:
    """The happy path: closes the real connection under a connected link."""
    link = make_link()
    connection = AsyncMock()
    ws = MagicMock()
    ws.client = MagicMock(connection=connection)
    link._ws = ws

    await force_transport_disconnect(link)

    connection.close.assert_awaited_once()


async def test_raises_when_never_connected() -> None:
    """No ``_ws`` at all — a caller-side setup mistake, not a no-op."""
    link = make_link()

    with pytest.raises(RuntimeError, match="no active transport connection"):
        await force_transport_disconnect(link)


async def test_raises_when_client_not_yet_established() -> None:
    """``_ws`` exists but its inner PHXChannelsClient hasn't been created yet."""
    link = make_link()
    ws = MagicMock()
    ws.client = None
    link._ws = ws

    with pytest.raises(RuntimeError, match="no active transport connection"):
        await force_transport_disconnect(link)


async def test_raises_when_connection_not_yet_open() -> None:
    """``client`` exists but hasn't opened its socket yet."""
    link = make_link()
    ws = MagicMock()
    ws.client = MagicMock(connection=None)
    link._ws = ws

    with pytest.raises(RuntimeError, match="no active transport connection"):
        await force_transport_disconnect(link)
