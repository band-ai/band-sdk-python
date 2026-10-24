"""Callback overload fails closed without blocking native control correlation."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from band.integrations.omprpc import OmpProcessError, OmpRpcClient
from band.integrations.omprpc.errors import BACKLOG_EXCEEDED
from band.integrations.omprpc.prompt import EVENT_BACKLOG_LIMIT
from band.integrations.ompwire import (
    OmpEvent,
    OmpEventType,
    OmpUIRequest,
    OmpUIResponse,
)
from tests.integrations.omppeer import COMPLETE, UI_APPROVE, burst_message_id
from tests.integrations.omprpc.kit import (
    ClientFactory,
    Observer,
    owned_rpc_process,
    second_prompt,
)


@asynccontextmanager
async def held_event_prompt(
    client: OmpRpcClient,
) -> AsyncIterator[
    tuple[asyncio.Task[None], asyncio.subprocess.Process, asyncio.Event]
]:
    observer = Observer()
    entered = asyncio.Event()
    release = asyncio.Event()
    stopped = asyncio.Event()

    async def on_event(event: OmpEvent) -> None:
        entered.set()
        try:
            await release.wait()
        finally:
            stopped.set()
        await observer.on_event(event)

    running = asyncio.create_task(
        client.prompt(
            "first",
            on_event=on_event,
            on_tool=observer.on_tool,
            on_ui=observer.on_ui,
        )
    )
    try:
        await entered.wait()
        yield running, owned_rpc_process(client), stopped
    finally:
        running.cancel()
        release.set()
        await asyncio.gather(running, return_exceptions=True)


async def overflow_reaps_and_restarts(
    client: OmpRpcClient,
    running: asyncio.Task[None],
    process: asyncio.subprocess.Process,
    stopped: asyncio.Event,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.clear()
    with caplog.at_level(logging.ERROR, logger="band.integrations.omprpc.client"):
        burst = asyncio.create_task(
            client.request("event-burst", count=EVENT_BACKLOG_LIMIT + 1)
        )
        try:
            with pytest.raises(OmpProcessError, match=BACKLOG_EXCEEDED):
                await running
            with pytest.raises(OmpProcessError, match=BACKLOG_EXCEEDED):
                await burst
        finally:
            burst.cancel()
            await asyncio.gather(burst, return_exceptions=True)
    assert any(
        record.levelno == logging.ERROR and BACKLOG_EXCEEDED in record.getMessage()
        for record in caplog.records
    )
    assert stopped.is_set()
    assert process.returncode is not None
    await client.start()
    await second_prompt(client)
    assert owned_rpc_process(client) is not process


class TestEventBackpressure:
    async def test_full_backlog_keeps_reentrant_control_live_and_preserves_event_order(
        self, client_factory: ClientFactory
    ) -> None:
        client = await client_factory("event-backpressure")
        process = owned_rpc_process(client)
        observer = Observer()
        entered = asyncio.Event()
        release = asyncio.Event()
        delivered = asyncio.Event()
        burst_ids = [burst_message_id(number) for number in range(EVENT_BACKLOG_LIMIT)]
        last_burst = burst_ids[-1]

        async def on_event(event: OmpEvent) -> None:
            match event.type:
                case OmpEventType.AGENT_START:
                    entered.set()
                    await release.wait()
            await observer.on_event(event)
            if event.messageId == last_burst:
                delivered.set()

        async def on_ui(request: OmpUIRequest) -> OmpUIResponse:
            await client.request("checkpoint")
            return OmpUIResponse(value=UI_APPROVE)

        running = asyncio.create_task(
            client.prompt(
                "first",
                on_event=on_event,
                on_tool=observer.on_tool,
                on_ui=on_ui,
            )
        )
        try:
            async with asyncio.timeout(8):
                await entered.wait()
                await client.request("event-burst", count=EVENT_BACKLOG_LIMIT)
                assert await client.request("event-ui") == {"value": UI_APPROVE}
                assert observer.events == []
                assert not running.done()
                assert process.returncode is None

                release.set()
                await delivered.wait()
                await client.request("event-result")
                await running

            assert observer.outline == [
                OmpEventType.AGENT_START,
                *([OmpEventType.MESSAGE_UPDATE] * EVENT_BACKLOG_LIMIT),
                OmpEventType.MESSAGE_END,
                OmpEventType.AGENT_END,
            ]
            assert observer.update_ids == burst_ids
            assert observer.assistant_texts == [COMPLETE]
            await second_prompt(client)
            assert owned_rpc_process(client) is process
            assert process.returncode is None
        finally:
            running.cancel()
            release.set()
            await asyncio.gather(running, return_exceptions=True)

    async def test_overload_before_result_fails_closed_and_restarts(
        self, client_factory: ClientFactory, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = await client_factory("event-backpressure")
        async with (
            asyncio.timeout(8),
            held_event_prompt(client) as (
                running,
                process,
                stopped,
            ),
        ):
            await overflow_reaps_and_restarts(client, running, process, stopped, caplog)

    async def test_overload_after_settlement_fails_closed_and_restarts(
        self, client_factory: ClientFactory, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = await client_factory("event-backpressure")
        async with (
            asyncio.timeout(8),
            held_event_prompt(client) as (
                running,
                process,
                stopped,
            ),
        ):
            await client.request("event-result")
            await overflow_reaps_and_restarts(client, running, process, stopped, caplog)
