from __future__ import annotations

import asyncio
import logging
import os
import signal
import time

logger = logging.getLogger(__name__)

STARTUP_MARKER = "Agent started:"
WS_MARKER = "[WebSocket] Subscribed to topic:"


class AgentRunner:
    def __init__(
        self,
        example_file: str,
        working_dir: str,
        env_overrides: dict[str, str] | None = None,
        venv: str | None = None,
    ) -> None:
        self.example_file = example_file
        self.working_dir = working_dir
        self._env = {**os.environ, **(env_overrides or {})}
        self._venv = venv
        self._process: asyncio.subprocess.Process | None = None
        self._stdout_lines: list[str] = []
        self._stderr_lines: list[str] = []
        self._reader_tasks: list[asyncio.Task] = []
        self._started = False

    def _build_cmd(self) -> list[str]:
        if self._venv:
            python = os.path.join(self._venv, "bin", "python")
            return [python, self.example_file]
        return ["uv", "run", "python", self.example_file]

    async def _cleanup_previous(self) -> None:
        """Ensure the previous process and its reader tasks are fully cleaned up."""
        # Cancel and drain any lingering reader tasks from a previous run.
        for task in self._reader_tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._reader_tasks.clear()

        # If the previous process is somehow still alive, kill it.
        if self._process is not None and self._process.returncode is None:
            try:
                self._process.kill()
                await self._process.wait()
            except ProcessLookupError:
                pass

        # Close any unclosed transport pipes from the previous process to
        # avoid file-descriptor leaks that can prevent the new subprocess
        # from starting.
        if self._process is not None:
            for pipe in (self._process.stdout, self._process.stderr):
                if pipe is not None:
                    pipe.feed_eof()
            # Allow the transport to finalise; a zero-sleep yields to the
            # event loop without adding real delay.
            await asyncio.sleep(0)

        self._process = None

    async def start(self, timeout: float = 30.0) -> bool:
        logger.info("Starting agent: %s", self.example_file)

        # Clean up any leftover state from a previous run so we start fresh.
        await self._cleanup_previous()

        self._stdout_lines.clear()
        self._stderr_lines.clear()

        cmd = self._build_cmd()
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self.working_dir,
            env=self._env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        self._reader_tasks = [
            asyncio.create_task(
                self._read_stream(self._process.stdout, self._stdout_lines, "stdout")
            ),
            asyncio.create_task(
                self._read_stream(self._process.stderr, self._stderr_lines, "stderr")
            ),
        ]

        start = time.monotonic()
        while time.monotonic() - start < timeout:
            all_output = "\n".join(self._stdout_lines + self._stderr_lines)
            if STARTUP_MARKER in all_output or WS_MARKER in all_output:
                self._started = True
                logger.info("Agent started successfully: %s", self.example_file)
                return True
            if self._process.returncode is not None:
                # Let reader tasks finish draining stderr so the error
                # message is captured fully before we report the failure.
                await self._drain_readers(drain_timeout=2.0)
                stderr = self.get_stderr()
                logger.error(
                    "Agent exited prematurely (rc=%s): %s\n%s",
                    self._process.returncode,
                    self.example_file,
                    stderr[-1000:] if stderr else "(no output)",
                )
                return False
            await asyncio.sleep(0.5)

        logger.error("Timeout waiting for agent startup: %s", self.example_file)
        return False

    async def _drain_readers(self, drain_timeout: float = 2.0) -> None:
        """Wait for reader tasks to finish reading remaining output."""
        for task in self._reader_tasks:
            if not task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=drain_timeout)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass

    async def stop(self, timeout: float = 10.0) -> bool:
        if not self._process:
            return True

        # Even if the process already exited, we still need to drain and
        # cancel reader tasks.  The early-return only applies when there
        # is nothing to clean up at all.
        already_dead = self._process.returncode is not None

        if not already_dead:
            logger.info("Stopping agent: %s", self.example_file)
            try:
                self._process.send_signal(signal.SIGINT)
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    self._process.kill()
                    await self._process.wait()
            except ProcessLookupError:
                pass

        # Always clean up reader tasks, whether process died on its own or
        # was stopped explicitly.
        for task in self._reader_tasks:
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._reader_tasks.clear()

        self._started = False
        graceful = "graceful=True" in "\n".join(self._stdout_lines + self._stderr_lines)
        logger.info("Agent stopped (graceful=%s, was_dead=%s)", graceful, already_dead)
        return graceful

    async def restart(
        self,
        stop_timeout: float = 10.0,
        start_timeout: float = 120.0,
        retries: int = 2,
        retry_delay: float = 5.0,
    ) -> bool:
        """Stop the agent and start it again.

        On restart the platform WebSocket may take tens of seconds to accept
        the new connection (server-side session cleanup).  If the agent
        process crashes during the reconnect backoff loop the Phoenix Channels
        client treats repeated rapid-disconnect failures as terminal and the
        process exits with code 1.  To handle this we retry the start.

        Args:
            stop_timeout: Seconds to wait for SIGINT before SIGKILL.
            start_timeout: Seconds to wait per start attempt for the startup marker.
            retries: Number of additional start attempts after the first failure.
            retry_delay: Seconds to wait between retry attempts.
        """
        await self.stop(timeout=stop_timeout)
        await asyncio.sleep(2.0)

        for attempt in range(1 + retries):
            if attempt > 0:
                logger.info(
                    "Restart attempt %s/%s (retrying after %ss)",
                    attempt + 1,
                    1 + retries,
                    retry_delay,
                )
                await asyncio.sleep(retry_delay)
            if await self.start(timeout=start_timeout):
                return True

        return False

    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    def get_logs(self) -> str:
        lines = []
        for line in self._stdout_lines:
            lines.append(f"[stdout] {line}")
        for line in self._stderr_lines:
            lines.append(f"[stderr] {line}")
        return "\n".join(lines)

    def get_stderr(self) -> str:
        return "\n".join(self._stderr_lines)

    async def _read_stream(
        self,
        stream: asyncio.StreamReader | None,
        buffer: list[str],
        label: str,
    ) -> None:
        if not stream:
            return
        try:
            while True:
                line = await stream.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").rstrip()
                buffer.append(decoded)
                logger.debug("[%s] %s", label, decoded)
        except asyncio.CancelledError:
            pass
