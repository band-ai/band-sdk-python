"""Native cancellation retires callbacks queued behind another mailbox job."""

from __future__ import annotations

import asyncio
import gc
from collections.abc import Awaitable, Callable

import pytest

from band.integrations.ompwire import (
    OmpHostToolCall,
    OmpToolResult,
    OmpUIRequest,
    OmpUIResponse,
)
from band.runtime.mailbox import RoomMailbox
from tests.integrations.omppeer import HOST_CANCELLED_ID, UI_CANCELLED_ID
from tests.integrations.omprpc.kit import (
    ClientFactory,
    Observer,
    checkpoint_frames,
    second_prompt,
)

ROOM = "scheduled-callbacks"


class TestScheduledCallbacks:
    @pytest.mark.parametrize(
        ("kind", "target"), [("host", HOST_CANCELLED_ID), ("ui", UI_CANCELLED_ID)]
    )
    async def test_cancellation_before_execution_releases_the_callback(
        self,
        client_factory: ClientFactory,
        recwarn: pytest.WarningsRecorder,
        kind: str,
        target: str,
    ) -> None:
        client = await client_factory(f"{kind}-cancel")
        mailbox = RoomMailbox()
        entered, queued, release = (asyncio.Event() for _ in range(3))

        async def hold() -> None:
            entered.set()
            await release.wait()

        async def schedule(run: Callable[[], Awaitable[None]]) -> None:
            queued.set()
            await mailbox.ask(ROOM, run)

        async def on_tool(call: OmpHostToolCall) -> OmpToolResult:
            pytest.fail("A cancelled queued host tool must not execute")

        async def on_ui(request: OmpUIRequest) -> OmpUIResponse:
            pytest.fail("A cancelled queued UI request must not execute")

        client.schedule_callback = schedule
        observer = Observer()
        held = asyncio.create_task(mailbox.ask(ROOM, hold))
        await entered.wait()
        running = asyncio.create_task(
            client.prompt(
                "first", on_event=observer.on_event, on_tool=on_tool, on_ui=on_ui
            )
        )
        try:
            await queued.wait()
            await client.request(f"cancel-{kind}")
            await running
            release.set()
            await held
            frames = await checkpoint_frames(client)
            assert not any(frame.get("id") == target for frame in frames)
            await second_prompt(client)
            gc.collect()
            assert not recwarn
        finally:
            release.set()
            running.cancel()
            await asyncio.gather(held, running, return_exceptions=True)
            await mailbox.close(ROOM)
