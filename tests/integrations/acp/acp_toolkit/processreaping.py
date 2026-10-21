"""A spawn context that leaves a real child for the runtime to reap."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from band.integrations.acp.client_runtime import ACPRuntime
from tests.integrations.acp.acp_toolkit.harness import make_acp_connection


@asynccontextmanager
async def leaky_runtime(
    failure: BaseException | None = None,
) -> AsyncIterator[tuple[ACPRuntime, asyncio.subprocess.Process]]:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(60)",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    kill, wait = process.kill, process.wait
    connection = make_acp_connection()
    if failure is not None:
        connection.initialize.side_effect = failure

    @asynccontextmanager
    async def spawn(*args: Any, **kwargs: Any) -> AsyncIterator[tuple[Any, Any]]:
        yield connection, process

    runtime = ACPRuntime(command=["agent"], spawn_process=spawn)
    try:
        yield runtime, process
    finally:
        process.kill = kill  # type: ignore[method-assign]
        process.wait = wait  # type: ignore[method-assign]
        if process.returncode is None:
            with suppress(ProcessLookupError):
                kill()
        await wait()
        await runtime.stop()
