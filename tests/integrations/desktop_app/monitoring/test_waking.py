"""Deliver room events to blocked monitoring calls."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from band.integrations.desktop_app.settings import MAX_ROOM_EVENT_TIMEOUT_S
from band.integrations.desktop_app.tools import RoomTool
from tests.integrations.desktop_app.conftest import (
    ROOM_ID,
    ids,
    mentioned_message,
    message,
)
from tests.integrations.desktop_app.monitoring.kit import WaitingBroker
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.tasks import background
from tests.toolkit.timecontrol import controlled_clock

# All I/O is in memory, so long-poll deadlines can advance virtually.
pytestmark = pytest.mark.looptime(resolution=binary_clock_resolution())


class TestWaking:
    async def test_the_broker_releases_everyone_waiting_on_a_room(self) -> None:
        broker = WaitingBroker()
        async with asyncio.TaskGroup() as tasks:
            waiter = tasks.create_task(
                broker.wait(
                    ROOM_ID, after_version=broker.version(ROOM_ID), timeout_seconds=1
                )
            )

            await broker.waiting.wait()
            broker.waiting.clear()
            another = tasks.create_task(
                broker.wait(
                    ROOM_ID, after_version=broker.version(ROOM_ID), timeout_seconds=1
                )
            )
            await broker.waiting.wait()
            assert not waiter.done()
            assert not another.done()
            await broker.publish(ROOM_ID)

        assert await waiter is True
        assert await another is True

    @pytest.mark.parametrize("timeout_seconds", [1, MAX_ROOM_EVENT_TIMEOUT_S])
    async def test_a_quiet_wait_expires_at_its_deadline(
        self, timeout_seconds: int
    ) -> None:
        broker = WaitingBroker()
        async with (
            controlled_clock() as clock,
            background(
                broker.wait(
                    ROOM_ID,
                    after_version=broker.version(ROOM_ID),
                    timeout_seconds=timeout_seconds,
                )
            ) as waiting,
        ):
            await broker.waiting.wait()
            await clock.elapse(timeout_seconds / 2)
            assert not waiting.done()

            await clock.elapse(timeout_seconds / 2)
            assert waiting.done()
            assert await waiting is False

    async def test_an_event_mid_wait_hydrates_the_new_context(self, room: Any) -> None:
        broker = WaitingBroker()
        live = room(
            [], [mentioned_message("req-1", "2026-01-01T00:00:03Z")], events=broker
        )

        async with asyncio.TaskGroup() as tasks:
            waiting = tasks.create_task(live.wait(since="2026-01-01T00:00:02Z"))
            await broker.waiting.wait()
            assert not waiting.done()
            await live.publish()
            result = await waiting

        assert result.event_received is True
        assert ids(result.pending_requests) == ["req-1"]

    async def test_a_live_mention_tells_the_model_to_answer_and_resume(
        self, room: Any
    ) -> None:
        live = room(
            [message("m-1", "2026-01-01T00:00:01Z")],
            [mentioned_message("req-1", "2026-01-01T00:00:03Z")],
        )
        await live.join()

        tick = await live.monitor()

        assert tick["pending_requests"][0]["id"] == "req-1"
        assert "Answer them in the room now" in tick["summary_text"]
        assert RoomTool.MONITOR in tick["summary_text"], (
            "every tick must point at the next one or the loop stops"
        )
