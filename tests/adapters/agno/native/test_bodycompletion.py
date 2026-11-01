"""Cancelling Agno's awaiter cannot complete a synchronous native body."""

from __future__ import annotations

import asyncio

import pytest
from agno.tools.function import Function

from band.core.exceptions import CleanupIncompleteError
from band.runtime.tools.agent import AgentTools
from tests.adapters.agno.native.kit import NATIVE_RESULT, SyncBody, native_call


class TestActualNativeBodyCompletion:
    @pytest.mark.looptime
    async def test_cancelled_awaiter_retains_body_until_actual_finally(
        self, scoped_tools: AgentTools, body: SyncBody
    ) -> None:
        async with native_call(
            scoped_tools, Function.from_callable(body.lookup)
        ) as call:
            await body.entered.wait()
            call.cancel()
            with pytest.raises(CleanupIncompleteError, match="still running"):
                await call
            assert scoped_tools._scope is not None
            (owner,) = scoped_tools._scope.workers
            assert not owner.task.done()
            with pytest.raises(asyncio.CancelledError):
                await scoped_tools._for_resource(owner).send_notice(
                    "stale", mentions=["@Alice"]
                )
            body.release.set()
            await body.finished.wait()
            await owner.task

    async def test_success_joins_body_and_preserves_borrowed_schema(
        self, scoped_tools: AgentTools, body: SyncBody
    ) -> None:
        borrowed = Function.from_callable(body.lookup)
        schema = borrowed.to_dict()
        entrypoint = borrowed.entrypoint
        async with native_call(scoped_tools, borrowed) as call:
            await body.entered.wait()
            body.release.set()
            result = await call
            assert result[-1].result == NATIVE_RESULT
        assert borrowed.entrypoint is entrypoint
        assert borrowed.to_dict() == schema
