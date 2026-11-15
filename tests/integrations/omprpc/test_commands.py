"""Command responses correlate independently and recover from errors or cancellation."""

from __future__ import annotations

import asyncio

import pytest

from band.integrations.omprpc import OmpRpcError
from tests.integrations.omppeers.base import COMPLETE
from tests.integrations.omprpc.events import follow_up_texts
from tests.integrations.omprpc.spawn import ClientFactory
from tests.integrations.ompscenarios import PeerCommand, PeerScenario


class TestCommandCorrelation:
    async def test_command_responses_are_correlated_out_of_order_and_errors_are_recoverable(
        self,
        client_factory: ClientFactory,
    ) -> None:
        client = await client_factory(PeerScenario.STANDARD_TURN)
        async with asyncio.timeout(5):
            first, second = await asyncio.gather(
                client.request(PeerCommand.FIRST), client.request(PeerCommand.SECOND)
            )
        assert (first, second) == ({"value": "first"}, {"value": "second"})
        with pytest.raises(OmpRpcError, match="Command failed") as failed:
            await client.request(PeerCommand.FAIL_REQUEST)
        assert (failed.value.command, failed.value.code) == ("error", "bad_command")
        assert await follow_up_texts(client) == [COMPLETE]

    async def test_cancelled_command_drops_only_its_waiter(
        self,
        client_factory: ClientFactory,
    ) -> None:
        client = await client_factory(PeerScenario.STANDARD_TURN)
        pending = asyncio.create_task(client.request(PeerCommand.HOLD))
        await client.request(PeerCommand.CHECKPOINT)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert await client.request(PeerCommand.SECOND) == {"value": "second"}
        assert await follow_up_texts(client) == [COMPLETE]
