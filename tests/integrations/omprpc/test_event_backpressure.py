"""Callback overload fails closed without blocking native control correlation."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import JsonValue

from band.integrations import omprpc
from band.integrations.omprpc import OmpProcessError
from band.integrations.ompwire import (
    OmpEvent,
    OmpEventType,
    OmpUIRequest,
    OmpUIResponse,
)
from tests.integrations.omprpc.kit import ClientFactory, Observer, second_prompt


class TestEventBackpressure:
    async def test_full_backlog_keeps_reentrant_control_live_and_preserves_event_order(
        self, client_factory: ClientFactory
    ) -> None:
        client = await client_factory("event-backpressure")
        process = client._proc
        assert process is not None
        observer = Observer()
        entered = asyncio.Event()
        release = asyncio.Event()
        delivered = asyncio.Event()
        event_count = omprpc._EVENT_BACKLOG_LIMIT

        async def on_event(event: OmpEvent) -> None:
            if event.type == OmpEventType.AGENT_START:
                entered.set()
                await release.wait()
            await observer.on_event(event)
            if event.messageId == f"burst-{event_count - 1}":
                delivered.set()

        async def on_ui(request: OmpUIRequest) -> OmpUIResponse:
            await client.request("checkpoint")
            return OmpUIResponse(value="Approve")

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
                await client.request("event-burst", count=event_count)
                assert await client.request("event-ui") == {"value": "Approve"}
                assert observer.events == []
                assert not running.done()
                assert process.returncode is None

                release.set()
                await delivered.wait()
                await client.request("event-result")
                await running

            assert [(event.type, event.messageId) for event in observer.events] == [
                (OmpEventType.AGENT_START, None),
                *[
                    (OmpEventType.MESSAGE_UPDATE, f"burst-{number}")
                    for number in range(event_count)
                ],
                (OmpEventType.MESSAGE_END, "complete"),
                (OmpEventType.AGENT_END, None),
            ]
            assert observer.assistant_texts == ["complete"]
            await second_prompt(client)
            assert client._proc is process
            assert process.returncode is None
        finally:
            running.cancel()
            release.set()
            await asyncio.gather(running, return_exceptions=True)

    @pytest.mark.parametrize(
        "result_seen",
        [False, True],
        ids=["before-result", "after-result-and-settlement"],
    )
    async def test_overload_fails_the_prompt_cancels_delivery_reaps_and_can_restart(
        self, client_factory: ClientFactory, result_seen: bool
    ) -> None:
        client = await client_factory("event-backpressure")
        process = client._proc
        assert process is not None
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
        overflowing: asyncio.Task[dict[str, JsonValue]] | None = None
        try:
            async with asyncio.timeout(8):
                await entered.wait()
                if result_seen:
                    # The peer acknowledges only after the settled state snapshot.
                    await client.request("event-result")
                await client.request(
                    "event-burst",
                    count=omprpc._EVENT_BACKLOG_LIMIT - (2 if result_seen else 0),
                )
                assert not running.done()
                assert not stopped.is_set()

                overflowing = asyncio.create_task(
                    client.request("event-burst", count=1)
                )
                with pytest.raises(OmpProcessError, match="event callback backlog"):
                    await running

                assert stopped.is_set()
                assert observer.events == []
                assert process.returncode is not None
                await asyncio.gather(overflowing, return_exceptions=True)
                await client.start()
                await second_prompt(client)
                assert client._proc is not process
        finally:
            tasks = [running, *([overflowing] if overflowing is not None else [])]
            for task in tasks:
                task.cancel()
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
