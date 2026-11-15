"""Detached provider failure reports retain their originating turn."""

from __future__ import annotations

import asyncio

import pytest
from band_sdk_core import AgentFailure

from band.adapters.codex import CODEX_PROVIDER, TRANSPORT_LOST_ERROR, CodexAdapterConfig
from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE, TURN_FAILURE_PROVIDER
from band.core.turn_lifecycle import bind_turn_task
from band.integrations.codex import TRANSPORT_CLOSED, RpcEvent
from band.integrations.codex.types import CODEX_TRANSPORT_CLOSED_FAILURE_CODE
from band.platform.event import PlatformEvent
from band.runtime.execution import ExecutionContext
from band.runtime.sessionconfig import SessionConfig
from band.runtime.tools.agent import AgentTools
from tests.adapters.codex.kit import parked_on_approval
from tests.adapters.codexturns import (
    FakeCodexClient,
    event_notification,
    make_codex_adapter,
)
from tests.adapters.hold import Hold
from tests.conftest import make_message_event
from tests.platform.settlement.kit import link_over
from tests.runtime.activityownership.kit import ActivityPlatform, deliver_event
from tests.runtime.helpers import ALICE, ROOM_ID, rest_client_over, run_through


class TestDetachedProviderFailure:
    @pytest.mark.parametrize(
        ("report", "preceding", "expected"),
        [
            (
                True,
                [],
                [
                    {
                        "provider": CODEX_PROVIDER,
                        "code": CODEX_TRANSPORT_CLOSED_FAILURE_CODE,
                        "message": TRANSPORT_LOST_ERROR,
                        "detail": None,
                    }
                ],
            ),
            (
                True,
                [
                    event_notification(
                        "error",
                        {"error": {"message": "Provider failed"}, "willRetry": False},
                    )
                ],
                [
                    {
                        "provider": CODEX_PROVIDER,
                        "code": None,
                        "message": "Provider failed",
                        "detail": {
                            "codex_room_id": ROOM_ID,
                            "codex_thread_id": "thr-1",
                            "codex_turn_id": "turn-1",
                        },
                    }
                ],
            ),
            (False, [], []),
        ],
        ids=["transport", "first-error-once", "disabled"],
    )
    async def test_lost_approval_reports_without_resettling_its_delivery(
        self, report: bool, preceding: list[RpcEvent], expected: list[dict[str, object]]
    ) -> None:
        platform = ActivityPlatform()
        client = FakeCodexClient(events=[*preceding, *parked_on_approval()])
        adapter = make_codex_adapter(
            client,
            config=CodexAdapterConfig(
                approval_mode="manual", approval_wait_timeout_s=3600
            ),
            emit=(),
        )
        await adapter.on_started("Agent", "Agent")
        async with rest_client_over(platform.answer) as rest:
            ctx = run_through(
                adapter,
                link_over(rest),
                config=SessionConfig(
                    enable_context_hydration=False,
                    enable_working_state=False,
                    max_cycle_seconds=None,
                    report_turn_failures_to_room=report,
                ),
            )
            ctx.set_participants([ALICE])
            try:
                await deliver_event(
                    ctx, make_message_event(room_id=ROOM_ID, msg_id="parked")
                )
                worker = adapter._turn_tasks[ROOM_ID].task
                client.emit(
                    event_notification(TRANSPORT_CLOSED, {"reason": "Process exited"})
                )
                await asyncio.gather(worker, return_exceptions=True)

                assert platform.failures == expected
                assert platform.marked("processed") == ["parked"]
                assert platform.marked("failed") == []
            finally:
                await ctx.stop()
                await adapter.cleanup_all()

    async def test_late_failure_does_not_suppress_a_newer_foreground_failure(
        self,
    ) -> None:
        platform = ActivityPlatform()
        foreground = Hold()
        fail_old = asyncio.Event()
        captured: list[AgentTools] = []
        workers: list[asyncio.Task[None]] = []
        failure = AgentFailure(
            CODEX_PROVIDER, TRANSPORT_LOST_ERROR, CODEX_TRANSPORT_CLOSED_FAILURE_CODE
        )

        async def park(ctx: ExecutionContext, event: PlatformEvent) -> None:
            tools = AgentTools.from_context(ctx)
            captured.append(tools)

            async def work() -> None:
                await fail_old.wait()
                await tools.send_failure(failure)

            worker = asyncio.create_task(work())
            workers.append(worker)
            bind_turn_task(tools, worker).fail(
                ConnectionError("private provider credentials"), cooperative=True
            )
            tools.turn.detach()

        async def fail_new(ctx: ExecutionContext, event: PlatformEvent) -> None:
            await foreground.wait()
            raise RuntimeError("private newer credentials")

        handlers = iter((park, fail_new))

        async def handle(ctx: ExecutionContext, event: PlatformEvent) -> None:
            await next(handlers)(ctx, event)

        async with rest_client_over(platform.answer) as rest:
            ctx = ExecutionContext(
                ROOM_ID,
                link_over(rest),
                handle,
                config=SessionConfig(
                    enable_context_hydration=False,
                    enable_working_state=False,
                    max_cycle_seconds=None,
                ),
            )
            newer: asyncio.Task[None] | None = None
            try:
                await deliver_event(
                    ctx, make_message_event(room_id=ROOM_ID, msg_id="old")
                )
                newer = asyncio.create_task(
                    deliver_event(
                        ctx, make_message_event(room_id=ROOM_ID, msg_id="new")
                    )
                )
                await foreground.arrived()
                fail_old.set()
                await workers[0]
                with pytest.raises(asyncio.CancelledError):
                    await captured[0].send_notice("stale", mentions=["alice"])
                foreground.released.set()
                await newer

                assert platform.failures == [
                    {
                        "provider": CODEX_PROVIDER,
                        "code": CODEX_TRANSPORT_CLOSED_FAILURE_CODE,
                        "message": TRANSPORT_LOST_ERROR,
                        "detail": None,
                    },
                    {
                        "provider": TURN_FAILURE_PROVIDER,
                        "code": None,
                        "message": GENERIC_PROVIDER_FAILURE_MESSAGE,
                        "detail": None,
                    },
                ]
                assert platform.marked("processed") == ["old"]
                assert platform.marked("failed") == ["new"]
                assert platform.room_messages == []
            finally:
                fail_old.set()
                foreground.released.set()
                await asyncio.gather(*workers, return_exceptions=True)
                if newer is not None:
                    await asyncio.gather(newer, return_exceptions=True)
                await ctx.stop()
