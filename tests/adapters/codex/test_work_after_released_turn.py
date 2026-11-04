"""Room work refused while a released Codex turn finishes runs once it ends."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
import pytest_asyncio

from band.adapters.codex import CodexAdapter, CodexAdapterConfig, CodexCommand
from band.client.streaming import ControlMode
from band.integrations.codex.types import CodexRequestMethod
from band.platform.event import ReconnectedEvent
from band.platform.link import BandLink
from band.runtime.control import apply_control
from band.runtime.execution import ExecutionContext
from band.runtime.sessionconfig import SessionConfig
from band.runtime.tools import BandTool
from tests.adapters.codex.kit import (
    PATIENT_APPROVAL_S,
    ROOM_ID,
    answered_turn,
    approval_tokens_from_chat,
    parked_on_approval,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    await_released_turn,
    make_codex_adapter,
    tool_call_request,
    turn_completed,
)
from tests.adapters.lifecycle.runtime import ReplyingPlatform
from tests.runtime.conftest import wait_for_condition
from tests.runtime.helpers import (
    AGENT_ID,
    NO_PERIODIC_WAKE_S,
    rest_client_over,
    run_through,
)

FINAL_REPLY = "Tests pass."
RELEASED_TURN = "turn-1"
HELD = ["first", "second"]
# The /next reads one live delivery costs the room, with no extra pass.
LIVE_TURN_NEXT_READS = 1


class CodexRoomUnderTest:
    """A manual-approval Codex adapter behind the real room scheduler."""

    def __init__(
        self,
        platform: ReplyingPlatform,
        adapter: CodexAdapter,
        client: FakeCodexClient,
        ctx: ExecutionContext,
    ) -> None:
        self.platform = platform
        self.adapter = adapter
        self.client = client
        self.ctx = ctx

    @property
    def processed(self) -> list[str]:
        return self.platform.marked("processed")

    @property
    def turn_starts(self) -> int:
        return self.client.request_methods.count(CodexRequestMethod.TURN_START)

    @property
    def idle(self) -> bool:
        """No scheduler pass is running or queued."""
        return self.ctx.queue.empty() and self.ctx.scheduler.current_pass is None

    async def deliver(self, message_id: str, content: str) -> None:
        self.platform.add_message(message_id, content=content)
        await self.ctx.on_event(self.platform.event(message_id, content=content))

    async def processes(self, *message_ids: str) -> None:
        await wait_for_condition(lambda: self.processed == list(message_ids))

    async def open_window(self) -> None:
        """Approve the turn and let it post its final reply, without ending it."""
        await self.deliver("ask", "run tests")
        await self.processes("ask")
        token = approval_tokens_from_chat(self.platform.replies)[-1]
        await self.deliver("approve", f"/{CodexCommand.APPROVE} {token}")
        await self.processes("ask", "approve")
        self.client.emit_turn(
            RELEASED_TURN,
            tool_call_request(
                1,
                BandTool.SEND_MESSAGE,
                {"content": FINAL_REPLY, "mentions": ["@alice"]},
            ),
        )
        await wait_for_condition(lambda: FINAL_REPLY in self.platform.replies)

    async def hold(self, *message_ids: str) -> None:
        """Send ordinary messages that the still-running turn refuses."""
        for message_id in message_ids:
            await self.deliver(message_id, f"and {message_id}")
        await wait_for_condition(
            lambda: self.ctx.scheduler.deferred_id == message_ids[0]
        )
        await wait_for_condition(lambda: self.idle)

    def end_turn(self) -> None:
        self.client.script(*answered_turn("turn-2"), *answered_turn("turn-3"))
        self.client.emit_turn(RELEASED_TURN, turn_completed(RELEASED_TURN))


@pytest_asyncio.fixture(loop_scope="function")
async def codex_scheduled() -> AsyncIterator[
    Callable[[FakeCodexClient], Awaitable[CodexRoomUnderTest]]
]:
    """Open Codex rooms on the real scheduler; each is stopped at the end."""
    opened: list[CodexRoomUnderTest] = []
    platform = ReplyingPlatform()
    async with rest_client_over(platform.answer) as rest:

        async def open_room(client: FakeCodexClient) -> CodexRoomUnderTest:
            adapter = make_codex_adapter(
                client,
                config=CodexAdapterConfig(
                    approval_mode="manual", approval_wait_timeout_s=PATIENT_APPROVAL_S
                ),
            )
            await adapter.on_started("Agent", "A coding agent")
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            ctx = run_through(
                adapter,
                link,
                config=SessionConfig(
                    enable_context_hydration=False,
                    enable_working_state=False,
                    idle_resync_seconds=NO_PERIODIC_WAKE_S,
                    idle_resync_max_seconds=NO_PERIODIC_WAKE_S,
                ),
            )
            await ctx.start()
            opened.append(room := CodexRoomUnderTest(platform, adapter, client, ctx))
            return room

        yield open_room
        for room in opened:
            await room.ctx.stop()
            await room.adapter.on_cleanup(ROOM_ID)


@pytest_asyncio.fixture(loop_scope="function")
async def room(
    codex_scheduled: Callable[[FakeCodexClient], Awaitable[CodexRoomUnderTest]],
) -> CodexRoomUnderTest:
    return await codex_scheduled(FakeCodexClient(events=list(parked_on_approval())))


class TestWorkAfterReleasedTurn:
    @pytest.mark.asyncio
    async def test_messages_sent_after_the_final_reply_run_in_order_once_the_turn_ends(
        self, room: CodexRoomUnderTest
    ) -> None:
        await room.open_window()
        await room.hold(*HELD)

        room.end_turn()

        await room.processes("ask", "approve", *HELD)
        assert room.platform.marked("failed") == []
        assert room.turn_starts == 3

    @pytest.mark.asyncio
    async def test_a_control_in_the_window_is_answered_while_ordinary_work_waits(
        self, room: CodexRoomUnderTest
    ) -> None:
        await room.open_window()
        await room.hold(*HELD)

        await room.deliver("status", f"/{CodexCommand.STATUS}")
        await room.processes("ask", "approve", "status")
        room.end_turn()

        await room.processes("ask", "approve", "status", *HELD)

    @pytest.mark.asyncio
    async def test_held_work_waits_through_a_stop_and_runs_in_order_after_play(
        self, room: CodexRoomUnderTest
    ) -> None:
        await room.open_window()
        await room.hold(*HELD)

        # Band serves a stopped room no /next work until PLAY.
        room.platform.stopped = True
        await apply_control(room.ctx, ControlMode.STOP, room.adapter.on_interrupt)
        await await_released_turn(room.adapter, ROOM_ID)
        await wait_for_condition(lambda: room.idle)
        assert room.processed == ["ask", "approve"]

        room.client.script(*answered_turn("turn-2"), *answered_turn("turn-3"))
        room.platform.stopped = False
        await apply_control(room.ctx, ControlMode.PLAY, room.adapter.on_interrupt)

        await room.processes("ask", "approve", *HELD)
        assert room.platform.marked("failed") == []

    @pytest.mark.asyncio
    async def test_a_reconnect_in_the_window_runs_each_held_message_once(
        self, room: CodexRoomUnderTest
    ) -> None:
        await room.open_window()
        await room.hold(*HELD)

        reads = len(room.platform.next_reads)
        await room.ctx.on_event(ReconnectedEvent())
        await wait_for_condition(lambda: len(room.platform.next_reads) > reads)
        await wait_for_condition(lambda: room.idle)
        assert room.processed == ["ask", "approve"]
        room.end_turn()

        await room.processes("ask", "approve", *HELD)
        assert room.turn_starts == 3

    @pytest.mark.asyncio
    async def test_a_turn_that_never_released_the_room_requests_no_extra_pass(
        self,
        codex_scheduled: Callable[[FakeCodexClient], Awaitable[CodexRoomUnderTest]],
    ) -> None:
        room = await codex_scheduled(
            FakeCodexClient(events=list(answered_turn(RELEASED_TURN)))
        )
        await wait_for_condition(lambda: room.idle)
        reads = len(room.platform.next_reads)

        await room.deliver("ask", "say hi")
        await room.processes("ask")
        await wait_for_condition(lambda: room.idle)

        assert len(room.platform.next_reads) == reads + LIVE_TURN_NEXT_READS
