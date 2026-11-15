"""Retire older widget instances when a new one takes ownership."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from band.integrations.desktop_app.tools import MonitorCaller
from tests.integrations.desktop_app.conftest import (
    message,
)
from tests.integrations.desktop_app.monitoring.kit import WaitingBroker
from tests.toolkit.clock import binary_clock_resolution

# All I/O is in memory, so long-poll deadlines can advance virtually.
pytestmark = pytest.mark.looptime(resolution=binary_clock_resolution())


class TestViewInstances:
    """A remounted widget takes the room over; the old one retires itself."""

    async def test_a_newer_mount_supersedes_every_older_one(self, room: Any) -> None:
        live = room([message("m-1", "2026-01-01T00:00:01Z")])
        await live.join()

        first = await live.monitor(caller=MonitorCaller.APP, instance="widget-a")
        newer = await live.monitor(caller=MonitorCaller.APP, instance="widget-b")
        older = await live.monitor(caller=MonitorCaller.APP, instance="widget-a")

        assert first["superseded"] is False
        assert newer["superseded"] is False
        assert older["superseded"] is True, (
            "an already-known instance must never steal ownership back"
        )

    async def test_the_models_calls_never_retire_anything(self, room: Any) -> None:
        live = room([message("m-1", "2026-01-01T00:00:01Z")])
        await live.join()
        await live.monitor(caller=MonitorCaller.APP, instance="widget-a")

        model_tick = await live.monitor()
        still_owner = await live.monitor(caller=MonitorCaller.APP, instance="widget-a")

        assert model_tick["superseded"] is False
        assert still_owner["superseded"] is False

    async def test_a_takeover_reaches_the_wait_already_in_flight(
        self, room: Any
    ) -> None:
        """The old widget is usually parked on a wait when the new one mounts,
        so telling only its *next* call leaves both on screen for a tick."""
        broker = WaitingBroker()
        live = room([message("m-1", "2026-01-01T00:00:01Z")], events=broker)
        await live.join()
        first = await live.monitor(caller=MonitorCaller.APP, instance="widget-a")

        async with asyncio.TaskGroup() as tasks:
            parked = tasks.create_task(
                live.monitor(
                    caller=MonitorCaller.APP,
                    instance="widget-a",
                    since=first["next_since"],
                )
            )
            await broker.waiting.wait()
            assert not parked.done()
            takeover = await live.monitor(caller=MonitorCaller.APP, instance="widget-b")
            await live.publish()

        assert (await parked)["superseded"] is True, (
            "the takeover happened while this call was parked on the wait"
        )
        assert takeover["superseded"] is False
