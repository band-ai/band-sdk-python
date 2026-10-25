"""Own controlled completion deadlines and observe native state barriers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import cast

from band.integrations.omprpc import OmpRpcClient
from band.integrations.omprpc.client import CALLBACK_EXIT_TIMEOUT_S
from band.integrations.ompwire import OmpCommandName, OmpEvent, OmpEventType
from tests.integrations.omprpc.events import EventRecorder
from tests.integrations.omprpc.sessions import Recorder, RpcTurn, rpc_session
from tests.integrations.omprpc.spawn import ClientFactory
from tests.integrations.omprpc.wirelog import checkpoint_frames
from tests.integrations.ompscenarios import PeerCommand, PeerScenario
from tests.toolkit.timecontrol import ControlledClock, controlled_clock

COMPLETION_BUDGET_S = 8.0
CALLBACK_SETTLEMENT_BUDGET_S = 2 * COMPLETION_BUDGET_S
BACKGROUND_WINDOW_S = 4 * COMPLETION_BUDGET_S
NO_CALLBACK_GRACE_S = 0.0


async def snapshots_observed(client: OmpRpcClient, count: int = 1) -> None:
    await client.request(PeerCommand.WAIT_FOR_SNAPSHOTS, count=count)
    await client.request(PeerCommand.CHECKPOINT)


async def snapshot_count(client: OmpRpcClient) -> int:
    return cast(int, (await client.request(PeerCommand.CHECKPOINT))["snapshots"])


async def state_request_count(client: OmpRpcClient) -> int:
    return sum(
        frame["type"] == OmpCommandName.GET_STATE
        for frame in await checkpoint_frames(client)
    )


async def state_requests_observed(client: OmpRpcClient, count: int = 1) -> None:
    await client.request(PeerCommand.WAIT_FOR_STATE_REQUESTS, count=count)
    await client.request(PeerCommand.CHECKPOINT)


@dataclass(frozen=True)
class CompletionTurn(RpcTurn[Recorder]):
    clock: ControlledClock


CompletionContext = AbstractAsyncContextManager[CompletionTurn[Recorder]]


@asynccontextmanager
async def completion_run(
    client_factory: ClientFactory,
    scenario: PeerScenario,
    observer: Recorder,
    *,
    callback_timeout_s: float = CALLBACK_EXIT_TIMEOUT_S,
) -> AsyncIterator[CompletionTurn[Recorder]]:
    async with controlled_clock() as clock:
        resources = AsyncExitStack()
        try:
            turn = await clock.run_io(
                resources.enter_async_context(
                    rpc_session(
                        client_factory,
                        scenario,
                        observer,
                        callback_timeout_s=callback_timeout_s,
                        completion_timeout_s=COMPLETION_BUDGET_S,
                    )
                )
            )
            yield CompletionTurn(
                turn.client, turn.observer, turn.running, turn.process, turn.peer, clock
            )
        finally:
            await clock.run_io(resources.aclose())


async def background_claim_observed(observer: EventRecorder) -> OmpEvent:
    while True:
        event = await observer.wait_for(OmpEventType.AGENT_END)
        if event.awaitingAsyncWork is True:
            return event
