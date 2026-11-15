"""Release native Windows peers and their control servers after every outcome."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from band.integrations.acp.client_runtime import ACPRuntime
from tests.integrations.acp.acp_toolkit.peer import ExitStage
from tests.integrations.acp.acp_toolkit.pipepeer import CONTROL_HOST
from tests.integrations.acp.windowsownership.initialization import InitializationReceipt
from tests.integrations.acp.windowsownership.peer import OwnedPeerFactory, OwnedPipePeer
from tests.ports import HeldPort


@pytest.fixture
def initialization_receipt(
    caplog: pytest.LogCaptureFixture,
) -> Iterator[InitializationReceipt]:
    observer = InitializationReceipt()
    logger = logging.getLogger(ACPRuntime.__module__)
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        logger.addHandler(observer)
        try:
            yield observer
        finally:
            logger.removeHandler(observer)


@pytest.fixture
async def unlaunchable_runtime(
    tmp_path: Path,
) -> AsyncIterator[tuple[ACPRuntime, Path]]:
    workdir = tmp_path / "native-launch"
    workdir.mkdir()
    runtime = ACPRuntime(command=[str(workdir / "absent-server.exe")], cwd=str(workdir))
    try:
        yield runtime, workdir
    finally:
        await runtime.stop()


@pytest.fixture
def owned_pipe_peer() -> OwnedPeerFactory:
    @asynccontextmanager
    async def build(stage: ExitStage = ExitStage.EOF) -> AsyncIterator[OwnedPipePeer]:
        with HeldPort(CONTROL_HOST) as port:
            peer = OwnedPipePeer(stage, port.port)
            port.release()
            server = await asyncio.start_server(peer.connect, CONTROL_HOST, port.port)
        try:
            yield peer
        finally:
            try:
                await peer.finish()
            finally:
                server.close()
                await server.wait_closed()

    return build
