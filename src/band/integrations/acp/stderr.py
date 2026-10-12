"""Bounded stderr diagnostics for an ACP subprocess."""

from __future__ import annotations

import asyncio
import logging
from collections import deque

STDERR_TAIL_LINES = 20
STDERR_DRAIN_TIMEOUT_S = 5.0
STDERR_EXIT_POLL_INTERVAL_S = 0.05
STDERR_EXIT_FLUSH_TIMEOUT_S = 0.1
STDERR_LINE_LOG_TEMPLATE = "ACP agent stderr: %s"


class ACPStderrDrain:
    """Drain one process and distinguish a crash from an intentional exit."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        self._stopping = False
        self._stdout: asyncio.StreamReader | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task[None] | None = None

    def start(self, process: asyncio.subprocess.Process) -> None:
        self._stopping = False
        self._stdout = process.stdout
        self._process = process
        self._task = asyncio.create_task(self._drain(process))

    def expect_exit(self, *, connection_failed: bool = False) -> None:
        if connection_failed:
            return
        if self._process is not None and self._process.returncode is not None:
            return
        # Cleanup after stdout EOF did not cause the connection to close.
        if self._stdout is not None and self._stdout.at_eof():
            return
        self._stopping = True

    async def _drain(self, process: asyncio.subprocess.Process) -> None:
        if process.stderr is None:
            return
        tail: deque[str] = deque(maxlen=STDERR_TAIL_LINES)
        reader = asyncio.create_task(self._read_lines(process.stderr, tail))
        try:
            # An existing Process.wait() can await inherited pipes after exit.
            while process.returncode is None:
                await asyncio.sleep(STDERR_EXIT_POLL_INTERVAL_S)
            unexpected = reader.result() if reader.done() else not self._stopping
            try:
                await asyncio.wait_for(reader, timeout=STDERR_EXIT_FLUSH_TIMEOUT_S)
            except TimeoutError:
                pass
            if unexpected and process.returncode != 0:
                self._logger.warning(
                    "ACP agent exited with code %s; stderr tail:\n%s",
                    process.returncode,
                    "\n".join(tail),
                )
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)

    async def _read_lines(self, stderr: asyncio.StreamReader, tail: deque[str]) -> bool:
        while True:
            try:
                line = await stderr.readline()
            except (ValueError, asyncio.LimitOverrunError):
                self._logger.debug("Skipped ACP stderr line exceeding the stream limit")
                continue
            except OSError:
                self._logger.debug("Error reading ACP agent stderr", exc_info=True)
                break
            if not line:
                break
            text = line.decode(errors="replace").rstrip("\r\n")
            tail.append(text)
            self._logger.debug(STDERR_LINE_LOG_TEMPLATE, text)
        # A later stop cannot reclassify an EOF already observed.
        return not self._stopping

    async def finish(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        try:
            await asyncio.wait_for(task, timeout=STDERR_DRAIN_TIMEOUT_S)
        except TimeoutError:
            self._logger.debug("Timed out draining ACP agent stderr")
        except Exception:
            self._logger.debug("Error draining ACP agent stderr", exc_info=True)
