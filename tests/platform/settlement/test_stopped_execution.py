"""A stopped execution (HTTP 204) and cancellation are never settlement results."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import RoomExecutionStoppedError
from band.platform.link import BandLink
from tests.platform.settlement.kit import Reply, SettlementPeer, Terminal


class TestStoppedExecution:
    async def test_a_204_raises_on_the_detailed_and_legacy_apis(
        self, terminal: Terminal, peer: SettlementPeer, link: BandLink
    ) -> None:
        peer.reply = Reply(204)

        with pytest.raises(RoomExecutionStoppedError):
            await terminal.detailed(link)
        with pytest.raises(RoomExecutionStoppedError):
            await terminal.legacy(link)

    async def test_cancellation_propagates_from_both_apis(
        self, terminal: Terminal, peer: SettlementPeer, link: BandLink
    ) -> None:
        peer.reply = None
        for call in (terminal.detailed, terminal.legacy):
            peer.arrived.clear()
            task = asyncio.create_task(call(link))
            await asyncio.wait_for(peer.arrived.wait(), timeout=5)

            task.cancel()

            with pytest.raises(asyncio.CancelledError):
                await task
