"""Command responses correlate independently and recover from errors or cancellation."""

from __future__ import annotations

import asyncio

import pytest

from band.integrations.omprpc import OmpRpcError
from tests.integrations.omprpc.kit import ClientFactory, second_prompt


class TestCommandCorrelation:
    async def test_command_responses_are_correlated_out_of_order_and_errors_are_recoverable(
        self,
        client_factory: ClientFactory,
    ) -> None:
        client = await client_factory("basic")
        async with asyncio.timeout(5):
            first, second = await asyncio.gather(
                client.request("first"), client.request("second")
            )
        assert (first, second) == ({"value": "first"}, {"value": "second"})
        with pytest.raises(OmpRpcError, match="Command failed") as failed:
            await client.request("error")
        assert (failed.value.command, failed.value.code) == ("error", "bad_command")
        await second_prompt(client)

    async def test_cancelled_command_drops_only_its_waiter(
        self,
        client_factory: ClientFactory,
    ) -> None:
        client = await client_factory("basic")
        pending = asyncio.create_task(client.request("hold"))
        await client.request("checkpoint")
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert await client.request("second") == {"value": "second"}
        await second_prompt(client)
