"""Distinguish a running child from an exited child whose pipes remain open."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.framework_conformance import shelltrace
from tests.framework_conformance.shelltrace import (
    SHELL_PHASE_FIELD,
    SHELL_RETURNCODE_FIELD,
    ShellPhase,
    ShellProbeProcess,
)


def wait_for_release_script(release: Path) -> str:
    return (
        "import time; from pathlib import Path; "
        f"release = Path({str(release)!r}); "
        "\nwhile not release.exists(): time.sleep(0.01)"
    )


def timeout_returncode(caplog: pytest.LogCaptureFixture) -> int | None:
    [timeout] = [
        record
        for record in caplog.records
        if getattr(record, SHELL_PHASE_FIELD, None) == ShellPhase.TIMEOUT
    ]
    return vars(timeout)[SHELL_RETURNCODE_FIELD]


def test_timeout_records_exited_child_before_cleanup(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=ShellProbeProcess.__module__)
    release = tmp_path / "release"
    descendant = wait_for_release_script(release)
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
    child = wait_for_release_script(release)
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


@pytest.mark.parametrize("failure", ["unavailable", "timeout", "inherited-writer"])
def test_wait_capture_failure_preserves_the_original_timeout_and_child(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    caplog.set_level(logging.INFO, logger=ShellProbeProcess.__module__)
    monkeypatch.setattr(shelltrace, "WAIT_CAPTURE_EXECUTABLE", Path(sys.executable))
    monkeypatch.setattr(shelltrace, "WAIT_CAPTURE_TIMEOUT_S", 0.05)
    probes: list[subprocess.Popen[bytes]] = []
    writers: list[subprocess.Popen[bytes]] = []

    def diagnostic_peer(args: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        if failure == "unavailable":
            raise PermissionError("capture unavailable")
        if failure == "inherited-writer":
            writers.append(
                subprocess.Popen[bytes](
                    [sys.executable, "-c", wait_for_release_script(release)], **kwargs
                )
            )
        probe = subprocess.Popen[bytes](
            [sys.executable, "-c", "import time; time.sleep(30)"], **kwargs
        )
        probes.append(probe)
        return probe

    monkeypatch.setattr(shelltrace, "NATIVE_POPEN", diagnostic_peer)
    release = tmp_path / "release"
    with ShellProbeProcess(
        [sys.executable, "-c", wait_for_release_script(release)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as process:
        try:
            with pytest.raises(subprocess.TimeoutExpired) as error:
                process.communicate(timeout=0.05)
            assert error.value.cmd == process.args
            assert error.value.timeout == 0.05
            assert process.poll() is None
            assert timeout_returncode(caplog) is None
            assert all(probe.poll() is not None for probe in probes)
            assert all(writer.poll() is None for writer in writers)
            assert any(record.levelno == logging.ERROR for record in caplog.records)
        finally:
            release.touch()
            process.communicate(timeout=10)
            for writer in writers:
                assert writer.wait(timeout=10) == 0
