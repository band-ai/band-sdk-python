"""Claims refused or deferred while the room is stopped."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from band.runtime.cycle import TurnScope
from band.runtime.delivery import TurnOutcome
from band.runtime.execution import ExecutionContext
from tests.conftest import BlockingHandler
from tests.runtime.executioninterrupt.kit import auxiliary_context, backlog_message
from tests.runtime.helpers import LifecyclePlatform, rest_client_over
from tests.runtime.scheduler.driver import attempt_delivery


class TestStoppedClaims:
    """A stopped room defers claims without invoking the handler or spending retries."""

    @pytest.mark.parametrize("path", ["live", "backlog"])
    async def test_stopped_claim_defers_without_invoking_or_poisoning_retry(
        self, path: str
    ) -> None:
        peer = LifecyclePlatform(stopped=True)
        peer.add_message("refused")
        handler = BlockingHandler(block=False)
        original = (
            peer.event("refused") if path == "live" else backlog_message("refused")
        )
        async with rest_client_over(peer.answer) as rest:
            ctx = auxiliary_context(rest, handler)
            assert await attempt_delivery(ctx, original) is TurnOutcome.RETRY_LATER
            while_stopped = (list(handler.invoked), list(peer.accepted_marks))
            peer.stopped = False
            # The single retry allowed is still unspent.
            assert (
                await attempt_delivery(ctx, backlog_message("refused"))
                is TurnOutcome.ADVANCED
            )

        assert while_stopped == ([], [])
        assert handler.completed == ["refused"]
        assert peer.accepted_marks == [
            ("refused", "processing"),
            ("refused", "processed"),
        ]

    async def test_pending_stop_cleanup_defers_claims_with_bounded_wait(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        release, entered = asyncio.Event(), asyncio.Event()

        async def observer(ctx: ExecutionContext, scope: TurnScope) -> None:
            entered.set()
            await release.wait()

        peer = LifecyclePlatform()
        peer.add_message("later")
        async with rest_client_over(peer.answer) as rest:
            ctx = auxiliary_context(rest, AsyncMock(), observer=observer)
            await ctx.begin_delivery()
            ctx.observe_platform_stop(ctx.current_scope)
            await entered.wait()
            monkeypatch.setattr(
                "band.core.turn_lifecycle.CYCLE_CANCEL_GRACE_SECONDS", 0.01
            )
            assert (
                await attempt_delivery(ctx, peer.event("later"))
                is TurnOutcome.RETRY_LATER
            )
            during_cleanup = list(peer.requested_marks)
            release.set()
            assert (
                await attempt_delivery(ctx, peer.event("later")) is TurnOutcome.ADVANCED
            )

        assert during_cleanup == []
        assert peer.requested_marks == [("later", "processing"), ("later", "processed")]
