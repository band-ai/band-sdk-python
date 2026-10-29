"""Unsuccessful Windows ACP initialization closes its already-created tree."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from acp.exceptions import RequestError

from band.integrations.acp.client_runtime import ACPRuntime
from band.integrations.codex.winspawn import SpawnFailure
from tests.integrations.acp.acp_toolkit.peer import ExitStage
from tests.integrations.acp.windowsownership.initialization import (
    InitializationReceipt,
    await_startup_signal,
)
from tests.integrations.acp.windowsownership.peer import OwnedPeerFactory


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows job objects")
@pytest.mark.timeout(120)
class TestWindowsACPStartup:
    async def test_rejected_spawn_releases_the_helper_working_directory(
        self, unlaunchable_runtime: tuple[ACPRuntime, Path]
    ) -> None:
        runtime, workdir = unlaunchable_runtime

        with pytest.raises(SpawnFailure):
            await runtime.start()

        assert runtime.closed
        workdir.rmdir()
        assert not workdir.exists()

    async def test_failed_initialize_closes_the_pipe_holding_descendant(
        self,
        owned_pipe_peer: OwnedPeerFactory,
        initialization_receipt: InitializationReceipt,
    ) -> None:
        async with owned_pipe_peer(ExitStage.INITIALIZE_WAIT) as peer:
            starting = asyncio.create_task(peer.runtime.start())
            try:
                await await_startup_signal(starting, peer.holder_ready)
                await await_startup_signal(starting, initialization_receipt.received)
                await peer.reject_initialize()

                with pytest.raises(RequestError):
                    await starting
                await peer.holder_closed.wait()

                assert peer.runtime.closed
            finally:
                starting.cancel()
                await asyncio.gather(starting, return_exceptions=True)

    async def test_cancelled_initialize_closes_the_pipe_holding_descendant(
        self,
        owned_pipe_peer: OwnedPeerFactory,
        initialization_receipt: InitializationReceipt,
    ) -> None:
        async with owned_pipe_peer(ExitStage.INITIALIZE_WAIT) as peer:
            starting = asyncio.create_task(peer.runtime.start())
            try:
                await await_startup_signal(starting, peer.holder_ready)
                await await_startup_signal(starting, initialization_receipt.received)
                starting.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await starting
                await peer.holder_closed.wait()

                assert peer.runtime.closed
            finally:
                starting.cancel()
                await asyncio.gather(starting, return_exceptions=True)
