"""Exercise active dependency changes against an independent graph model."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import CleanupIncompleteError
from tests.adapters.hold import Hold
from tests.adapters.waiting import until
from tests.core.retention.kit import SETTLE_GRACE_S, ResidenceHost


async def play(parents: list[int], departed: set[int], fail_close: bool) -> None:
    """Build an acyclic wait graph, remove callers, then request cleanup."""
    async with ResidenceHost(timeout=None, settle_grace=SETTLE_GRACE_S) as host:
        resident = host.room()
        await resident.turn()
        request, finish = Hold(), Hold()

        async def requester() -> None:
            await request.wait()
            with pytest.raises(CleanupIncompleteError):
                await resident.room.dispose()
            await finish.wait()

        tasks = [resident.room.start_owned(requester)]
        disposal = None

        async def dependent(target: asyncio.Task[None]) -> None:
            await resident.room.wait_owned(target)

        try:
            for index, parent in enumerate(parents, start=1):
                # An out-of-range parent represents unrelated accepted work.
                operation = (
                    finish.wait
                    if parent >= index
                    else lambda t=tasks[parent]: dependent(t)
                )
                tasks.append(resident.room.start_owned(operation))
            await request.arrived()
            expected_edges = {
                index: parent
                for index, parent in enumerate(parents, start=1)
                if parent < index
            }
            await until(
                lambda: len(resident.room._waiting) == len(expected_edges),
                failure="dependencies did not start",
            )
            for index in departed:
                tasks[index].cancel()
            await asyncio.gather(
                *(tasks[index] for index in departed), return_exceptions=True
            )
            protected = {0}
            for index, parent in expected_edges.items():
                if index not in departed and parent in protected:
                    protected.add(index)
            request.released.set()
            await finish.arrived()
            await until(
                lambda: (
                    bool(host.clock.timers)
                    or all(
                        task.done()
                        for index, task in enumerate(tasks)
                        if index not in protected
                    )
                ),
                failure="grace not armed",
            )
            await host.clock.advance(SETTLE_GRACE_S)
            await until(
                lambda: all(
                    task.done() or task.cancelling()
                    for index, task in enumerate(tasks)
                    if index not in protected
                ),
                failure="unrelated operations not cancelled",
            )
            assert all(not tasks[index].cancelling() for index in protected)
            assert resident.live and not resident.releases
            resident.fail_close = fail_close
            disposal = asyncio.create_task(resident.room.dispose())
        finally:
            request.released.set()
            finish.released.set()
            await asyncio.gather(*tasks, return_exceptions=True)
        assert disposal is not None
        if fail_close:
            with pytest.raises(RuntimeError, match="close refused"):
                await disposal
            assert resident.live and resident.room.snapshot.pending_cleanup
            resident.fail_close = False
        else:
            await disposal
        await resident.room.dispose()
