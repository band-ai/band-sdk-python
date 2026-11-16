"""Kit: AgentCore container instances launched as real processes on one host."""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import sys
from dataclasses import dataclass

from tests.loaders import load_script_module
from tests.oneshot_server.kit import CREDENTIALS
from tests.paths import EXAMPLES_ROOT

SCRIPT = EXAMPLES_ROOT / "agentcore" / "custom_tools_llm_server.py"
HEALTHY = load_script_module(SCRIPT, "coresident_container").HEALTHY
HOST = "127.0.0.1"
INSTANCES = 3
# uvicorn logs this only after lifespan startup, once its socket is listening;
# for port 0 it names the port the OS assigned.
SERVING = re.compile(rb"Uvicorn running on (http://\S+)")
# Hung-shutdown guard before escalating to kill; not part of any claim.
STOP_GRACE_S = 10


@dataclass(frozen=True)
class Instance:
    process: asyncio.subprocess.Process

    async def serving(self) -> str:
        """The base URL once uvicorn reports listening; fail with the output on early exit."""
        assert self.process.stdout is not None
        output = bytearray()
        while not (
            match := SERVING.search(line := await self.process.stdout.readline())
        ):
            if not line:
                raise AssertionError(
                    "instance exited before serving:\n"
                    + output.decode(errors="replace")
                )
            output += line
        return match.group(1).decode()


async def launch(platform_url: str) -> Instance:
    """Start one container on a port the OS assigns when it binds."""
    env = {
        **os.environ,
        **CREDENTIALS,
        "BAND_REST_URL": platform_url,
        "HOST": HOST,
        "PORT": "0",
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(SCRIPT),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    return Instance(process)


async def stop(instance: Instance) -> None:
    """Terminate the instance and await its confirmed exit."""
    process = instance.process
    with contextlib.suppress(ProcessLookupError):
        process.terminate()
    try:
        async with asyncio.timeout(STOP_GRACE_S):
            await process.wait()
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()
