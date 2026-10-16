"""Successful spawn-context exit cannot abandon a live ACP child."""

from __future__ import annotations

import asyncio

import pytest

from tests.integrations.acp.acp_toolkit.processreaping import leaky_runtime


class TestProcessReaping:
    """The runtime confirms exit itself when the spawn context leaves a child."""

    @pytest.mark.asyncio
    async def test_stop_reaps_a_process_its_context_left_running(self) -> None:
        async with leaky_runtime() as (runtime, process):
            await runtime.start()
            await runtime.stop()

            assert process.returncode is not None
            assert runtime.closed

    @pytest.mark.asyncio
    @pytest.mark.parametrize("confirmation", ["kill", "wait"])
    async def test_failed_confirmation_keeps_the_child_and_refuses_its_connection(
        self, confirmation: str
    ) -> None:
        async with leaky_runtime() as (runtime, process):
            await runtime.start()
            kill, wait = process.kill, process.wait

            def refused_kill() -> None:
                raise PermissionError("kill refused")

            async def refused_wait() -> int:
                raise OSError("wait failed")

            if confirmation == "kill":
                process.kill = refused_kill  # type: ignore[method-assign]
            else:
                process.wait = refused_wait  # type: ignore[method-assign]
            with pytest.raises(OSError):
                await runtime.stop()

            assert runtime._transport is process
            assert not runtime.closed
            with pytest.raises(RuntimeError, match="connection is not open"):
                await runtime.ensure_connection()
            with pytest.raises(RuntimeError, match="not yet closed"):
                await runtime.start()

            process.kill = kill  # type: ignore[method-assign]
            process.wait = wait  # type: ignore[method-assign]
            await runtime.stop()

            assert process.returncode is not None
            assert runtime.closed

    @pytest.mark.asyncio
    @pytest.mark.parametrize("cancelled", [False, True], ids=["failed", "cancelled"])
    async def test_failed_start_reaps_its_child_without_changing_the_start_outcome(
        self, cancelled: bool
    ) -> None:
        error = asyncio.CancelledError() if cancelled else LookupError("init failed")
        async with leaky_runtime(error) as (runtime, process):
            with pytest.raises(type(error)):
                await runtime.start()

            assert process.returncode is not None
            assert runtime.closed

    @pytest.mark.asyncio
    @pytest.mark.parametrize("cancelled", [False, True], ids=["failed", "cancelled"])
    async def test_failed_start_keeps_its_outcome_when_the_child_refuses_to_close(
        self, cancelled: bool
    ) -> None:
        error = asyncio.CancelledError() if cancelled else LookupError("init failed")
        async with leaky_runtime(error) as (runtime, process):
            kill = process.kill

            def refused_kill() -> None:
                raise PermissionError("kill refused")

            process.kill = refused_kill  # type: ignore[method-assign]
            with pytest.raises(type(error)):
                await runtime.start()

            assert process.returncode is None
            assert runtime._transport is process
            assert not runtime.closed
            with pytest.raises(RuntimeError, match="connection is not open"):
                await runtime.ensure_connection()
            process.kill = kill  # type: ignore[method-assign]
            await runtime.stop()

            assert process.returncode is not None
            assert runtime.closed
