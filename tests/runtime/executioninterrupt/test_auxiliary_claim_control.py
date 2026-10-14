"""Control signals arriving while an auxiliary claim is in flight."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from band.runtime.cycle import TurnScope
from band.runtime.execution import ExecutionContext
from band.runtime.tools.agent import AgentTools
from tests.conftest import make_message_event
from tests.runtime.executioninterrupt.kit import (
    auxiliary_context,
)
from tests.runtime.helpers import (
    ClaimGate,
    LifecyclePlatform,
    rest_client_over,
)


class TestAuxiliaryClaimControl:
    """Stop, interrupt and play decide whether a claiming turn may continue."""

    @pytest.mark.parametrize("control", ["stop", "interrupt", "play", "stop-play"])
    async def test_auxiliary_claim_checks_control_after_suppressed_transport_cancel(
        self,
        control: str,
    ) -> None:
        peer = LifecyclePlatform()
        gate = ClaimGate(peer, "aux", suppress_cancel=True)
        downstream: list[str] = []
        async with rest_client_over(gate.answer) as rest:

            async def handler(ctx: ExecutionContext, event: Any) -> None:
                assert await ctx.claim_message("aux")
                downstream.append(event.payload.id)

            ctx = auxiliary_context(rest, handler)
            async with asyncio.TaskGroup() as tasks:
                processing = tasks.create_task(
                    ctx._process_event(make_message_event(msg_id="trigger"))
                )
                await gate.entered.wait()
                match control:
                    case "stop" | "stop-play":
                        ctx.stop_room()
                    case "interrupt":
                        assert ctx.interrupt()
                if control in {"play", "stop-play"}:
                    await ctx.resume_room()
                gate.release.set()
                await processing
            assert downstream == (["trigger"] if control == "play" else [])
            assert peer.marked("processed") == (
                ["trigger"] if control in {"play", "interrupt"} else []
            )
            assert peer.requested("failed") == []

    @pytest.mark.parametrize("new_scope", [False, True])
    @pytest.mark.parametrize("refused", [False, True])
    async def test_detached_auxiliary_response_cannot_continue_or_notify_room(
        self, new_scope: bool, refused: bool
    ) -> None:
        peer = LifecyclePlatform()
        gate = ClaimGate(
            peer,
            "old-aux",
            response=httpx.Response(204) if refused else None,
            suppress_cancel=True,
        )
        old_finished, new_started, finish_new = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )
        downstream: list[str] = []
        notified: list[str] = []
        misuse_rejected: list[bool] = []

        async def observer(ctx: ExecutionContext, scope: TurnScope) -> None:
            notified.append(ctx.room_id)

        async with rest_client_over(gate.answer) as rest:

            async def handler(ctx: ExecutionContext, event: Any) -> None:
                if event.payload.id == "old":
                    try:
                        await ctx.claim_message("old-aux")
                        downstream.append("old")
                    except asyncio.CancelledError:
                        with pytest.raises(RuntimeError, match="active handler"):
                            await ctx.claim_message("detached")
                        misuse_rejected.append(True)
                        raise
                    finally:
                        old_finished.set()
                else:
                    new_started.set()
                    await finish_new.wait()
                    downstream.append("new")

            ctx = auxiliary_context(
                rest, handler, observer=observer, max_cycle_seconds=60
            )
            processing = asyncio.create_task(
                ctx._process_event(make_message_event(msg_id="old"))
            )
            await gate.entered.wait()
            processing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await processing
            await gate.cancelled.wait()
            async with asyncio.TaskGroup() as tasks:
                if new_scope:
                    newer = tasks.create_task(
                        ctx._process_event(make_message_event(msg_id="new"))
                    )
                    await new_started.wait()
                gate.release.set()
                await old_finished.wait()
                finish_new.set()
                if new_scope:
                    await newer
            assert downstream == (["new"] if new_scope else [])
            assert notified == []
            assert misuse_rejected == [True]
            assert peer.marked("processed") == (["new"] if new_scope else [])
            assert peer.requested("failed") == []

    async def test_auxiliary_stop_during_accepted_claim_cannot_continue(self) -> None:
        peer = LifecyclePlatform()
        gate = ClaimGate(peer, "aux", suppress_cancel=True)
        captured: list[AgentTools] = []
        downstream: list[str] = []
        async with rest_client_over(gate.answer) as rest:

            async def handler(ctx: ExecutionContext, event: Any) -> None:
                captured.append(AgentTools.from_context(ctx))
                await ctx.claim_message("aux")
                downstream.append(event.payload.id)

            ctx = auxiliary_context(rest, handler)
            async with asyncio.TaskGroup() as tasks:
                processing = tasks.create_task(
                    ctx._process_event(make_message_event(msg_id="trigger"))
                )
                await gate.entered.wait()
                with pytest.raises(asyncio.CancelledError):
                    await captured[0].send_event("late", "thought")
                gate.release.set()
                await processing
            assert downstream == []
            assert peer.requested("processed") == []
            assert peer.requested("failed") == []
