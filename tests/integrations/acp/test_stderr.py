"""Subprocess-boundary regressions for ACP stderr diagnostics."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from band.integrations.acp.stderr import ACPStderrDrain

LOGGER = logging.getLogger(__name__)
EXIT_CODE = 3
FATAL_LINE = "fatal agent diagnostic"
# The Windows venv redirector keeps inherited stdout open until its child exits.
PYTHON_EXECUTABLE = getattr(sys, "_base_executable", sys.executable)


async def wait_for_exit(process: asyncio.subprocess.Process) -> None:
    async with asyncio.timeout(3):
        while process.returncode is None:
            await asyncio.sleep(0.01)


@asynccontextmanager
async def inherited_stderr_peer(
    release_file: Path, *, buffered_stdout: bool
) -> AsyncIterator[asyncio.subprocess.Process]:
    helper = (
        "import pathlib, sys, time\n"
        "release = pathlib.Path(sys.argv[1])\n"
        "while not release.exists(): time.sleep(0.01)\n"
    )
    source = (
        "import os, subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)\n"
        "sys.stderr.write(sys.argv[3] + '\\n'); sys.stderr.flush()\n"
        "sys.stdout.write(sys.argv[4]); sys.stdout.flush()\n"
        "os._exit(int(sys.argv[5]))\n"
    )
    process = await asyncio.create_subprocess_exec(
        PYTHON_EXECUTABLE,
        "-c",
        source,
        helper,
        str(release_file),
        FATAL_LINE,
        "partial ACP message" if buffered_stdout else "",
        str(EXIT_CODE),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        yield process
    finally:
        release_file.touch()
        await process.communicate()


def crash_reports(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == LOGGER.name and record.levelno == logging.WARNING
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("buffered_stdout", [False, True])
async def test_crash_is_reported_while_helper_keeps_stderr_open(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, buffered_stdout: bool
) -> None:
    release_file = tmp_path / "release-helper"
    with caplog.at_level(logging.WARNING, logger=LOGGER.name):
        async with inherited_stderr_peer(
            release_file, buffered_stdout=buffered_stdout
        ) as process:
            drain = ACPStderrDrain(LOGGER)
            drain.start(process)
            await wait_for_exit(process)
            if buffered_stdout:
                assert process.stdout is not None and not process.stdout.at_eof()
                drain.expect_exit()
            async with asyncio.timeout(1):
                await drain.finish()
            assert not release_file.exists()
            assert crash_reports(caplog) == [
                f"ACP agent exited with code {EXIT_CODE}; stderr tail:\n{FATAL_LINE}"
            ]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ConnectionResetError("reset"), OSError("pipe")])
async def test_stderr_read_failure_does_not_escape_cleanup(
    caplog: pytest.LogCaptureFixture, error: OSError
) -> None:
    process = await asyncio.create_subprocess_exec(
        PYTHON_EXECUTABLE,
        "-c",
        "import sys; sys.exit(3)",
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stderr is not None
    process.stderr.set_exception(error)
    drain = ACPStderrDrain(LOGGER)
    with caplog.at_level(logging.DEBUG, logger=LOGGER.name):
        drain.start(process)
        try:
            await drain.finish()
        finally:
            await wait_for_exit(process)
            await process.wait()
    assert [
        record.levelno
        for record in caplog.records
        if record.name == LOGGER.name
        and record.getMessage() == "Error reading ACP agent stderr"
    ] == [logging.DEBUG]
    assert crash_reports(caplog) == [
        f"ACP agent exited with code {EXIT_CODE}; stderr tail:\n"
    ]
