"""Callbacks cannot post after native transport cleanup has begun."""

from __future__ import annotations

import asyncio
import sys

import pytest

from band.integrations.omprpc import OmpRpcClient
from band.integrations.ompwire import (
    OmpContent,
    OmpEvent,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIRequest,
    OmpUIResponse,
)


@pytest.mark.asyncio
class TestOmpAskShutdown:
    @pytest.mark.parametrize("scenario", ["ask-on-abort", "tool-on-abort"])
    async def test_callbacks_arriving_during_shutdown_never_run(
        self, scenario: str
    ) -> None:
        client = OmpRpcClient(
            (sys.executable, "-u", "-m", "tests.integrations.ompaskpeer", scenario)
        )
        yielded = asyncio.Event()
        started: list[str] = []

        async def on_event(event: OmpEvent) -> None:
            if event.type == "agent_end":
                yielded.set()

        async def on_tool(call: OmpHostToolCall) -> OmpToolResult:
            started.append(call.id)
            return OmpToolResult(content=[OmpContent(type="text", text="late")])

        async def on_ui(request: OmpUIRequest) -> OmpUIResponse:
            started.append(request.id)
            return OmpUIResponse(cancelled=True)

        running: asyncio.Task[None] | None = None
        try:
            async with asyncio.timeout(5):
                await client.start()
                running = asyncio.create_task(
                    client.prompt(
                        "first", on_event=on_event, on_tool=on_tool, on_ui=on_ui
                    )
                )
                await yielded.wait()
                running.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await running
            assert started == []
        finally:
            if running is not None:
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)
            await client.close()
