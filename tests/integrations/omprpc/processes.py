"""Construct and observe owned native process trees."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass

import psutil
import pytest

from band.integrations.omprpc import OmpProcessError, OmpRpcClient
from band.integrations.omprpc.custody import process_is_alive
from tests.integrations.omppeers.process import CRASH_EXIT
from tests.integrations.omprpc.cancellation import HeldCleanup
from tests.integrations.omprpc.gate import GatedProcess, ProcessGate
from tests.integrations.omprpc.spawn import OMP_PEER_COMMAND
from tests.integrations.ompscenarios import PeerCommand, PeerScenario


def owned_rpc_process(client: OmpRpcClient) -> asyncio.subprocess.Process:
    process = client._proc
    assert process is not None
    return process


def descendant_client(
    gate: ProcessGate,
    *,
    scenario: PeerScenario = PeerScenario.DESCENDANTS,
    stdio: str = "discard",
    detached: bool = False,
) -> OmpRpcClient:
    return OmpRpcClient(
        (
            *OMP_PEER_COMMAND,
            "--scenario",
            scenario,
            "--gate-port",
            str(gate.port),
            "--child-stdio",
            stdio,
            *(("--detached-children",) if detached else ()),
        )
    )


def is_descendant(child: psutil.Process, ancestor: psutil.Process) -> bool:
    """True when ``ancestor`` is a living parent of ``child``.

    Windows Python launchers insert an extra process, so the gate child's
    ``parent_pid`` is not always the owned helper.
    """
    try:
        return ancestor in child.parents()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


async def descendant_liveness(descendants: Sequence[GatedProcess]) -> list[bool]:
    return [
        await asyncio.to_thread(process_is_alive, child.process)
        for child in descendants
    ]


def session_ids(pids: Sequence[int]) -> list[int]:
    getsid = getattr(os, "getsid", None)
    assert getsid is not None
    return [getsid(pid) for pid in pids]


def job_member_pids(client: OmpRpcClient) -> set[int]:
    owner = client._windows_process
    assert owner is not None and owner.job is not None
    return set(owner.job._process_ids())


async def wait_for_descendants(gate: ProcessGate, count: int = 2) -> list[GatedProcess]:
    return [await gate.connection() for _ in range(count)]


async def descendants_started_with(
    gate: ProcessGate, root: asyncio.subprocess.Process, count: int = 2
) -> list[GatedProcess]:
    ancestor = await asyncio.to_thread(psutil.Process, root.pid)
    children: list[GatedProcess] = []
    while len(children) < count:
        child = await gate.connection()
        if is_descendant(child.process, ancestor):
            children.append(child)
    return children


async def crash_rpc_root(client: OmpRpcClient) -> None:
    root = owned_rpc_process(client)
    crashing = asyncio.create_task(client.request(PeerCommand.EXIT_PROCESS))
    async with asyncio.timeout(5):
        await root.wait()
    assert root.returncode == CRASH_EXIT
    with pytest.raises(OmpProcessError):
        await crashing


@dataclass
class DescendantTree:
    client: OmpRpcClient
    cleanup: HeldCleanup
    starting: asyncio.Task[None]
    root: asyncio.subprocess.Process
    descendants: list[GatedProcess]


@asynccontextmanager
async def eof_client(
    gate: ProcessGate, *, stdio: str
) -> AsyncIterator[tuple[OmpRpcClient, GatedProcess, asyncio.subprocess.Process]]:
    client = descendant_client(
        gate, scenario=PeerScenario.EOF_DESCENDANTS, stdio=stdio, detached=True
    )
    try:
        await client.start()
        helper = await gate.connection()
        yield client, helper, owned_rpc_process(client)
    finally:
        await client.close()


@asynccontextmanager
async def descendant_tree(
    gate: ProcessGate, *, scenario: PeerScenario, stdio: str
) -> AsyncIterator[DescendantTree]:
    client = descendant_client(gate, scenario=scenario, stdio=stdio)
    cleanup = HeldCleanup(client)
    starting = asyncio.create_task(client.start())
    try:
        descendants = await wait_for_descendants(gate)
        yield DescendantTree(
            client=client,
            cleanup=cleanup,
            starting=starting,
            root=owned_rpc_process(client),
            descendants=descendants,
        )
    finally:
        cleanup.release.set()
        starting.cancel()
        await asyncio.gather(starting, return_exceptions=True)
        await client.close()


async def peer_process(client: OmpRpcClient) -> psutil.Process:
    checkpoint = await client.request(PeerCommand.CHECKPOINT)
    pid = checkpoint["pid"]
    assert isinstance(pid, int)
    process = await asyncio.to_thread(psutil.Process, pid)
    return process
