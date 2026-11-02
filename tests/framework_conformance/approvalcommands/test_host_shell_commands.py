"""Approval probes write the same marker through each CI host shell."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.e2e.baseline.smoke.samples.approvals import (
    appending_command,
    marker_command,
    written_lines,
)
from tests.hostshells import HOST_SHELLS, host_shell_id, shell_argv

logger = logging.getLogger(__name__)

# This bounds the whole shell lifetime, including initialization on a fresh runner.
SHELL_PROCESS_TIMEOUT_SECONDS = 30


class TestHostShellCommands:
    @pytest.mark.timeout(3 * SHELL_PROCESS_TIMEOUT_SECONDS)
    @pytest.mark.parametrize("shell", HOST_SHELLS, ids=host_shell_id)
    def test_approval_commands_write_and_append_with_host_shell(
        self, tmp_path: Path, shell: str | None, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger=__name__)
        logger.info(
            "Shell probe: platform=%s python=%s executable=%s cwd=%s",
            sys.platform,
            sys.version,
            shutil.which(shell) if shell else "host default",
            tmp_path,
        )
        target = tmp_path / "approval with spaces.txt"
        marker = "approval-marker"
        target.write_text("old", encoding="utf-8")

        for command, expected in (
            (marker_command, [marker]),
            (appending_command, [marker, marker]),
        ):
            text = command(marker, target)
            args = text if shell is None else shell_argv(shell, text)
            logger.info("Starting shell command: %s", args)
            started = time.monotonic()
            try:
                result = subprocess.run(
                    args,
                    shell=shell is None,
                    check=True,
                    cwd=tmp_path,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=SHELL_PROCESS_TIMEOUT_SECONDS,
                )
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
                contents = target.read_bytes() if target.exists() else None
                logger.exception(
                    "Shell command failed after %ss: stdout=%r stderr=%r target=%r",
                    time.monotonic() - started,
                    exc.stdout,
                    exc.stderr,
                    contents,
                )
                pytest.fail(
                    f"{exc}\nstdout={exc.stdout!r}\nstderr={exc.stderr!r}"
                    f"\ntarget bytes={contents!r}",
                    pytrace=False,
                )
            logger.info(
                "Shell command completed after %ss: returncode=%s stdout=%r stderr=%r",
                time.monotonic() - started,
                result.returncode,
                result.stdout,
                result.stderr,
            )
            assert written_lines(target) == expected
