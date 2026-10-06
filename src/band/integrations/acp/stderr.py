"""Bounded stderr diagnostics for an ACP subprocess."""

from __future__ import annotations

import asyncio
import logging
from collections import deque

STDERR_TAIL_LINES = 20
STDERR_DRAIN_TIMEOUT_S = 5.0


class ACPStderrDrain:
    """Drain one process and distinguish a crash from an intentional exit."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        self._stopping = False
        self._task: asyncio.Task[None] | None = None

    def start(self, process: asyncio.subprocess.Process) -> None:
        self._stopping = False
        self._task = asyncio.create_task(self._drain(process))

    def expect_exit(self) -> None:
        self._stopping = True

    async def _drain(self, process: asyncio.subprocess.Process) -> None:
        if process.stderr is None:
            return
        tail: deque[str] = deque(maxlen=STDERR_TAIL_LINES)
        while True:
            try:
                line = await process.stderr.readline()
            except (ValueError, asyncio.LimitOverrunError):
                self._logger.debug("Skipped ACP stderr line exceeding the stream limit")
                continue
            if not line:
                break
            text = line.decode(errors="replace").rstrip("\r\n")
            tail.append(text)
            self._logger.debug("ACP agent stderr: %s", text)
        # Capture the exit intent before prompt failure can trigger stop().
        unexpected = not self._stopping
        returncode = await process.wait()
        if unexpected and returncode != 0:
            self._logger.warning(
                "ACP agent exited with code %s; stderr tail:\n%s",
                returncode,
                "\n".join(tail),
            )

    async def finish(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        try:
            await asyncio.wait_for(task, timeout=STDERR_DRAIN_TIMEOUT_S)
        except TimeoutError:
            self._logger.debug("Timed out draining ACP agent stderr")
