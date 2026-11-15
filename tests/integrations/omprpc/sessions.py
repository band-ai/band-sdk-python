"""Own one native RPC prompt and its process resources."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Generic, TypeVar

import psutil

from band.integrations.omprpc import OmpRpcClient
from band.integrations.omprpc.client import (
    CALLBACK_EXIT_TIMEOUT_S,
    COMPLETION_TIMEOUT_S,
)
from tests.integrations.omprpc.events import EventRecorder, record_prompt
from tests.integrations.omprpc.processes import owned_rpc_process, peer_process
from tests.integrations.omprpc.spawn import ClientFactory
from tests.integrations.ompscenarios import PeerScenario

Recorder = TypeVar("Recorder", bound=EventRecorder)


@dataclass(frozen=True)
class RpcTurn(Generic[Recorder]):
    client: OmpRpcClient
    observer: Recorder
    running: asyncio.Task[None]
    process: asyncio.subprocess.Process
    peer: psutil.Process


@asynccontextmanager
async def rpc_session(
    client_factory: ClientFactory,
    scenario: PeerScenario,
    observer: Recorder,
    *,
    callback_timeout_s: float = CALLBACK_EXIT_TIMEOUT_S,
    completion_timeout_s: float = COMPLETION_TIMEOUT_S,
) -> AsyncIterator[RpcTurn[Recorder]]:
    client = await client_factory(scenario)
    client.callback_exit_timeout_s = callback_timeout_s
    client.completion_timeout_s = completion_timeout_s
    peer = await peer_process(client)
    running = asyncio.create_task(record_prompt(client, observer))
    try:
        yield RpcTurn(client, observer, running, owned_rpc_process(client), peer)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await client.close()
