"""Interrupted prompts and cancelled closes await callback cancellation and process reaping."""

from __future__ import annotations

import asyncio

import pytest

from band.integrations.ompwire import OmpHostToolCall, OmpToolResult
from tests.integrations.omprpc.cancellation import HeldCleanup
from tests.integrations.omprpc.kit import (
    ClientFactory,
    Observer,
    prompt_task,
    queue_prompt,
    second_prompt,
    sent_prompt_messages,
)


class TestInterruptionAndReaping:
    async def test_cancelling_a_queued_prompt_preserves_the_active_and_next_prompt(
        self, client_factory: ClientFactory
    ) -> None:
        client = await client_factory("yield")
        process = client._proc
        assert process is not None
        observer = Observer()
        active = prompt_task(client, observer)
        queued: asyncio.Task[None] | None = None
        try:
            await observer.wait_for("agent_end")
            queued = await queue_prompt(client)
            queued.cancel()
            with pytest.raises(asyncio.CancelledError):
                await queued

            assert not active.done()
            assert process.returncode is None
            assert await sent_prompt_messages(client) == ["first"]
            await client.request("release")
            async with asyncio.timeout(5):
                await active
            await second_prompt(client)
            assert client._proc is process
            assert process.returncode is None
            assert await sent_prompt_messages(client) == ["first", "second"]
        finally:
            tasks = [active, *([queued] if queued is not None else [])]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

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
                async with asyncio.timeout(6):
                    with pytest.raises(TimeoutError):
                        async with asyncio.timeout(None) as deadline:
                            deadline.reschedule(asyncio.get_running_loop().time())
                            await running
                    assert deadline.expired()
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
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client = await client_factory("stubborn")
        process = client._proc
        assert process is not None
        cleanup = HeldCleanup(client, monkeypatch)
        closing = asyncio.create_task(client.close())
        try:
            await cleanup.wait_for_join(closing)
            for _ in range(3):
                await cleanup.cancel_and_wait_for_rejoin(closing)
                assert client._proc is process
                assert process.returncode is None
                assert not cleanup.finished.is_set()

            cleanup.release.set()
            async with asyncio.timeout(6):
                with pytest.raises(asyncio.CancelledError):
                    await closing
                await process.wait()
            assert cleanup.finished.is_set()
            assert process.returncode is not None
            await client.close()
        finally:
            cleanup.release.set()
            closing.cancel()
            await asyncio.gather(closing, return_exceptions=True)
            await client.close()

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
