"""A failed close of an ACP agent process has exactly one reporter."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import pytest

from band.integrations.acp.client_runtime import ACPRuntime
from tests.adapters.roompeer import CLEANUP_FAILURE
from tests.integrations.acp.acp_toolkit.lifecycle import FailingExits
from tests.integrations.acp.acp_toolkit.peer import stdio_peer_config
from tests.integrations.acp.stdiopeer import ExitStage
from tests.paths import REPO_ROOT

RefusingRuntime = Callable[[ExitStage], AbstractAsyncContextManager[ACPRuntime]]


@pytest.fixture
def refusing_runtime() -> RefusingRuntime:
    """A runtime over a real peer that exits at ``stage`` but whose first exit
    is refused; a retried stop kills it."""

    @asynccontextmanager
    async def build(stage: ExitStage) -> AsyncIterator[ACPRuntime]:
        config = stdio_peer_config(stage, [])
        async with FailingExits() as spawns:
            runtime = ACPRuntime(
                command=list(config.command),
                cwd=str(REPO_ROOT),
                env=config.env,
                spawn_process=spawns,
            )
            try:
                yield runtime
            finally:
                await runtime.stop()

    return build


def tracebacks(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Every logged message that carries a traceback."""
    return [record.getMessage() for record in caplog.records if record.exc_info]


class TestRuntimeCloseReporting:
    @pytest.mark.asyncio
    async def test_a_failed_stop_is_left_to_its_caller(
        self, refusing_runtime: RefusingRuntime, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        async with refusing_runtime(ExitStage.EOF) as runtime:
            await runtime.start()
            with pytest.raises(RuntimeError, match=CLEANUP_FAILURE):
                await runtime.stop()

        assert tracebacks(caplog) == []

    @pytest.mark.asyncio
    async def test_a_failed_close_after_a_failed_start_is_logged_once(
        self, refusing_runtime: RefusingRuntime, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The start raises its own error, so the close failure is logged."""
        caplog.set_level(logging.WARNING)
        async with refusing_runtime(ExitStage.INITIALIZE) as runtime:
            with pytest.raises(ConnectionError, match="Connection closed"):
                await runtime.start()
            process = runtime._transport
            assert isinstance(process, asyncio.subprocess.Process)

        assert tracebacks(caplog) == [
            (
                f"Closing ACP agent subprocess {process.pid} after a failed start "
                "failed; stop retries it"
            )
        ]
