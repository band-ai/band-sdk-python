"""Temporary Windows native shutdown probe; no inference is requested."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import tempfile
from pathlib import Path
from typing import Any

from band.adapters.antigravity_acp import GEMINI_API_KEY_ENV, GEMINI_HOME_ENV
from band.integrations.acp.client_runtime import ACPRuntime
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.antigravity import (
    antigravity_api_key,
    antigravity_command,
)

logger = logging.getLogger(__name__)


async def processes() -> list[dict[str, Any]]:
    process = await asyncio.create_subprocess_exec(
        "pwsh",
        "-NoProfile",
        "-Command",
        "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CreationDate | ConvertTo-Json -Compress",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await asyncio.wait_for(process.communicate(), 30)
    if process.returncode:
        raise RuntimeError("Windows process census failed")
    return json.loads(stdout.decode("utf-8-sig"))


def owned_processes(rows: list[dict[str, Any]], root: int) -> list[dict[str, Any]]:
    owned = {root}
    while True:
        children = {
            int(row["ProcessId"])
            for row in rows
            if int(row["ParentProcessId"]) in owned
        }
        expanded = owned | children
        if expanded == owned:
            return [row for row in rows if int(row["ProcessId"]) in owned]
        owned = expanded


def await_chain(task: asyncio.Task[Any]) -> list[str]:
    chain = []
    waiting = task.get_coro()
    while waiting is not None:
        frame = getattr(waiting, "cr_frame", None)
        if frame is not None:
            chain.append(
                f"{frame.f_code.co_filename}:{frame.f_lineno}:{frame.f_code.co_name}"
            )
        waiting = getattr(waiting, "cr_await", None)
    return chain


async def main() -> None:
    if sys.platform != "win32":
        raise ValueError("This diagnostic requires Windows")
    settings = BaselineSettings()
    with tempfile.TemporaryDirectory(prefix="band-native-shutdown-") as temporary:
        home = Path(temporary) / "home"
        home.mkdir(mode=0o700)
        runtime = ACPRuntime(
            command=list(antigravity_command(settings)),
            env={
                GEMINI_API_KEY_ENV: antigravity_api_key(settings),
                GEMINI_HOME_ENV: str(home),
            },
            cwd=temporary,
            auth_method="gemini-api-key",
        )
        await runtime.start()
        await runtime.create_session(cwd=temporary, mcp_servers=[])
        process = (
            runtime._transport
        )  # Diagnostic inspection only; removed before shipping.
        rows = owned_processes(await processes(), process.pid)
        logger.info("Before shutdown: %s", json.dumps(rows, default=str))
        closing = asyncio.create_task(runtime.stop(), name="diagnostic-native-stop")
        done, _ = await asyncio.wait({closing}, timeout=10)
        if done:
            await closing
            logger.info("Native session/new shutdown completed without inference")
            return
        logger.error(
            "Pending closure: returncode=%s stdout_eof=%s stderr_eof=%s",
            process.returncode,
            process.stdout.at_eof(),
            process.stderr.at_eof(),
        )
        for task in asyncio.all_tasks():
            logger.error("Pending await chain: %s", await_chain(task))
        current = await processes()
        identities = {(row["ProcessId"], row["CreationDate"]) for row in rows}
        remaining = [
            row
            for row in current
            if (row["ProcessId"], row["CreationDate"]) in identities
        ]
        logger.error(
            "Remaining owned processes: %s", json.dumps(remaining, default=str)
        )
        for row in reversed(remaining):
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(row["ProcessId"]),
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
        await asyncio.wait_for(asyncio.shield(closing), 30)
        logger.info("Cleanup completed after terminating the recorded owned processes")
        raise RuntimeError("Native shutdown required external process cleanup")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    asyncio.run(main())
