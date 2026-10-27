"""Agent-wide capacity limits forwards without admitting same-room waiters."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack

from tests.bridge.kit import (
    GatedForwarder,
    Mark,
    accept_event,
    build_runner,
    make_message_event,
)


class TestAgentRunnerConcurrencyCap:
    async def test_capacity_limits_forwards_across_rooms(self) -> None:
        forwarder = GatedForwarder(("a", "b", "c"))
        runner, _, _ = build_runner(forwarder=forwarder, max_concurrent_forwards=2)
        async with asyncio.TaskGroup() as group, AsyncExitStack() as cleanup:
            for release in forwarder.release.values():
                cleanup.callback(release.set)
            group.create_task(runner._safe_handle_event(make_message_event("a", "r1")))
            await forwarder.started["a"].wait()
            group.create_task(runner._safe_handle_event(make_message_event("b", "r2")))
            await forwarder.started["b"].wait()
            accepted = asyncio.Event()
            group.create_task(
                accept_event(runner, make_message_event("c", "r3"), accepted)
            )
            await accepted.wait()
            assert forwarder.events == [(Mark.START, "a"), (Mark.START, "b")]
            forwarder.release["a"].set()
            await forwarder.started["c"].wait()
            assert forwarder.events == [
                (Mark.START, "a"),
                (Mark.START, "b"),
                (Mark.END, "a"),
                (Mark.START, "c"),
            ]

    async def test_room_waiter_does_not_block_another_room(self) -> None:
        forwarder = GatedForwarder(("a", "b"))
        runner, _, _ = build_runner(forwarder=forwarder, max_concurrent_forwards=2)
        async with asyncio.TaskGroup() as group, AsyncExitStack() as cleanup:
            for release in forwarder.release.values():
                cleanup.callback(release.set)
            group.create_task(runner._safe_handle_event(make_message_event("a", "r1")))
            await forwarder.started["a"].wait()
            queued = asyncio.Event()
            group.create_task(
                accept_event(runner, make_message_event("queued", "r1"), queued)
            )
            await queued.wait()
            accepted = asyncio.Event()
            group.create_task(
                accept_event(runner, make_message_event("b", "r2"), accepted)
            )
            await accepted.wait()
            assert forwarder.events == [(Mark.START, "a"), (Mark.START, "b")]
