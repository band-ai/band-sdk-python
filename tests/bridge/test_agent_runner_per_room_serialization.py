"""Room forwards serialize within a room and overlap across rooms."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack

from tests.bridge.kit import (
    GatedForwarder,
    accept_event,
    build_runner,
    make_message_event,
)


class TestAgentRunnerPerRoomSerialization:
    async def test_same_room_serializes(self) -> None:
        forwarder = GatedForwarder(("a", "b"))
        runner, _, _ = build_runner(forwarder=forwarder)
        async with asyncio.TaskGroup() as group, AsyncExitStack() as cleanup:
            for release in forwarder.release.values():
                cleanup.callback(release.set)
            group.create_task(runner._safe_handle_event(make_message_event("a")))
            await forwarder.started["a"].wait()
            accepted = asyncio.Event()
            group.create_task(accept_event(runner, make_message_event("b"), accepted))
            await accepted.wait()
            assert forwarder.events == [("start", "a")]
            forwarder.release["a"].set()
            await forwarder.started["b"].wait()
            assert forwarder.events == [("start", "a"), ("end", "a"), ("start", "b")]

    async def test_different_rooms_overlap(self) -> None:
        forwarder = GatedForwarder(("a", "b"))
        runner, _, _ = build_runner(forwarder=forwarder)
        async with asyncio.TaskGroup() as group, AsyncExitStack() as cleanup:
            for release in forwarder.release.values():
                cleanup.callback(release.set)
            group.create_task(runner._safe_handle_event(make_message_event("a", "r1")))
            await forwarder.started["a"].wait()
            group.create_task(runner._safe_handle_event(make_message_event("b", "r2")))
            await forwarder.started["b"].wait()
            assert forwarder.events == [("start", "a"), ("start", "b")]
