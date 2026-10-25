"""Client lifecycle and process gate fixtures for OMP RPC tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import pytest
import pytest_asyncio

from tests.integrations.ompscenarios import PeerScenario

pytest.importorskip("psutil", reason="psutil not installed (band-sdk[omp])")

from band.integrations.omprpc import OmpRpcClient
from tests.integrations.omprpc.callbacks import (
    BlockedEventObserver,
    HeldEventFactory,
    HeldEventTurn,
    StubbornEventObserver,
    StubbornToolObserver,
)
from tests.integrations.omprpc.completion import (
    CALLBACK_SETTLEMENT_BUDGET_S,
    NO_CALLBACK_GRACE_S,
    CompletionContext,
    CompletionTurn,
    completion_run,
)
from tests.integrations.omprpc.events import EventRecorder, record_prompt
from tests.integrations.omprpc.gate import ProcessGate, process_gate_session
from tests.integrations.omprpc.processes import owned_rpc_process, peer_process
from tests.integrations.omprpc.sessions import RpcTurn, rpc_session
from tests.integrations.omprpc.spawn import OMP_PEER_COMMAND, ClientFactory


@pytest_asyncio.fixture(loop_scope="function")
async def client_factory() -> AsyncIterator[ClientFactory]:
    async with AsyncExitStack() as resources:

        async def build(scenario: PeerScenario) -> OmpRpcClient:
            client = OmpRpcClient(
                (
                    *OMP_PEER_COMMAND,
                    "--scenario",
                    scenario,
                )
            )
            resources.push_async_callback(client.close)
            await client.start()
            return client

        yield build


@pytest_asyncio.fixture(loop_scope="function")
async def process_gate() -> AsyncIterator[ProcessGate]:
    async with process_gate_session() as gate:
        yield gate


@pytest.fixture
def held_event_turn(client_factory: ClientFactory) -> HeldEventFactory:
    """Own a real prompt and its cancellation-resistant event callback."""

    @asynccontextmanager
    async def hold(scenario: PeerScenario) -> AsyncIterator[HeldEventTurn]:
        client = await client_factory(scenario)
        client.callback_exit_timeout_s = NO_CALLBACK_GRACE_S
        peer = await peer_process(client)
        observer = StubbornEventObserver()
        running = asyncio.create_task(record_prompt(client, observer))
        try:
            yield HeldEventTurn(
                client, observer, running, owned_rpc_process(client), peer
            )
        finally:
            await observer.release()
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
            await client.close()

    return hold


@pytest.fixture
def completion_turn(
    client_factory: ClientFactory, request: pytest.FixtureRequest
) -> CompletionContext[EventRecorder]:
    return completion_run(client_factory, request.param, EventRecorder())


@pytest.fixture
def blocked_completion_turn(
    client_factory: ClientFactory, request: pytest.FixtureRequest
) -> CompletionContext[BlockedEventObserver]:
    @asynccontextmanager
    async def hold() -> AsyncIterator[CompletionTurn[BlockedEventObserver]]:
        observer = BlockedEventObserver()
        async with completion_run(client_factory, request.param, observer) as turn:
            try:
                yield turn
            finally:
                observer.resume.set()

    return hold()


@pytest.fixture
def tool_completion_turn(
    client_factory: ClientFactory,
) -> CompletionContext[StubbornToolObserver]:
    @asynccontextmanager
    async def hold() -> AsyncIterator[CompletionTurn[StubbornToolObserver]]:
        observer = StubbornToolObserver()
        async with completion_run(
            client_factory,
            PeerScenario.SETTLED_WITH_UNFINISHED_TOOL,
            observer,
            callback_timeout_s=CALLBACK_SETTLEMENT_BUDGET_S,
        ) as turn:
            try:
                yield turn
            finally:
                await observer.release()

    return hold()


@pytest_asyncio.fixture(loop_scope="function")
async def tool_turn(
    client_factory: ClientFactory,
) -> AsyncIterator[RpcTurn[StubbornToolObserver]]:
    observer = StubbornToolObserver()
    async with rpc_session(
        client_factory,
        PeerScenario.SETTLED_WITH_UNFINISHED_TOOL,
        observer,
        callback_timeout_s=CALLBACK_SETTLEMENT_BUDGET_S,
    ) as turn:
        try:
            yield turn
        finally:
            await observer.release()


@pytest_asyncio.fixture(loop_scope="function")
async def rpc_turn(
    client_factory: ClientFactory,
    request: pytest.FixtureRequest,
) -> AsyncIterator[RpcTurn[EventRecorder]]:
    async with rpc_session(client_factory, request.param, EventRecorder()) as turn:
        yield turn
