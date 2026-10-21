"""Async JSON-RPC client for Codex app-server over stdio."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any

from band.core.exceptions import BandConnectionError
from band.integrations.codex.rpc_base import BaseJsonRpcClient, OverloadRetryPolicy
from band.integrations.codex.winspawn import (
    MISSING_EXECUTABLE,
    SpawnFailure,
    WindowsProcess,
    await_returncode,
    close_stdin,
    find_executable,
    process_image,
)

logger = logging.getLogger(__name__)

# Long enough for the child watcher to record an exit, short enough that a
# held stdio pipe cannot stall room cleanup.
_EXIT_OBSERVE_S = 2.0
# How long a closed stdin gets to end the whole Codex session before it is killed.
_SESSION_EXIT_S = 1.0
_SESSION_POLL_S = 0.02
_STDOUT_LIMIT = 16 * 1024 * 1024
HELPER_PATH = Path(__file__).resolve().with_name("winmain.py")


def _absolute_command(command: Sequence[str]) -> tuple[str, ...]:
    """Resolve the executable in this process, not in the room directory.

    The helper's current directory is the room. ``CreateProcessW`` searches
    that directory when the application name is relative, so a room-local
    binary would run in place of the one this process would have started.
    """
    head, *rest = command
    if Path(head).is_absolute():
        return (head, *rest)
    if os.name == "nt" and not os.path.dirname(head):
        found = find_executable(head)
    else:
        found = shutil.which(head)
        if found is None and Path(head).exists():
            found = str(Path(head).resolve())
    if found is None:
        raise FileNotFoundError(head)
    return (str(Path(found).resolve()), *rest)


def _session_pid_reused(process: asyncio.subprocess.Process) -> bool:
    """True when ``process.pid`` now names some other live process.

    After the leader is reaped, its session id stays reserved only while a
    member of that session still exists. Once the id is free it can be given
    to a new session, and ``killpg`` would signal that session.
    """
    if process.returncode is None:
        return False
    try:
        os.kill(process.pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        logger.warning(
            "Codex process %s pid is not signalable; leaving its session",
            process.pid,
        )
        return True
    logger.warning(
        "Codex process %s pid was reused; not signaling that session",
        process.pid,
    )
    return True


def _missing_cli_message(binary: str) -> str:
    return (
        f"Codex CLI binary not found: '{binary}'. "
        "Install it (`npm install -g @openai/codex`), authenticate "
        "(`codex login`), or configure CodexAdapterConfig.codex_command."
    )


async def _wait_readers(readers: Sequence[asyncio.Task[None]]) -> None:
    """Let cancelled reads finish without awaiting a pipe that may never close."""
    if not readers:
        return
    _done, pending = await asyncio.wait(readers, timeout=_EXIT_OBSERVE_S)
    if pending:
        logger.warning("Codex stdio readers still pending after tree kill")


async def kill_process_tree(process: asyncio.subprocess.Process) -> None:
    """Force-stop a Unix process and everything in its session.

    The codex launcher is a shim whose real CLI is its child. On Unix the
    child leads a session, so ``killpg`` still reaches that group after the
    leader has exited.
    """
    if os.name != "nt":
        if not _session_pid_reused(process):
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        return


async def _await_session_exit(
    process: asyncio.subprocess.Process, timeout: float
) -> bool:
    """True once the child and every other process in its session are gone.

    The session id stays reserved while any member lives, so probing the
    group is exact; the launcher alone exiting is not enough, since its
    real CLI child would keep the room's working directory.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass
        remaining = deadline - loop.time()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(_SESSION_POLL_S, remaining))


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
        self._windows_process: WindowsProcess | None = None
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
            if self._windows_process is not None:
                raise BandConnectionError("Codex process is already starting")
            self._windows_process = owner = WindowsProcess()
            try:
                return await owner.start(
                    self._windows_command,
                    cwd=self.cwd,
                    env=self.env,
                    limit=_STDOUT_LIMIT,
                )
            except BaseException:
                await self.close()
                raise
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
            str(process_image()),
            "-I",
            "-S",
            str(HELPER_PATH),
            job_name,
            str(status_handle),
            "--",
            *_absolute_command(self.command),
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
        if self._windows_process is None and self._proc is None:
            return
        if self._windows_process is not None:
            self._windows_process.closing = True
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
        try:
            # A cancelled pipe read on Windows stays pending while the child is
            # blocked in a synchronous stdin read, and awaiting that read never
            # times out. Terminate the tree and close our write end first.
            if self._windows_process is not None:
                await self._windows_process.close(_EXIT_OBSERVE_S)
                self._windows_process = None
            elif (process := self._proc) is not None:
                # Codex and its launcher exit on their own once stdin closes; the
                # tree kill is the fallback for a session that does not.
                await close_stdin(process)
                if not await _await_session_exit(process, _SESSION_EXIT_S):
                    await kill_process_tree(process)
                if not await await_returncode(process, _EXIT_OBSERVE_S):
                    logger.warning(
                        "Codex process %s still alive after tree kill", process.pid
                    )
            self._fail_pending("Codex stdio client closed")
        finally:
            await _wait_readers(readers)

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
