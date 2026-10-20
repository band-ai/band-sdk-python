"""Transport close and host disposal share listener ownership."""

from __future__ import annotations

import asyncio

import pytest
from slack_sdk.socket_mode.aiohttp import SocketModeClient

from band.integrations.slack.adapter import SlackAdapter
from band.runtime.startup import dispose_adapter
from tests.integrations.slack.shutdown.kit import GatedBrain


class TestTransportConcurrency:
    @pytest.mark.parametrize("host_disposal", [False, True])
    async def test_concurrent_close_settles_the_same_connection(
        self,
        connected_socket_adapter: tuple[SlackAdapter, GatedBrain, SocketModeClient],
        host_disposal: bool,
    ) -> None:
        adapter, brain, client = connected_socket_adapter

        async def second_close() -> None:
            if host_disposal:
                await dispose_adapter(adapter)
            else:
                await adapter.close()

        await asyncio.gather(adapter.close(), second_close())

        assert client.current_session is not None
        assert client.current_session.closed
        assert brain.cleanup_calls == int(host_disposal)
        await adapter.close()
