"""A refused auxiliary claim unwinds ownership and keeps the trigger replayable."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from band.runtime.execution import ExecutionContext
from tests.conftest import make_message_event
from tests.e2e.baseline.toolkit.control import AuxiliaryClaimRuntime
from tests.runtime.executioninterrupt.kit import (
    auxiliary_context,
    backlog_message,
)
from tests.runtime.helpers import (
    LifecyclePlatform,
    rest_client_over,
)
from tests.runtime.scheduler.driver import attempt_delivery


class TestAuxiliaryClaimRefusal:
    """A stopped room refuses auxiliary claims without losing the trigger."""

    @pytest.mark.parametrize("path", ["live", "backlog"])
    @pytest.mark.parametrize("suppress_cancel", [False, True])
    async def test_auxiliary_refusal_unwinds_ownership_and_preserves_replay(
        self, path: str, suppress_cancel: bool
    ) -> None:
        peer = LifecyclePlatform()
        invoked: list[str] = []
        downstream: list[str] = []
        released: list[bool] = []
        async with rest_client_over(peer.answer) as rest:

            async def handler(ctx: ExecutionContext, event: Any) -> None:
                invoked.append(event.payload.id)
                owned: list[str] = []
                try:
                    for mid in ("aux-1", "aux-2"):
                        assert ctx.claims.try_claim(ctx.room_id, mid)
                        owned.append(mid)
                        if len(invoked) == 1 and mid == "aux-2":
                            peer.stopped = True
                        assert await ctx.claim_message(mid)
                    downstream.append(event.payload.id)
                except asyncio.CancelledError:
                    if not suppress_cancel:
                        raise
                finally:
                    for mid in owned:
                        ctx.claims.release(ctx.room_id, mid)
                    released.append(
                        not set(owned).intersection(
                            ctx.claims.inflight_ids(ctx.room_id)
                        )
                    )

            ctx = auxiliary_context(rest, handler)
            if path == "live":
                await attempt_delivery(ctx, make_message_event(msg_id="trigger"))
            else:
                await attempt_delivery(ctx, backlog_message("trigger"))
            assert downstream == []
            assert peer.requested("processed") == []
            assert peer.requested("failed") == []
            assert released == [True]
            peer.stopped = False
            await ctx.resume_room()
            await attempt_delivery(ctx, backlog_message("trigger"))
            assert downstream == ["trigger"]
            assert invoked == ["trigger", "trigger"]
            assert peer.marked("processed") == ["trigger"]
            assert released == [True, True]

    async def test_auxiliary_observed_stop_prevents_further_claims(self) -> None:
        peer = LifecyclePlatform()
        async with rest_client_over(peer.answer) as rest:

            async def handler(ctx: ExecutionContext, event: Any) -> None:
                peer.stopped = True
                with pytest.raises(asyncio.CancelledError):
                    await ctx.claim_message("refused")
                with pytest.raises(asyncio.CancelledError):
                    await ctx.claim_message("never-requested")

            ctx = auxiliary_context(rest, handler)
            await attempt_delivery(ctx, make_message_event(msg_id="trigger"))
            assert peer.requested_marks == [
                ("trigger", "processing"),
                ("refused", "processing"),
            ]
            assert peer.requested("processed") == []
            assert peer.requested("failed") == []

    @pytest.mark.parametrize("replay_head", ["trigger", "aux-1", "aux-2"])
    async def test_auxiliary_control_runtime_releases_then_commits_replayed_burst(
        self, replay_head: str
    ) -> None:
        peer = LifecyclePlatform()
        control = AuxiliaryClaimRuntime()
        control.auxiliaries.put_nowait("aux-1")
        control.auxiliaries.put_nowait("aux-2")
        control.release_claim.set()
        refuse = True

        def answer(request: httpx.Request) -> httpx.Response:
            if refuse and request.url.path.endswith("/aux-2/processing"):
                peer.stopped = True
            return peer.answer(request)

        async with rest_client_over(answer) as rest:
            ctx = auxiliary_context(rest, control.on_execute)
            await attempt_delivery(ctx, make_message_event(msg_id="trigger"))
            assert control.cancelled.is_set()
            assert not control.owned_ids
            assert not control.downstream_started.is_set()
            assert peer.requested("processed") == []
            assert peer.requested("failed") == []
            refuse = False
            peer.stopped = False
            await ctx.resume_room()
            await attempt_delivery(ctx, backlog_message(replay_head))
            for mid in ("trigger", "aux-1", "aux-2"):
                await attempt_delivery(ctx, backlog_message(mid))
            assert control.downstream_started.is_set()
            assert control.completed_message_ids == (
                ["trigger"] if replay_head == "trigger" else [replay_head, "trigger"]
            )
            assert peer.requested("failed") == []
            assert sorted(peer.marked("processed")) == ["aux-1", "aux-2", "trigger"]
            assert not ctx.claims.pending_ack_ids(ctx.room_id)
            assert not control.owned_ids
