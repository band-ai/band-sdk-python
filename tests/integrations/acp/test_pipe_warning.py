"""The held-pipe diagnostic is scheduled and cancelled independently of I/O."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from band.integrations.acp.client_runtime import (
    STDOUT_HELD_WARNING_SECONDS,
    _warning_while_held,
)
from tests.integrations.acp.roomownership.kit import runtime_warnings


class TestPipeWarning:
    @pytest.mark.looptime
    @pytest.mark.parametrize("still_closing", [True, False], ids=["held", "closed"])
    async def test_only_an_unfinished_close_warns_once(
        self, caplog: pytest.LogCaptureFixture, still_closing: bool
    ) -> None:
        process = MagicMock(spec=asyncio.subprocess.Process, pid=123, returncode=0)
        with _warning_while_held(process):
            if still_closing:
                await asyncio.sleep(2 * STDOUT_HELD_WARNING_SECONDS)
        await asyncio.sleep(2 * STDOUT_HELD_WARNING_SECONDS)

        expected = (
            [
                (
                    f"ACP agent subprocess {process.pid} (exit code "
                    f"{process.returncode}) has not closed its stdout; "
                    "a descendant may hold it, and its room stays owned until that closes"
                )
            ]
            if still_closing
            else []
        )
        assert runtime_warnings(caplog) == expected
