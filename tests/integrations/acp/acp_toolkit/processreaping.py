"""A spawn context that leaves a real child for the runtime to reap."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from band.integrations.acp.client_runtime import ACPRuntime
from tests.adapters.waiting import until
from tests.integrations.acp.acp_toolkit.harness import make_acp_connection

STDOUT_BUFFER_LIMIT_BYTES = 1024
STDOUT_BURST_BYTES = 1024 * 1024


@asynccontextmanager
async def leaky_runtime(
    failure: BaseException | None = None,
    *,
    buffered_stdout: bool = False,
    close_failure: Exception | None = None,
) -> AsyncIterator[tuple[ACPRuntime, asyncio.subprocess.Process]]:
    command = (
        f"import sys; sys.stdout.buffer.write(b'x' * {STDOUT_BURST_BYTES}); "
        "sys.stdout.buffer.flush()"
        if buffered_stdout
        else "import time; time.sleep(60)"
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE
        if buffered_stdout
        else asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        limit=STDOUT_BUFFER_LIMIT_BYTES,
    )
    kill, wait = process.kill, process.wait
    connection = make_acp_connection()
    if failure is not None:
        connection.initialize.side_effect = failure
    if close_failure is not None:
        connection.close.side_effect = close_failure

    @asynccontextmanager
    async def spawn(*args: Any, **kwargs: Any) -> AsyncIterator[tuple[Any, Any]]:
        yield connection, process

    runtime = ACPRuntime(command=["agent"], spawn_process=spawn)
    try:
        if buffered_stdout:
            assert process.stdout is not None
            await until(
                lambda: process.stdout._paused,
                failure="child did not fill its stdout buffer",
            )
        yield runtime, process
    finally:
        process.kill = kill  # type: ignore[method-assign]
        process.wait = wait  # type: ignore[method-assign]
        if process.returncode is None:
            with suppress(ProcessLookupError):
                kill()
        if process.stdout is not None:
            await process.stdout.read()
        await wait()
        await runtime.stop()
