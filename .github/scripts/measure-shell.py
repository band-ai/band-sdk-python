"""Measure original shell launches independently of acceptance tests."""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.e2e.baseline.smoke.samples.approvals import marker_command, written_lines
from tests.framework_conformance.shelltrace import (
    ShellProbeProcess,
    configure_shell_trace,
)

logger = logging.getLogger(__name__)
ARTIFACTS = Path("artifacts/shell-diagnostic")


def main() -> None:
    configure_shell_trace()
    logging.basicConfig(level=logging.INFO)
    handler = logging.FileHandler(ARTIFACTS / "launch-measurements.log")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    subprocess.Popen = ShellProbeProcess
    with TemporaryDirectory() as temporary:
        target = Path(temporary) / "approval with spaces.txt"
        for sample in range(20):
            target.write_text("old", encoding="utf-8")
            command = [
                "powershell",
                "-NoProfile",
                "-Command",
                marker_command("approval-marker", target),
            ]
            started = time.monotonic()
            result = subprocess.run(
                command, check=True, cwd=temporary, timeout=10, capture_output=True
            )
            elapsed = time.monotonic() - started
            assert written_lines(target) == ["approval-marker"]
            logger.info(
                "Independent launch measurement: sample=%s elapsed_s=%s returncode=%s stdout_bytes=%s stderr_bytes=%s",
                sample,
                elapsed,
                result.returncode,
                len(result.stdout),
                len(result.stderr),
            )


if __name__ == "__main__":
    main()
