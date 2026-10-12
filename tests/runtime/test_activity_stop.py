"""Activity acknowledgements preempt the originating turn through real REST parsing."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest

from band.client.streaming import ControlMode
from band.core.turn_lifecycle import CYCLE_CANCEL_GRACE_SECONDS
from band.platform.event import PlatformEvent
from band.platform.link import BandLink
from band.runtime.execution import ExecutionContext
from band.runtime.runtime import AgentRuntime
from band.runtime.tools.agent import AgentTools
from band.runtime.types import SessionConfig
from tests.conftest import BlockingHandler, make_message_event
from tests.runtime.helpers import (
    AGENT_ID,
    IDLE,
    KEEP_ALIVE,
    ROOM_ID,
    STOP_ECHO,
    ActivityReport,
    LifecyclePlatform,
    rest_client_over,
    running_execution,
)


class ActivityPeer(LifecyclePlatform):
    """Hold an activity response while controls or the turn's lifetime advance."""

    def __init__(self) -> None:
        super().__init__()
        self.hold = False
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def respond(self, request: httpx.Request) -> httpx.Response:
        response = self.answer(request)
        if self.hold and request.url.path.endswith("/activity"):
            self.hold = False
            self.entered.set()
            await self.release.wait()
        return response


@asynccontextmanager
async def activity_context(
    peer: ActivityPeer,
    handler: Callable[[ExecutionContext, PlatformEvent], Awaitable[None]],
    observer: AsyncMock,
    *,
    enabled: bool = True,
    max_cycle_seconds: float | None = None,
) -> AsyncIterator[ExecutionContext]:
    async with rest_client_over(peer.respond) as rest:
        link = BandLink(agent_id=AGENT_ID, api_key="test-key")
        link.rest = rest
        runtime = AgentRuntime(
            link=link, agent_id=AGENT_ID, on_execute=handler, on_control=observer
        )
        ctx = ExecutionContext(
            ROOM_ID,
            link,
            handler,
            agent_id=AGENT_ID,
            on_platform_stop=runtime._on_platform_stop,
            config=SessionConfig(
                enable_context_hydration=False,
                enable_working_state=enabled,
                max_cycle_seconds=max_cycle_seconds,
            ),
        )
        try:
            yield ctx
        finally:
            peer.release.set()
            await ctx.stop()


@pytest.mark.looptime
async def test_heartbeat_stop_during_watchdog_cleanup_keeps_stop_disposition() -> None:
    peer = ActivityPeer()
    observer = AsyncMock()
    keep_alive = SessionConfig().working_keep_alive_seconds

    async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
        try:
            await asyncio.Future[None]()
        except asyncio.CancelledError:
            # The platform stops the room while the watchdog's cleanup runs
            # through the next keep-alive.
            peer.stopped = True
            await asyncio.sleep(CYCLE_CANCEL_GRACE_SECONDS - 0.1)
            raise

    async with activity_context(
        peer, handler, observer, max_cycle_seconds=keep_alive - 0.5
    ) as ctx:
        assert not await ctx._process_event(make_message_event(msg_id="trigger"))

        assert peer.activity_reports == [KEEP_ALIVE, STOP_ECHO, IDLE]
        assert peer.marked("processing") == ["trigger"]
        assert peer.requested("failed") == []
        observer.assert_awaited_once()


@pytest.mark.looptime
async def test_heartbeat_stop_cancels_once_and_preserves_replay() -> None:
    peer = ActivityPeer()
    handler = BlockingHandler(block=1)
    observer = AsyncMock()
    async with activity_context(peer, handler, observer) as ctx:
        event = make_message_event(msg_id="trigger")
        async with asyncio.TaskGroup() as tasks:
            turn = tasks.create_task(ctx._process_event(event))
            await handler.started.wait()
            peer.stopped = True
            await turn

        assert handler.cancelled.is_set()
        assert handler.completed == []
        assert peer.activity_reports == [KEEP_ALIVE, STOP_ECHO, IDLE]
        assert peer.marked("processing") == ["trigger"]
        assert peer.requested("processed") == []
        assert peer.requested("failed") == []
        observer.assert_awaited_once()

        peer.stopped = False
        await ctx.resume_room()
        await ctx._process_event(event)
        assert handler.invoked == ["trigger", "trigger"]
        assert handler.completed == ["trigger"]
        assert peer.marked("processed") == ["trigger"]
        observer.assert_awaited_once()


async def test_stop_on_initial_activity_report_never_enters_handler() -> None:
    peer = ActivityPeer()
    peer.stopped = True
    handler = BlockingHandler(block=False)
    observer = AsyncMock()
    async with activity_context(peer, handler, observer) as ctx:
        scope = await ctx._begin_scope()
        assert scope is not None
        assert not await ctx._run_cycle(
            scope, make_message_event(msg_id="trigger"), "trigger"
        )

        assert handler.invoked == []
        assert peer.activity_reports == [STOP_ECHO, IDLE]
        observer.assert_awaited_once()
        assert peer.requested("processed") == []
        assert peer.requested("failed") == []


async def test_repeated_activity_refusal_does_not_cancel_handler_cleanup() -> None:
    peer = ActivityPeer()
    started = asyncio.Event()
    cancelled = asyncio.Event()
    finish = asyncio.Event()
    cleaned_up = asyncio.Event()
    observer = AsyncMock()

    async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
        started.set()
        try:
            await asyncio.Future[None]()
        except asyncio.CancelledError:
            cancelled.set()
            await finish.wait()
            cleaned_up.set()

    async with activity_context(peer, handler, observer, enabled=False) as ctx:
        async with asyncio.TaskGroup() as tasks:
            turn = tasks.create_task(
                ctx._process_event(make_message_event(msg_id="trigger"))
            )
            await started.wait()
            peer.stopped = True
            await ctx._report_working_state(True)
            await cancelled.wait()
            await ctx._report_working_state(True)
            finish.set()
            await turn
            await cleaned_up.wait()

        assert cleaned_up.is_set()
        observer.assert_awaited_once()
        assert peer.requested("processed") == []
        assert peer.requested("failed") == []


@pytest.mark.looptime
async def test_activity_refusal_preserves_native_stop_cleanup() -> None:
    peer = ActivityPeer()
    started = asyncio.Event()
    reports_at_cleanup: list[ActivityReport] = []
    cleaning, release = asyncio.Event(), asyncio.Event()
    observer = AsyncMock()

    async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
        started.set()
        try:
            await asyncio.Future[None]()
        finally:
            cleaning.set()
            await release.wait()
            reports_at_cleanup.extend(peer.activity_reports)

    async with activity_context(peer, handler, observer) as ctx:
        async with asyncio.TaskGroup() as tasks:
            turn = tasks.create_task(
                ctx._process_event(make_message_event(msg_id="trigger"))
            )
            await started.wait()
            peer.stopped = True
            ctx.stop_room()
            await cleaning.wait()
            await ctx._report_working_state(True)
            release.set()
            await turn

        # The final idle report follows the cleanup it reports on.
        assert reports_at_cleanup == [KEEP_ALIVE, STOP_ECHO]
        assert peer.activity_reports == [*reports_at_cleanup, IDLE]
        observer.assert_not_awaited()
        assert peer.marked("processing") == ["trigger"]
        assert peer.requested("processed") == []
        assert peer.requested("failed") == []


@pytest.mark.parametrize("boundary", ["play", "replacement", "shutdown"])
async def test_stale_activity_refusal_does_not_stop_a_newer_scope(
    boundary: str,
) -> None:
    peer = ActivityPeer()
    peer.stopped = True
    peer.hold = True
    started = asyncio.Event()
    finish = asyncio.Event()
    completed: list[PlatformEvent] = []

    async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
        started.set()
        await finish.wait()
        completed.append(event)

    observer = AsyncMock()
    async with activity_context(peer, handler, observer, enabled=False) as ctx:
        scope = await ctx._begin_scope()
        async with asyncio.TaskGroup() as tasks:
            report = tasks.create_task(ctx._report_working_state(True))
            await peer.entered.wait()
            match boundary:
                case "play":
                    await ctx.resume_room()
                case "replacement":
                    scope = await ctx._begin_scope()
                case "shutdown":
                    await ctx.stop()
                    peer.release.set()
                    assert await report
                    observer.assert_not_awaited()
                    return
            event = make_message_event(msg_id="newer")
            assert scope is not None
            turn = tasks.create_task(ctx._run_cycle(scope, event, "newer"))
            await started.wait()
            peer.release.set()
            assert await report
            finish.set()
            assert await turn

        observer.assert_not_awaited()
        assert completed == [event]


async def test_shutdown_during_initial_activity_report_cleans_up_reporter() -> None:
    peer = ActivityPeer()
    peer.hold = True
    handler = BlockingHandler(block=False)
    observer = AsyncMock()
    async with activity_context(peer, handler, observer) as ctx:
        async with asyncio.TaskGroup() as tasks:
            turn = tasks.create_task(
                ctx._process_event(make_message_event(msg_id="trigger"))
            )
            await peer.entered.wait()
            turn.cancel()
            with pytest.raises(asyncio.CancelledError):
                await turn
        assert handler.invoked == []
        assert peer.activity_reports == [KEEP_ALIVE, IDLE]
        observer.assert_not_awaited()


async def test_disabled_working_state_keeps_its_existing_contract() -> None:
    peer = ActivityPeer()
    handler = BlockingHandler(block=False)
    async with activity_context(peer, handler, AsyncMock(), enabled=False) as ctx:
        await ctx._process_event(make_message_event(msg_id="trigger"))
        assert handler.completed == ["trigger"]
        assert peer.activity_reports == []


@pytest.mark.parametrize("play_after_stop", [False, True])
async def test_fresh_activity_stop_after_play_cancels_the_same_turn(
    play_after_stop: bool,
) -> None:
    peer = ActivityPeer()
    handler = BlockingHandler(block=1)
    observer = AsyncMock()
    async with (
        activity_context(peer, handler, observer, enabled=False) as ctx,
        asyncio.TaskGroup() as tasks,
    ):
        turn = tasks.create_task(
            ctx._process_event(make_message_event(msg_id="trigger"))
        )
        await handler.started.wait()
        await ctx.resume_room()
        peer.stopped = True
        assert await ctx._report_working_state(True)
        if play_after_stop:
            await ctx.resume_room()
        await asyncio.sleep(0)
        assert handler.cancelled.is_set()
        await turn
        if play_after_stop:
            observer.assert_not_awaited()
        else:
            observer.assert_awaited_once_with(ROOM_ID, ControlMode.STOP)
        assert peer.marked("processing") == ["trigger"]
        assert peer.requested("processed") == []
        assert peer.requested("failed") == []


@pytest.mark.parametrize("refusal", ["post", "claim"])
async def test_stopped_tool_preserves_awaited_cleanup(refusal: str) -> None:
    peer = ActivityPeer()
    cleanup: list[str] = []
    cleaning, release = asyncio.Event(), asyncio.Event()

    async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
        peer.stopped = True
        try:
            if refusal == "claim":
                await ctx.claim_message("auxiliary")
            else:
                await AgentTools.from_context(ctx).send_message(
                    "reply", mentions=[{"id": "user-1", "type": "user"}]
                )
        finally:
            cleaning.set()
            await release.wait()
            cleanup.append("completed")

    async with activity_context(peer, handler, AsyncMock(), enabled=False) as ctx:
        turn = asyncio.create_task(
            ctx._process_event(make_message_event(msg_id="trigger"))
        )
        await cleaning.wait()
        release.set()
        await turn
    assert cleanup == ["completed"]
    assert peer.requested("processed") == []
    assert peer.requested("failed") == []


@pytest.mark.parametrize(
    ("first", "second", "processed"),
    [
        (ControlMode.STOP, ControlMode.STOP, []),
        (ControlMode.STOP, ControlMode.INTERRUPT, ["trigger"]),
        (ControlMode.INTERRUPT, ControlMode.STOP, []),
    ],
)
async def test_started_room_controls_preserve_cleanup_and_latest_disposition(
    first: ControlMode, second: ControlMode, processed: list[str]
) -> None:
    peer = ActivityPeer()
    started, cleaning, release, finished = (asyncio.Event() for _ in range(4))
    cleanup: list[str] = []

    async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
        started.set()
        try:
            await asyncio.Future[None]()
        finally:
            cleaning.set()
            await release.wait()
            cleanup.append("completed")
            finished.set()

    async with activity_context(peer, handler, AsyncMock()) as ctx:
        async with running_execution(ctx):
            await ctx.on_event(make_message_event(msg_id="trigger"))
            await started.wait()
            ctx.interrupt(kind=first)
            await cleaning.wait()
            ctx.interrupt(kind=second)
            release.set()
            await finished.wait()
            await ctx._idle_event.wait()
        assert cleanup == ["completed"]
        assert peer.requested("processed") == processed
        assert peer.requested("failed") == []
        assert peer.activity_reports == [KEEP_ALIVE, IDLE]
