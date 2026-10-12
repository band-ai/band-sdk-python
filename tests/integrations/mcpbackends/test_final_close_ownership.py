"""A final close owns its backend until the stop actually succeeds."""

from __future__ import annotations

import asyncio
import logging

import pytest

from band.core.exceptions import CleanupIncompleteError
from band.integrations.mcp import (
    SharedBandMCPBackend,
)
from tests.integrations.mcpbackends.kit import one_tool_settings
from tests.mcpbackends import FakeBandMCPBackend, hold_backend


class TestFinalCloseOwnership:
    """A final close owns its backend until the stop actually succeeds."""

    async def test_failed_final_stop_retries_the_same_backend_and_blocks_reopen(
        self,
    ) -> None:
        backend = FakeBandMCPBackend()
        backend.fail_stops = 1
        owner = SharedBandMCPBackend(one_tool_settings)
        await hold_backend(owner, backend)

        with pytest.raises(OSError, match="stop failed"):
            await owner.close(final=True)
        with pytest.raises(CleanupIncompleteError):
            await owner.reopen()

        await owner.close(final=True)
        assert backend.stop_calls == 2
        await owner.reopen()
        replacement = await hold_backend(owner)
        assert replacement is not backend
        await owner.close(final=True)

    async def test_cancelled_final_close_leaves_its_stop_owned(self) -> None:
        stop_started, stop_release = asyncio.Event(), asyncio.Event()
        backend = FakeBandMCPBackend(
            stop_started=stop_started, stop_release=stop_release
        )
        owner = SharedBandMCPBackend(one_tool_settings)
        await hold_backend(owner, backend)

        closing = asyncio.create_task(owner.close(final=True))
        await stop_started.wait()
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        with pytest.raises(CleanupIncompleteError):
            await owner.reopen()

        retry = asyncio.create_task(owner.close(final=True))
        await asyncio.sleep(0)
        stop_release.set()
        await retry
        assert backend.stop_calls == 1
        await owner.reopen()

    async def test_final_stop_that_fails_after_its_caller_left_is_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        stop_started, stop_release = asyncio.Event(), asyncio.Event()
        backend = FakeBandMCPBackend(
            stop_started=stop_started, stop_release=stop_release
        )
        backend.fail_stops = 1
        owner = SharedBandMCPBackend(one_tool_settings)
        await hold_backend(owner, backend)

        closing = asyncio.create_task(owner.close(final=True))
        await stop_started.wait()
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing

        caplog.set_level(logging.WARNING)
        stop_release.set()
        callback_wait_s = 1.0

        def failure_logged() -> bool:
            return any(
                "final close failed" in record.message for record in caplog.records
            )

        deadline = asyncio.get_running_loop().time() + callback_wait_s
        while asyncio.get_running_loop().time() < deadline and not failure_logged():
            await asyncio.sleep(0)
        assert failure_logged()

        await owner.close(final=True)
        assert backend.stop_calls == 2
        await owner.reopen()
