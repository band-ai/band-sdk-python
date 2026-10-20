"""Own adapters, their event readers and fake transport clients in tests."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, nullcontext

import looptime
from typing_extensions import Unpack

from band.adapters.opencode import OpencodeAdapterConfig
from band.core.types import FeatureKwargs
from band.runtime.custom_tools import CustomToolDef
from tests.adapters.opencode.helpers import FakeOpencodeClient, ObservedOpencodeAdapter


def turn_watchers(adapter: ObservedOpencodeAdapter) -> set[asyncio.Task[None]]:
    turns = (room.turn for room in adapter._rooms.values())
    return {turn.watcher for turn in turns if turn and turn.watcher}


async def close_adapter(
    adapter: ObservedOpencodeAdapter, client: FakeOpencodeClient, *, virtual: bool
) -> None:
    loop = asyncio.get_running_loop()
    mode = (
        looptime.enabled(strict=True)
        if virtual
        and isinstance(loop, looptime.LoopTimeEventLoop)
        and not loop.looptime_on
        else nullcontext()
    )
    with mode:
        tasks = adapter.background_tasks | turn_watchers(adapter)
        if adapter._event_task is not None:
            tasks.add(adapter._event_task)
        try:
            async with AsyncExitStack() as rooms:
                for room_id in list(adapter._rooms):
                    rooms.push_async_callback(adapter.on_cleanup, room_id)
        finally:
            # A backend close can fail after the adapter detached these tasks.
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if not client.closed:
                await client.close()
        assert adapter._rooms == {}
        assert adapter._registration is None
        assert adapter._event_task is None


class AdapterOwner(AsyncExitStack):
    """Close every created adapter, even when another adapter's cleanup fails."""

    def create(
        self,
        client: FakeOpencodeClient,
        *,
        config: OpencodeAdapterConfig | None = None,
        additional_tools: list[CustomToolDef] | None = None,
        **features: Unpack[FeatureKwargs],
    ) -> ObservedOpencodeAdapter:
        adapter = ObservedOpencodeAdapter(
            config=config,
            additional_tools=additional_tools,
            client_factory=lambda _: client,
            **features,
        )
        loop = asyncio.get_running_loop()
        self.push_async_callback(
            close_adapter,
            adapter,
            client,
            virtual=isinstance(loop, looptime.LoopTimeEventLoop) and loop.looptime_on,
        )
        return adapter
