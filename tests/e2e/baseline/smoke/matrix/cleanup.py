"""Host lifetimes and controlled tool work for the resource cleanup matrix."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx

from band.agent import Agent
from band.core.simple_adapter import SimpleAdapter
from band.runtime.custom_tools import get_custom_tool_name
from band.runtime.oneshot import OneShotInvoker
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.timeouts import slow_turn_budget
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.oneshot import running_invoker_with_handle
from tests.e2e.baseline.toolkit.provisioning import (
    AdapterCell,
    ProvisionedAgent,
    running_agent_with_handle,
)
from tests.e2e.baseline.toolkit.tools import ToolSpec
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.example_agents.anthropic.kit import EXAMPLE
from tests.example_agents.anthropic.peer import shared_api

CleanupHost = Agent | OneShotInvoker
HOST_TYPES = (Agent, OneShotInvoker)
TODO = get_custom_tool_name(EXAMPLE["GetTodoInput"])
USER = get_custom_tool_name(EXAMPLE["GetUserInput"])
ACTIVE_PROMPT = f"Always call {TODO} to read the current todo; report its title."
PROMPT = (
    f"Call {TODO} and {USER} afresh for each request about sample data. "
    "Their current values are only available from the tools, never earlier history. "
    "Report the exact todo title and user name using band_send_message."
)
BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=2)


def resource_tools(client: httpx.AsyncClient, base_url: str) -> list[ToolSpec]:
    return [
        ToolSpec(model, handler)
        for model, handler in EXAMPLE["shared_tools"](client, base_url)
    ]


@asynccontextmanager
async def running_cleanup_host(
    cell: AdapterCell,
    identity: ProvisionedAgent,
    adapter: SimpleAdapter[Any],
    room_id: str,
    host_type: type[Agent | OneShotInvoker],
) -> AsyncIterator[CleanupHost]:
    with cell.resources.track_running(identity.id):
        if host_type is Agent:
            async with running_agent_with_handle(
                identity, adapter, cell.settings
            ) as host:
                yield host
        else:
            async with running_invoker_with_handle(
                identity, adapter, cell.settings, room_id=room_id
            ) as invoker:
                yield invoker


async def stop_cleanup_host(host: CleanupHost) -> None:
    match host:
        case Agent():
            await host.stop()
        case OneShotInvoker():
            await host.shutdown()


@dataclass
class ResourceUse:
    completed: bool = False
    settled: bool = False
    resource_open_at_settlement: bool = False


@dataclass
class ResourceWork:
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    settled_event: asyncio.Event = field(default_factory=asyncio.Event)
    calls: list[ResourceUse] = field(default_factory=list)

    @property
    def completed(self) -> bool:
        return all(call.completed for call in self.calls)

    @property
    def settled(self) -> bool:
        return all(call.settled for call in self.calls)

    @property
    def resource_released_early(self) -> bool:
        return any(
            call.settled and not call.resource_open_at_settlement for call in self.calls
        )

    async def wait_for_settlement_or_stop(self, stopping: asyncio.Task[None]) -> None:
        settled = asyncio.create_task(self.settled_event.wait())
        try:
            async with asyncio.timeout(BUDGET.deadline_s):
                await asyncio.wait(
                    {stopping, settled}, return_when=asyncio.FIRST_COMPLETED
                )
        finally:
            settled.cancel()
            await asyncio.gather(settled, return_exceptions=True)

    async def release_and_settle(self) -> None:
        self.release.set()
        if self.calls:
            async with asyncio.timeout(BUDGET.deadline_s):
                await self.settled_event.wait()

    def tool(self, client: httpx.AsyncClient, base_url: str) -> ToolSpec:
        model, read = EXAMPLE["shared_tools"](client, base_url)[0]

        async def held(args: Any) -> str:
            call = ResourceUse()
            self.calls.append(call)
            self.settled_event.clear()
            try:
                result = await read(args)
                self.entered.set()
                await self.release.wait()
                call.completed = True
                return result
            finally:
                call.resource_open_at_settlement = not client.is_closed
                call.settled = True
                if self.settled:
                    self.settled_event.set()

        return ToolSpec(model, held)


@dataclass(frozen=True)
class ActiveResourceTurn:
    host: CleanupHost
    client: httpx.AsyncClient
    work: ResourceWork


@asynccontextmanager
async def active_resource_turn(
    cell: AdapterCell,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    host_type: type[Agent | OneShotInvoker],
) -> AsyncIterator[ActiveResourceTurn]:
    identity = await cell.provision()
    room_id = await cell.resources.provision_room(
        title=f"e2e-active-resource-cleanup-{identity.id}",
        participants=[identity.id],
    )
    async with shared_api() as (client, base_url, _):
        work = ResourceWork()
        adapter = cell.build(tools=[work.tool(client, base_url)])

        async def close_resource() -> None:
            assert work.settled, "Resource cleanup preceded accepted tool settlement"
            assert not work.resource_released_early
            await client.aclose()

        adapter.add_cleanup(close_resource)
        async with (
            running_cleanup_host(cell, identity, adapter, room_id, host_type) as host,
            reply_capture(room_id),
        ):
            try:
                await user_ops.send_message(
                    room_id,
                    "Read current todo 1 using the tool and report its title.",
                    mention_id=identity.id,
                    mention_name=identity.name,
                )
                async with asyncio.timeout(BUDGET.deadline_s):
                    await work.entered.wait()
                yield ActiveResourceTurn(host, client, work)
            finally:
                await work.release_and_settle()


@asynccontextmanager
async def stopping_resource_host(
    turn: ActiveResourceTurn,
) -> AsyncIterator[asyncio.Task[None]]:
    entered = asyncio.Event()

    async def stop() -> None:
        entered.set()
        await stop_cleanup_host(turn.host)

    stopping = asyncio.create_task(stop())
    try:
        await entered.wait()
        yield stopping
    finally:
        turn.work.release.set()
        try:
            await stopping
        finally:
            await turn.work.release_and_settle()
