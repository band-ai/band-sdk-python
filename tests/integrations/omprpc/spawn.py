"""Launch native RPC peers and hold their process creation."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable
from typing import Any

from band.integrations.omprpc import OmpRpcClient
from tests.integrations import omppeer
from tests.integrations.ompscenarios import PeerScenario

OMP_PEER_COMMAND = (sys.executable, "-u", "-m", omppeer.__name__)


class FailingOwnedClient(OmpRpcClient):
    fail_spawn = True

    async def spawn_owned_process(
        self, *command: str, **kwargs: Any
    ) -> asyncio.subprocess.Process:
        process = await super().spawn_owned_process(*command, **kwargs)
        if self.fail_spawn:
            raise ValueError("Owned spawn wrapper failed")
        return process


class SpawnBarrier:
    """Create the first subprocess, then hold returning it until released."""

    def __init__(
        self, spawn: Callable[..., Awaitable[asyncio.subprocess.Process]]
    ) -> None:
        self.spawn = spawn
        self.created = asyncio.Event()
        self.release = asyncio.Event()
        self.process: asyncio.subprocess.Process | None = None

    async def create(
        self, *command: str, **kwargs: object
    ) -> asyncio.subprocess.Process:
        process = await self.spawn(*command, **kwargs)
        if self.process is None:
            self.process = process
            self.created.set()
            await self.release.wait()
        return process


ClientFactory = Callable[[PeerScenario], Awaitable[OmpRpcClient]]
