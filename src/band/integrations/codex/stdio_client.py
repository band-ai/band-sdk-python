"""Async JSON-RPC client for Codex app-server over stdio."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from band.core.exceptions import BandConnectionError
from band.integrations.codex.rpc_base import BaseJsonRpcClient, OverloadRetryPolicy

logger = logging.getLogger(__name__)

_PROCESS_EXIT_TIMEOUT_S = 1.0


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
            self._proc = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
                env=self.env,
                limit=16 * 1024 * 1024,  # 16 MB — Codex sends large JSON-RPC lines
            )
        except FileNotFoundError as exc:
            binary = self.command[0] if self.command else "codex"
            raise BandConnectionError(
                f"Codex CLI binary not found: '{binary}'. "
                "Install it (`npm install -g @openai/codex`), authenticate "
                "(`codex login`), or configure CodexAdapterConfig.codex_command."
            ) from exc
        self._connected = True
        self._reader_task = asyncio.create_task(self._read_stdout_loop())
        self._stderr_task = asyncio.create_task(self._read_stderr_loop())

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
            if process.stdin is not None:
                process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), timeout=_PROCESS_EXIT_TIMEOUT_S)
            except TimeoutError:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()

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
