"""Injected creation preserves forced retirement and a usable replacement."""

from __future__ import annotations

import asyncio
import sys

import pytest

from band.integrations.omprpc import OmpProcessError, OmpRpcClient
from band.integrations.ompwire import OmpEventType
from tests.integrations.omprpc.kit import (
    OMP_PEER_SCRIPT,
    FailingOwnedClient,
    Observer,
    owned_rpc_process,
    prompt_task,
    second_prompt,
)


class TestInjectedSpawnOwnership:
    async def test_stubborn_injected_process_is_reaped_before_restart(self) -> None:
        client = OmpRpcClient(
            (sys.executable, "-u", OMP_PEER_SCRIPT, "--scenario", "stubborn"),
            create_process=asyncio.create_subprocess_exec,
        )
        process: asyncio.subprocess.Process | None = None
        running: asyncio.Task[None] | None = None
        try:
            await client.start()
            process = owned_rpc_process(client)
            observer = Observer()
            running = prompt_task(client, observer)
            await observer.wait_for(OmpEventType.AGENT_END)
            async with asyncio.timeout(10):
                await client.close()
                with pytest.raises(OmpProcessError):
                    await running
            assert process.returncode is not None
            await client.start()
            await second_prompt(client)
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
            (sys.executable, "-u", OMP_PEER_SCRIPT, "--scenario", "basic"),
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
            await second_prompt(client)
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            await client.close()
