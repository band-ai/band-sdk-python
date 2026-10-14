"""An auxiliary claim reports acceptance and is owned by the handler."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from band.runtime.execution import ExecutionContext
from tests.conftest import make_message_event
from tests.runtime.executioninterrupt.kit import (
    auxiliary_context,
)
from tests.runtime.helpers import (
    LifecyclePlatform,
    rest_client_over,
)


class TestAuxiliaryClaimAcceptance:
    """Auxiliary claims answer with acceptance and only from the active handler."""

    @pytest.mark.parametrize("accepted", [True, False])
    async def test_auxiliary_claim_returns_acceptance_without_ending_turn(
        self,
        accepted: bool,
    ) -> None:
        peer = LifecyclePlatform()
        results: list[bool] = []

        def answer(request: httpx.Request) -> httpx.Response:
            if not accepted and request.url.path.endswith("/aux/processing"):
                return httpx.Response(503)
            return peer.answer(request)

        async with rest_client_over(answer) as rest:

            async def handler(ctx: ExecutionContext, event: Any) -> None:
                results.append(await ctx.claim_message("aux"))

            ctx = auxiliary_context(rest, handler)
            await ctx._process_event(make_message_event(msg_id="trigger"))
            assert results == [accepted]
            assert peer.marked("processed") == ["trigger"]
            assert peer.requested("failed") == []

    @pytest.mark.parametrize("owner", ["outside", "subtask"])
    async def test_auxiliary_claim_rejects_non_handler_tasks_before_rest(
        self,
        owner: str,
    ) -> None:
        peer = LifecyclePlatform()
        async with rest_client_over(peer.answer) as rest:

            async def handler(ctx: ExecutionContext, event: Any) -> None:
                with pytest.raises(RuntimeError, match="active handler"):
                    await asyncio.create_task(ctx.claim_message("aux"))

            ctx = auxiliary_context(rest, handler)
            if owner == "outside":
                with pytest.raises(RuntimeError, match="active handler"):
                    await ctx.claim_message("aux")
                assert peer.requested_marks == []
            else:
                await ctx._process_event(make_message_event(msg_id="trigger"))
                assert peer.requested_marks == [
                    ("trigger", "processing"),
                    ("trigger", "processed"),
                ]
