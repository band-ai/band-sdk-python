"""The live probe observes cancellation without depending on async finalizers."""

from __future__ import annotations

import asyncio
import json

import anyio
import pytest

from tests.e2e.baseline.smoke.matrix.cleanup import ResourceWork
from tests.example_agents.anthropic.peer import shared_api


class TestResourceWorkSettlement:
    @pytest.mark.parametrize("cancellation", ["task", "scope"])
    async def test_cancelled_work_records_settlement_with_its_client_open(
        self, cancellation: str
    ) -> None:
        async with shared_api() as (client, base_url, _):
            work = ResourceWork()
            native = work.tool(client, base_url).as_callable()

            async def invoke() -> None:
                await native(todo_id=1)

            try:
                match cancellation:
                    case "task":
                        task = asyncio.create_task(invoke())
                        try:
                            await work.entered.wait()
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                        finally:
                            await work.release_and_settle()
                            await asyncio.gather(task, return_exceptions=True)
                    case "scope":
                        async with anyio.create_task_group() as group:
                            group.start_soon(invoke)
                            await work.entered.wait()
                            group.cancel_scope.cancel()
                    case _:
                        raise ValueError(f"Unknown cancellation: {cancellation}")
            finally:
                await work.release_and_settle()
            assert work.settled
            assert not work.resource_released_early
            assert not client.is_closed

    async def test_failed_scenario_drains_held_work_before_peer_teardown(self) -> None:
        async with shared_api() as (client, base_url, values):
            work = ResourceWork()
            native = work.tool(client, base_url).as_callable()
            task = asyncio.create_task(native(todo_id=1))
            try:
                with pytest.raises(LookupError, match="Scenario failed"):
                    await work.entered.wait()
                    raise LookupError("Scenario failed")
            finally:
                await work.release_and_settle()
                result = await task
            assert json.loads(result) == values["todo"]
            assert work.settled
            assert not work.resource_released_early
            assert not client.is_closed
