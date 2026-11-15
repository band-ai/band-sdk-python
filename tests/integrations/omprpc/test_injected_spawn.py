"""Injected creation preserves forced retirement and a usable replacement."""

from __future__ import annotations

import asyncio

import pytest

from band.integrations.omprpc import OmpProcessError, OmpRpcClient
from band.integrations.ompwire import OmpEventType
from tests.integrations.omppeers.base import COMPLETE
from tests.integrations.omprpc.events import (
    EventRecorder,
    follow_up_texts,
    record_prompt,
)
from tests.integrations.omprpc.processes import owned_rpc_process
from tests.integrations.omprpc.spawn import OMP_PEER_COMMAND, FailingOwnedClient
from tests.integrations.ompscenarios import PeerScenario


class TestInjectedSpawnOwnership:
    async def test_stubborn_injected_process_is_reaped_before_restart(self) -> None:
        client = OmpRpcClient(
            (*OMP_PEER_COMMAND, "--scenario", PeerScenario.STUBBORN),
            create_process=asyncio.create_subprocess_exec,
        )
        process: asyncio.subprocess.Process | None = None
        running: asyncio.Task[None] | None = None
        try:
            await client.start()
            process = owned_rpc_process(client)
            observer = EventRecorder()
            running = asyncio.create_task(record_prompt(client, observer))
            await observer.wait_for(OmpEventType.AGENT_END)
            async with asyncio.timeout(10):
                await client.close()
                with pytest.raises(OmpProcessError):
                    await running
            assert process.returncode is not None
            await client.start()
            assert await follow_up_texts(client) == [COMPLETE]
            assert owned_rpc_process(client) is not process
        finally:
            # Release an uncontained direct child even when the regression fails.
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            if running is not None:
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)
            await client.close()

    async def test_failed_owned_wrapper_reaps_before_restart(self) -> None:
        client = FailingOwnedClient(
            (*OMP_PEER_COMMAND, "--scenario", PeerScenario.STANDARD_TURN),
            create_process=asyncio.create_subprocess_exec,
        )
        process: asyncio.subprocess.Process | None = None
        try:
            with pytest.raises(ValueError):
                await client.start()
            process = owned_rpc_process(client)
            assert process.returncode is not None
            client.fail_spawn = False
            await client.start()
            assert await follow_up_texts(client) == [COMPLETE]
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            await client.close()
