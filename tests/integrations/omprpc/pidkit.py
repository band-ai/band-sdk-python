"""Model kernel PID reuse at the external process signal boundary."""

from __future__ import annotations

import asyncio

import psutil

from band.integrations.omprpc import OmpRpcClient
from band.integrations.omprpc.scope import OmpProcessScope


class RetainedProcess(psutil.Process):
    """Retain real death detection while routing signals to a replacement."""

    def __init__(self, pid: int) -> None:
        super().__init__(pid)
        self.replacement: psutil.Process | None = None

    def kill(self) -> None:
        # Kernel PID allocation cannot be controlled; model only its signal target.
        assert self.replacement is not None
        self.replacement.kill()


def signal_scope(processes: list[psutil.Process]) -> None:
    scope = OmpProcessScope()
    scope.processes.update(processes)
    scope.signal()


async def rpc_peer(client: OmpRpcClient) -> psutil.Process:
    checkpoint = await client.request("checkpoint")
    pid = checkpoint["pid"]
    assert isinstance(pid, int)
    return await asyncio.to_thread(psutil.Process, pid)
