"""Own subprocess creation, readiness, streams and confirmed cleanup asynchronously."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from collections import deque
from collections.abc import Awaitable, Callable
from copy import deepcopy
from pathlib import Path
from typing import TypeVar

from band.contracts.errors import (
    ContractCapacityError,
    ContractError,
    ContractProcessError,
)
from band.contracts.models import ProcessOptions, ProcessResult
from band.contracts.wire import JsonObject, decode_json

logger = logging.getLogger(__name__)
Result = TypeVar("Result")


async def join_cleanup(task: asyncio.Task[Result]) -> Result:
    """Delay caller cancellation until the owned cleanup has actually finished."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


class ManagedProcess:
    """Keep a single child owned across startup races and repeated close calls."""

    def __init__(
        self,
        command: tuple[str, ...],
        *,
        options: ProcessOptions,
        ready_event: str | None = None,
        cwd: Path | None = None,
        environment: dict[str, str] | None = None,
        prepare: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        if not command:
            raise ValueError("A managed command is required")
        self._command = command
        self._options = options
        self._ready_event = ready_event
        self._cwd = cwd
        self._environment = dict(environment or {})
        self._prepare = prepare
        self._process: asyncio.subprocess.Process | None = None
        self._start_task: asyncio.Task[JsonObject] | None = None
        self._prepare_task: asyncio.Future[None] | None = None
        self._close_task: asyncio.Task[ProcessResult | None] | None = None
        self._wait_task: asyncio.Task[int] | None = None
        self._readers: list[asyncio.Task[None]] = []
        self._spawned = asyncio.Event()
        self._ready: asyncio.Future[JsonObject] | None = None
        self._closing = False
        self._stdout = bytearray()
        self._stderr = bytearray()
        self._events: deque[JsonObject] = deque(maxlen=100)
        self._stream_error: ContractError | None = None
        self.result: ProcessResult | None = None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    @property
    def active(self) -> bool:
        return self._start_task is not None and (
            self._close_task is None or not self._close_task.done()
        )

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def events(self) -> list[JsonObject]:
        return deepcopy(list(self._events))

    async def start(self) -> JsonObject:
        if self._closing:
            raise ContractProcessError("A closed managed process cannot be restarted")
        if self._start_task is None:
            self._ready = asyncio.get_running_loop().create_future()
            self._start_task = asyncio.create_task(self._launch())
        try:
            return deepcopy(await asyncio.shield(self._start_task))
        except asyncio.CancelledError:
            await self.aclose()
            raise
        except (ContractError, OSError, ValueError, RuntimeError):
            await self.aclose()
            raise

    async def _launch(self) -> JsonObject:
        try:
            if self._closing:
                raise ContractProcessError("Managed startup was closed before spawn")
            if self._prepare is not None:
                self._prepare_task = asyncio.ensure_future(self._prepare())
                await self._prepare_task
            if self._closing:
                raise ContractProcessError("Managed startup was closed before spawn")
            self._process = await asyncio.create_subprocess_exec(
                *self._command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._cwd,
                env=self._environment,
                start_new_session=sys.platform == "linux",
                limit=self._options.max_output_bytes + 1,
            )
            self._wait_task = asyncio.create_task(self._process.wait())
            self._readers = [
                asyncio.create_task(self._read_stdout()),
                asyncio.create_task(self._read_stderr()),
            ]
        finally:
            self._spawned.set()
        if self._ready_event is None:
            return {}
        if self._ready is None:
            raise ContractProcessError("Managed readiness was not initialized")
        try:
            return await asyncio.wait_for(
                asyncio.shield(self._ready), self._options.startup_timeout
            )
        except TimeoutError:
            raise ContractProcessError(
                "Managed process did not emit its ready event"
            ) from None

    async def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            while line := await process.stdout.readline():
                if self._ready_event is None:
                    if len(self._stdout) + len(line) > self._options.max_output_bytes:
                        raise ContractCapacityError("process output")
                    self._stdout.extend(line)
                else:
                    if len(line) > self._options.max_output_bytes:
                        raise ContractCapacityError("process event")
                    event = decode_json(line)
                    self._events.append(event)
                    if (
                        event.get("event") == self._ready_event
                        and self._ready is not None
                        and not self._ready.done()
                    ):
                        self._ready.set_result(event)
        except (ContractError, ValueError, OSError):
            self._stream_error = ContractProcessError(
                "Managed process produced invalid or excessive output"
            )
            self._begin_close()
        finally:
            if (
                self._ready_event is not None
                and self._ready is not None
                and not self._ready.done()
            ):
                self._ready.set_exception(
                    self._stream_error
                    or ContractProcessError(
                        "Managed process exited before its ready event"
                    )
                )

    async def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        while chunk := await process.stderr.read(8192):
            self._stderr.extend(chunk)
            if len(self._stderr) > 65_536:
                del self._stderr[:-65_536]

    def _begin_close(self) -> asyncio.Task[ProcessResult | None]:
        self._closing = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._cleanup())
        return self._close_task

    async def aclose(self) -> ProcessResult | None:
        return await join_cleanup(self._begin_close())

    async def _cleanup(self) -> ProcessResult | None:
        if self._start_task is None:
            return None
        if self._prepare_task is not None and not self._prepare_task.done():
            self._prepare_task.cancel()
        await self._spawned.wait()
        process = self._process
        forced = False
        if process is not None and self._wait_task is not None:
            if process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
            joined = asyncio.gather(
                self._wait_task, *self._readers, return_exceptions=True
            )
            try:
                await asyncio.wait_for(
                    asyncio.shield(joined), self._options.shutdown_timeout
                )
            except TimeoutError:
                forced = True
                logger.warning(
                    "Managed contract process exceeded its shutdown deadline"
                )
                try:
                    if sys.platform == "linux":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                except ProcessLookupError:
                    pass
            await joined
            returncode = self._wait_task.result()
            self.result = ProcessResult(
                returncode=returncode,
                forced=forced,
                stdout=bytes(self._stdout),
                stderr=bytes(self._stderr),
            )
        await asyncio.gather(self._start_task, return_exceptions=True)
        if (
            self._ready is not None
            and self._ready.done()
            and not self._ready.cancelled()
        ):
            self._ready.exception()
        return self.result

    async def wait(self) -> ProcessResult:
        await self.start()
        if self._wait_task is None:
            raise ContractProcessError("Managed process was not created")
        try:
            await asyncio.shield(self._wait_task)
        finally:
            await self.aclose()
        if self.result is None:
            raise ContractProcessError("Managed process has no confirmed exit")
        return self.result

    async def run(self, *, allowed: tuple[int, ...] = (0,)) -> ProcessResult:
        """Run one finite command and return only after streams and child are joined."""
        try:
            result = await asyncio.wait_for(
                self.wait(), self._options.operation_timeout
            )
        except TimeoutError:
            raise ContractProcessError(
                "Contract operator command exceeded its deadline"
            ) from None
        if self._stream_error is not None:
            raise self._stream_error
        if result.forced or result.returncode not in allowed:
            raise ContractProcessError(
                f"Contract operator command exited with status {result.returncode}"
            )
        return result
