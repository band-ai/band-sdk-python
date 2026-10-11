"""Distinguish a running child from an exited child whose pipes remain open."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

import pytest

from tests.framework_conformance.shelltrace import ShellPhase, ShellProbeProcess


def timeout_returncode(caplog: pytest.LogCaptureFixture) -> int | None:
    [timeout] = [
        record
        for record in caplog.records
        if getattr(record, "shell_phase", None) == ShellPhase.TIMEOUT
    ]
    return vars(timeout)["shell_returncode"]


def test_timeout_records_exited_child_before_cleanup(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=ShellProbeProcess.__module__)
    release = tmp_path / "release"
    descendant = (
        "import time; from pathlib import Path; "
        f"release = Path({str(release)!r}); "
        "\nwhile not release.exists(): time.sleep(0.01)"
    )
    parent = (
        "import subprocess, sys; "
        f"subprocess.Popen([sys.executable, '-c', {descendant!r}])"
    )
    with ShellProbeProcess(
        [sys.executable, "-c", parent],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as process:
        try:
            assert process.wait(timeout=10) == 0
            with pytest.raises(subprocess.TimeoutExpired):
                process.communicate(timeout=0.05)
            assert timeout_returncode(caplog) == 0
        finally:
            release.touch()
            process.communicate(timeout=10)


def test_timeout_keeps_the_original_running_child_alive(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=ShellProbeProcess.__module__)
    release = tmp_path / "release"
    child = (
        "import time; from pathlib import Path; "
        f"release = Path({str(release)!r}); "
        "\nwhile not release.exists(): time.sleep(0.01)"
    )
    with ShellProbeProcess(
        [sys.executable, "-c", child],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as process:
        try:
            with pytest.raises(subprocess.TimeoutExpired):
                process.communicate(timeout=0.05)
            assert process.poll() is None
            assert timeout_returncode(caplog) is None
        finally:
            release.touch()
            assert process.communicate(timeout=10) == (b"", b"")
