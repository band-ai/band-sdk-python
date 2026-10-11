"""Observe subprocess phases before timeout cleanup changes the child state."""

from __future__ import annotations

import logging
import subprocess
import time
from enum import StrEnum
from tempfile import TemporaryFile
from typing import Any

from tests.paths import REPO_ROOT

logger = logging.getLogger(__name__)

SHELL_PHASE_FIELD = "shell_phase"
SHELL_RETURNCODE_FIELD = "shell_returncode"
WAIT_CAPTURE_TIMEOUT_S = 3
WAIT_CAPTURE_EXECUTABLE = REPO_ROOT / "artifacts/shell-waits/ShellWaits.exe"
NATIVE_POPEN = subprocess.Popen


def capture_shell_waits(pid: int) -> None:
    if not WAIT_CAPTURE_EXECUTABLE.is_file():
        return
    started = time.monotonic()
    # A descendant retaining stdout must not extend the diagnostic deadline.
    with TemporaryFile() as output:
        probe = NATIVE_POPEN(
            [str(WAIT_CAPTURE_EXECUTABLE), str(pid)],
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            probe.wait(timeout=WAIT_CAPTURE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            probe.kill()
            try:
                probe.wait(timeout=WAIT_CAPTURE_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                logger.error("Shell wait capture could not be reaped")
            logger.error("Shell wait capture exceeded its diagnostic deadline")
        output.seek(0)
        logger.info(
            "Shell wait capture: elapsed_s=%s returncode=%s records=%s",
            time.monotonic() - started,
            probe.returncode,
            output.read().decode("utf-8", errors="replace"),
        )


class ShellPhase(StrEnum):
    CREATED = "created"
    TIMEOUT = "timeout"


class ShellProbeProcess(subprocess.Popen[bytes]):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        started = time.monotonic()
        super().__init__(*args, **kwargs)
        logger.info(
            "Shell process created: pid=%s launch_s=%s",
            self.pid,
            time.monotonic() - started,
            extra={SHELL_PHASE_FIELD: ShellPhase.CREATED},
        )

    def communicate(
        self, input: bytes | None = None, timeout: float | None = None
    ) -> tuple[bytes, bytes]:
        try:
            return super().communicate(input=input, timeout=timeout)
        except subprocess.TimeoutExpired:
            # On Windows, communicate waits for pipe EOF before waiting for exit.
            # run() kills the child next, obscuring whether it had already exited.
            returncode = self.poll()
            readers = {
                name: thread.is_alive()
                for name in ("stdout", "stderr")
                if (thread := getattr(self, f"{name}_thread", None)) is not None
            }
            logger.info(
                "Shell communication timed out before cleanup: pid=%s "
                "returncode=%s readers_alive=%s",
                self.pid,
                returncode,
                readers,
                extra={
                    SHELL_PHASE_FIELD: ShellPhase.TIMEOUT,
                    SHELL_RETURNCODE_FIELD: returncode,
                },
            )
            if returncode is None:
                try:
                    capture_shell_waits(self.pid)
                except OSError as error:
                    logger.error(
                        "Shell wait capture unavailable: %s", type(error).__name__
                    )
            raise
