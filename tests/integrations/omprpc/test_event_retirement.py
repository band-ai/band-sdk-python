"""A retired event worker exits when its cancellation-resistant callback returns."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest

from band.integrations.omprpc import OmpProcessError
from band.integrations.ompwire import OmpEvent, OmpEventType
from tests.integrations.omprpc.kit import ClientFactory, Observer, second_prompt


class TestEventCallbackRetirement:
    @pytest.mark.parametrize("queued_event", [False, True], ids=["empty", "backlog"])
    async def test_released_callback_proves_retirement_without_delivering_stale_events(
        self,
        client_factory: ClientFactory,
        monkeypatch: pytest.MonkeyPatch,
        queued_event: bool,
    ) -> None:
        client = await client_factory("event-backpressure")
        process = client._proc
        assert process is not None
        observer = Observer()
        entered = asyncio.Event()
        cancelled = asyncio.Event()
        release = asyncio.Event()
        worker_done = asyncio.Event()
        delivered: list[str] = []
        cancelled_tasks: set[asyncio.Task[None]] = set()

        async def on_event(event: OmpEvent) -> None:
            delivered.append(event.type)
            entered.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancelled.set()

        running = asyncio.create_task(
            client.prompt(
                "first",
                on_event=on_event,
                on_tool=observer.on_tool,
                on_ui=observer.on_ui,
            )
        )
        worker: asyncio.Task[None] | None = None
        try:
            async with asyncio.timeout(8):
                await entered.wait()
                turn = client._turn
                assert turn is not None and turn.event_worker is not None
                worker = turn.event_worker
                worker.add_done_callback(lambda task: worker_done.set())
                cancel_tasks = client._cancel_tasks

                async def return_observed_survivor(
                    tasks: Sequence[asyncio.Task[None]],
                ) -> None:
                    if worker in tasks and not release.is_set():
                        # Exercise pending-worker retirement, not the join deadline.
                        cancelled_tasks.update(tasks)
                        for task in tasks:
                            task.cancel()
                        await cancelled.wait()
                    else:
                        await cancel_tasks(tasks)

                monkeypatch.setattr(client, "_cancel_tasks", return_observed_survivor)
                if queued_event:
                    await client.request("event-burst", count=1)

                running.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await running
                assert process.returncode is not None
                assert not worker.done()
                with pytest.raises(OmpProcessError, match="retirement is unproven"):
                    await client.start()

                release.set()
                await worker_done.wait()
                assert delivered == [OmpEventType.AGENT_START]
                await client.close()
                await client.start()
                await second_prompt(client)
                assert client._proc is not process
        finally:
            release.set()
            for task in (running, worker, *cancelled_tasks):
                if task is not None:
                    task.cancel()
            await asyncio.gather(
                running,
                *(task for task in (worker, *cancelled_tasks) if task is not None),
                return_exceptions=True,
            )
            await client.close()
