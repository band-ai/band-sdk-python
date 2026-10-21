"""Closing executions stop recovery before issuing another platform read."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from band_rest import AsyncRestClient

from tests.platform.settlement.kit import PEER_URL
from tests.runtime.recovery.kit import (
    Ending,
    Entrance,
    Fault,
    RoomPeer,
    ScriptedAdapter,
    Shape,
    wired_harness,
)

pytestmark = pytest.mark.looptime


class TestClosingRecovery:
    async def test_pending_write_does_not_restart_recovery_during_shutdown(
        self,
    ) -> None:
        peer = RoomPeer()
        adapter = ScriptedAdapter()
        adapter.ending = Ending.RAISES
        reads_after_closing: list[str] = []
        blocked_read = asyncio.Event()

        async def observe_request(request: httpx.Request) -> None:
            if room.ctx.is_closing and request.url.path.endswith("/messages/next"):
                reads_after_closing.append(request.url.path)
                # A blocked platform read lets shutdown deliver cancellation even
                # when a faulty scheduler would otherwise spin synchronously.
                await blocked_read.wait()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=peer.app),
            event_hooks={"request": [observe_request]},
        ) as http:
            rest = AsyncRestClient(
                api_key="test-key", base_url=PEER_URL, httpx_client=http
            )
            room = wired_harness(peer, adapter, rest, Shape.OFF)
            try:
                await room.outage_then(Fault.REFUSED, Entrance.CONTROL_PROBE)
                await room.ctx.request_resync()
                await room.ctx.stop()

                assert reads_after_closing == []
                assert room.model_turns == [room.members]
            finally:
                await room.runtime.stop()
