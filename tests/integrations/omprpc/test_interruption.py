"""Interrupted prompts and cancelled closes await callback cancellation and process reaping."""

from __future__ import annotations

import asyncio
import sys

import pytest
from pydantic import JsonValue

from band.integrations.omprpc import OmpProcessError, OmpRpcClient
from band.integrations.ompwire import (
    OMP_FRAME_BYTES,
    OmpEventType,
    OmpHostToolCall,
    OmpToolResult,
)
from tests.integrations.omppeer import STDIN_BACKPRESSURE
from tests.integrations.omprpc.backpressure import (
    PRESSURE_WRITE_COUNT,
    BackpressureSpawn,
)
from tests.integrations.omprpc.cancellation import HeldCleanup
from tests.integrations.omprpc.kit import (
    OMP_PEER_SCRIPT,
    ClientFactory,
    Observer,
    owned_rpc_process,
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
        process = owned_rpc_process(client)
        observer = Observer()
        active = prompt_task(client, observer)
        queued: asyncio.Task[None] | None = None
        try:
            await observer.wait_for(OmpEventType.AGENT_END)
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
            assert owned_rpc_process(client) is process
            assert process.returncode is None
            assert await sent_prompt_messages(client) == ["first", "second"]
        finally:
            tasks = [active, *([queued] if queued is not None else [])]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    @pytest.mark.parametrize("scenario", ["yield", "no-result", "stubborn"])
    async def test_cancelled_prompt_aborts_reaps_and_can_restart(
        self, client_factory: ClientFactory, scenario: str
    ) -> None:
        client = await client_factory(scenario)
        process = owned_rpc_process(client)
        observer = Observer()
        running = prompt_task(client, observer)
        try:
            await observer.wait_for(OmpEventType.AGENT_END)
            running.cancel()
            async with asyncio.timeout(6):
                with pytest.raises(asyncio.CancelledError):
                    await running
            assert process.returncode is not None
            async with asyncio.timeout(5):
                await client.start()
                await second_prompt(client)
            assert owned_rpc_process(client) is not process
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    @pytest.mark.parametrize("scenario", ["yield", "no-result", "stubborn"])
    async def test_timed_out_prompt_aborts_reaps_and_can_restart(
        self, client_factory: ClientFactory, scenario: str
    ) -> None:
        client = await client_factory(scenario)
        process = owned_rpc_process(client)
        observer = Observer()
        running = prompt_task(client, observer)
        try:
            await observer.wait_for(OmpEventType.AGENT_END)
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
            assert owned_rpc_process(client) is not process
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    async def test_repeatedly_cancelled_close_still_waits_for_reap(
        self,
        client_factory: ClientFactory,
    ) -> None:
        client = await client_factory("stubborn")
        process = owned_rpc_process(client)
        cleanup = HeldCleanup(client)
        closing = asyncio.create_task(client.close())
        try:
            await cleanup.wait_for_join(closing)
            for _ in range(3):
                await cleanup.cancel_and_wait_for_rejoin(closing)
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
        process = owned_rpc_process(client)
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

    async def test_close_finishes_while_a_stdin_drain_is_held(self) -> None:
        spawn = BackpressureSpawn()
        client = OmpRpcClient(
            (sys.executable, "-u", OMP_PEER_SCRIPT, "--scenario", STDIN_BACKPRESSURE),
            create_process=spawn.create,
        )
        writes: list[asyncio.Task[dict[str, JsonValue]]] = []
        try:
            await client.start()
            process = owned_rpc_process(client)
            await client.request("hold")
            payload = "x" * (OMP_FRAME_BYTES // 2)
            writes = [
                asyncio.create_task(client.request("pressure", data=payload))
                for _ in range(PRESSURE_WRITE_COUNT)
            ]
            async with asyncio.timeout(5):
                await spawn.paused.wait()
                await client.close()
            assert process.returncode is not None
            outcomes = await asyncio.gather(*writes, return_exceptions=True)
            assert all(isinstance(outcome, OmpProcessError) for outcome in outcomes)
        finally:
            for writing in writes:
                writing.cancel()
            await asyncio.gather(*writes, return_exceptions=True)
            await client.close()
