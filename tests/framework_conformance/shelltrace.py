"""Observe subprocess phases before timeout cleanup changes the child state."""

from __future__ import annotations

import logging
import subprocess
import time
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


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
            extra={"shell_phase": ShellPhase.CREATED},
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
                    "shell_phase": ShellPhase.TIMEOUT,
                    "shell_returncode": returncode,
                },
            )
            raise
