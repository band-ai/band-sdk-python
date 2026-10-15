"""Async JSON-RPC client for Codex app-server over stdio."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from typing import Any

from band.core.exceptions import BandConnectionError
from band.integrations.codex.rpc_base import BaseJsonRpcClient, OverloadRetryPolicy
from band.integrations.codex.winspawn import (
    MISSING_EXECUTABLE,
    MODULE_NAME,
    SpawnFailure,
    spawn,
    terminate_job,
)

logger = logging.getLogger(__name__)

# Long enough for the child watcher to record an exit, short enough that a
# held stdio pipe cannot stall room cleanup.
_EXIT_OBSERVE_S = 2.0
_STDOUT_LIMIT = 16 * 1024 * 1024


def _missing_cli_message(binary: str) -> str:
    return (
        f"Codex CLI binary not found: '{binary}'. "
        "Install it (`npm install -g @openai/codex`), authenticate "
        "(`codex login`), or configure CodexAdapterConfig.codex_command."
    )


async def kill_process_tree(process: asyncio.subprocess.Process) -> None:
    """Force-stop ``process`` and everything it spawned.

    The codex launcher is a shim whose real CLI is its child. On Unix the
    child leads a session, so ``killpg`` still reaches that group after the
    leader has exited. On Windows the tree belongs to a job created before
    the child ran, so termination does not need the launcher pid to be alive.
    """
    if os.name != "nt":
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        return
    await terminate_job(process.pid, _EXIT_OBSERVE_S)


async def _observe_exit(process: asyncio.subprocess.Process) -> bool:
    """True once the direct child has exited.

    ``Process.wait`` parks until every stdio pipe disconnects when it is
    entered before a returncode is stored. After the watcher stores one, a
    new ``wait`` returns immediately.
    """
    try:
        await asyncio.wait_for(process.wait(), timeout=_EXIT_OBSERVE_S)
    except TimeoutError:
        if process.returncode is None:
            return False
        logger.warning(
            "Codex process %s exited with %s while a descendant still held its stdio pipes",
            process.pid,
            process.returncode,
        )
        await process.wait()
    return True


class CodexStdioClient(BaseJsonRpcClient):
    """Bidirectional JSON-RPC client with a single stdout read loop."""

    def __init__(
        self,
        *,
        command: Sequence[str] | None = None,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        retry_policy: OverloadRetryPolicy | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        super().__init__(retry_policy=retry_policy, sleep=sleep)
        self._transport_label = "codex output line"
        self.command = tuple(command) if command else self._default_codex_command()
        self.cwd = cwd
        if env is None:
            self.env = None
        else:
            merged_env = dict(os.environ)
            merged_env.update(env)
            self.env = merged_env

        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None

    @staticmethod
    def _default_codex_command() -> tuple[str, str, str, str]:
        """Resolve the default Codex CLI binary.

        Some installations expose ``codex``, others ``codex-cli``.
        """
        for binary in ("codex", "codex-cli"):
            resolved = shutil.which(binary)
            if resolved:
                return (resolved, "app-server", "--listen", "stdio://")
        return ("codex", "app-server", "--listen", "stdio://")

    async def connect(self) -> None:
        """Start the codex app-server process and reader tasks."""
        if self._close_task is not None:
            await self.close()
            self._close_task = None
        if self._connected:
            return

        try:
            self._proc = await self._spawn()
        except FileNotFoundError as exc:
            raise BandConnectionError(_missing_cli_message(self.command[0])) from exc
        except SpawnFailure as exc:
            raise self._spawn_connection_error(exc) from exc
        self._connected = True
        self._reader_task = asyncio.create_task(self._read_stdout_loop())
        self._stderr_task = asyncio.create_task(self._read_stderr_loop())

    async def _spawn(self) -> asyncio.subprocess.Process:
        if os.name == "nt":
            return await spawn(
                self._windows_command,
                cwd=self.cwd,
                env=self.env,
                limit=_STDOUT_LIMIT,
                observe_timeout=_EXIT_OBSERVE_S,
            )
        # The child leads its own session, so its pid is the group id.
        return await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
            env=self.env,
            start_new_session=True,
            limit=_STDOUT_LIMIT,
        )

    def _windows_command(self, job_name: str, status_handle: int) -> list[str]:
        return [
            sys.executable,
            "-m",
            MODULE_NAME,
            job_name,
            str(status_handle),
            "--",
            *self.command,
        ]

    def _spawn_connection_error(self, failure: SpawnFailure) -> BandConnectionError:
        if failure.winerror in MISSING_EXECUTABLE:
            return BandConnectionError(_missing_cli_message(self.command[0]))
        detail = (
            f"winerror {failure.winerror}"
            if failure.winerror is not None
            else "the spawn helper did not report readiness"
        )
        return BandConnectionError(
            f"Codex process could not be assigned to its job ({detail})"
        )

    async def close(self) -> None:
        """Share retryable cleanup that survives a cancelled or timed-out caller."""
        if self._proc is None:
            return
        self._connected = False
        if self._close_task is None or (
            self._close_task.done()
            and (
                self._close_task.cancelled() or self._close_task.exception() is not None
            )
        ):
            self._close_task = asyncio.create_task(self._close_process())
            self._close_task.add_done_callback(self._log_close_failure)
        await asyncio.shield(self._close_task)

    @staticmethod
    def _log_close_failure(task: asyncio.Task[None]) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error("Codex subprocess cleanup failed", exc_info=error)

    async def _close_process(self) -> None:
        readers = [
            task for task in (self._reader_task, self._stderr_task) if task is not None
        ]
        for task in readers:
            task.cancel()
        await asyncio.gather(*readers, return_exceptions=True)

        if (process := self._proc) is not None:
            # A launcher that has already exited still leaves descendants. Unix
            # signals the session group; Windows terminates the job.
            await kill_process_tree(process)
            if process.stdin is not None:
                process.stdin.close()
            if not await _observe_exit(process):
                logger.warning(
                    "Codex process %s still alive after tree kill", process.pid
                )

        self._fail_pending("Codex stdio client closed")

    async def _send_json(self, payload: dict[str, Any]) -> None:
        if not self._proc or not self._proc.stdin:
            raise RuntimeError("Codex process not connected")
        line = json.dumps(payload, separators=(",", ":")) + "\n"
        self._proc.stdin.write(line.encode("utf-8"))
        await self._proc.stdin.drain()

    # ------------------------------------------------------------------
    # Stdio-specific read loops
    # ------------------------------------------------------------------

    async def _read_stdout_loop(self) -> None:
        if not self._proc or not self._proc.stdout:
            return
        try:
            while True:
                line = await self._proc.stdout.readline()
                if not line:
                    self._fail_pending("Codex process exited unexpectedly")
                    return
                await self._dispatch_rpc_message(line.decode("utf-8", errors="replace"))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Codex stdout loop failed")
            self._fail_pending("Codex stdout loop failed")

    async def _read_stderr_loop(self) -> None:
        if not self._proc or not self._proc.stderr:
            return
        try:
            while True:
                line = await self._proc.stderr.readline()
                if not line:
                    return
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    logger.debug("codex stderr: %s", text)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Codex stderr loop failed")
