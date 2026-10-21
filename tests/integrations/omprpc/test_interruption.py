"""Interrupted prompts and cancelled closes await callback cancellation and process reaping."""

from __future__ import annotations

import asyncio

import pytest

from band.integrations.ompwire import OmpHostToolCall, OmpToolResult
from tests.integrations.omprpc.kit import (
    ClientFactory,
    Observer,
    prompt_task,
    second_prompt,
)


class TestInterruptionAndReaping:
    @pytest.mark.parametrize("scenario", ["yield", "no-result", "stubborn"])
    @pytest.mark.parametrize("interruption", ["timeout", "cancel"])
    async def test_interrupted_prompt_aborts_reaps_and_can_restart(
        self, client_factory: ClientFactory, scenario: str, interruption: str
    ) -> None:
        client = await client_factory(scenario)
        process = client._proc
        assert process is not None
        observer = Observer()
        running = prompt_task(client, observer)
        try:
            await observer.wait_for("agent_end")
            if interruption == "cancel":
                running.cancel()
                async with asyncio.timeout(6):
                    with pytest.raises(asyncio.CancelledError):
                        await running
            else:
                with pytest.raises(TimeoutError):
                    async with asyncio.timeout(0.05):
                        await running
            assert process.returncode is not None
            async with asyncio.timeout(5):
                await client.start()
                await second_prompt(client)
            assert client._proc is not process
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    async def test_repeatedly_cancelled_close_still_waits_for_reap(
        self,
        client_factory: ClientFactory,
    ) -> None:
        client = await client_factory("stubborn")
        process = client._proc
        assert process is not None
        closing = asyncio.create_task(client.close())
        loop = asyncio.get_running_loop()
        cancellations = [
            loop.call_later(delay, closing.cancel) for delay in (0.01, 0.05, 0.1)
        ]
        try:
            async with asyncio.timeout(6):
                with pytest.raises(asyncio.CancelledError):
                    await closing
            assert process.returncode is not None
            await client.close()
        finally:
            for cancellation in cancellations:
                cancellation.cancel()
            await asyncio.gather(closing, return_exceptions=True)

    async def test_cancelled_prompt_cancels_its_host_callback_before_reap(
        self,
        client_factory: ClientFactory,
    ) -> None:
        client = await client_factory("host-cancel")
        process = client._proc
        assert process is not None
        observer = Observer()
        started = asyncio.Event()
        cancelled = asyncio.Event()
        forever = asyncio.Event()

        async def on_tool(call: OmpHostToolCall) -> OmpToolResult:
            started.set()
            try:
                await forever.wait()
            finally:
                cancelled.set()
            return OmpToolResult(content=[])

        running = asyncio.create_task(
            client.prompt(
                "first",
                on_event=observer.on_event,
                on_tool=on_tool,
                on_ui=observer.on_ui,
            )
        )
        try:
            async with asyncio.timeout(6):
                await started.wait()
                running.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await running
            assert cancelled.is_set()
            assert process.returncode is not None
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
