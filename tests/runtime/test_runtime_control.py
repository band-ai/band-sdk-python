"""Tests for AgentRuntime.handle_control routing/dedup."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.client.streaming import AgentControlPayload, ControlMode
from band.platform.event import ReconnectedEvent
from band.platform.link import BandLink
from band.runtime.execution import ExecutionContext, ResyncRequest
from band.runtime.runtime import AgentRuntime
from band.runtime.types import PlatformMessage
from band.testing.platform import platform_connection_stub


def _control(mode: str, scope: str = "agent", **kw) -> AgentControlPayload:
    return AgentControlPayload(mode=mode, scope=scope, agent_id="agent-123", **kw)


async def never_executes(_context: ExecutionContext, event: object) -> None:
    raise AssertionError(f"No cycle should run, got {event!r}")


@pytest.fixture
def link() -> BandLink:
    """A real link that is never connected: control routing needs no I/O."""
    platform = platform_connection_stub()
    return BandLink(
        agent_id=platform.agent_id,
        api_key=platform.api_key,
        ws_url=platform.ws_url,
        rest_url=platform.rest_url,
    )


class ControlledRooms:
    """Real idle room executions under one runtime, observed the way an
    adapter sees control: the aborts its ``on_interrupt`` hears, and the
    catch-ups a play queues."""

    def __init__(self, link: BandLink, *room_ids: str) -> None:
        self.heard: list[tuple[str, ControlMode]] = []
        self.runtime = AgentRuntime(
            link=link,
            agent_id=link.agent_id,
            on_execute=never_executes,
            on_control=self.on_interrupt,
        )
        self.executions = {
            room_id: ExecutionContext(
                room_id, link, never_executes, agent_id=link.agent_id
            )
            for room_id in room_ids
        }
        self.runtime.executions = dict(self.executions)

    async def on_interrupt(self, room_id: str, mode: ControlMode) -> None:
        self.heard.append((room_id, mode))

    async def signal(self, mode: str, **payload: object) -> None:
        await self.runtime.handle_control(_control(mode, **payload))

    def resyncs(self, room_id: str) -> int:
        """Drain the room's queue, counting the /next catch-ups requested."""
        queue = self.executions[room_id].queue
        drained = [queue.get_nowait() for _ in range(queue.qsize())]
        return sum(isinstance(item, ResyncRequest) for item in drained)


@pytest.fixture
def rooms(link: BandLink) -> ControlledRooms:
    return ControlledRooms(link, "r1", "r2")


class TestRouting:
    """The runtime's own interrupt()/stop_room() only cancel the task that
    invoked the handler, so every abort is also forwarded to the adapter's
    on_interrupt -- even with no cycle to cancel, since the adapter may keep
    a turn running detached (e.g. parked on a human decision)."""

    async def test_signals_reach_exactly_the_rooms_they_name(
        self, rooms: ControlledRooms
    ) -> None:
        await rooms.signal("interrupt", scope="agent", room_id=None)
        await rooms.signal("stop", scope="room", room_id="r2")
        await rooms.signal("interrupt", scope="agent", room_id="r1")
        await rooms.signal("interrupt", scope="room", room_id="ghost")
        await rooms.signal("interrupt", scope="room", room_id=None)
        await rooms.signal("play", scope="room", room_id="r2")

        assert rooms.heard == [
            ("r1", ControlMode.INTERRUPT),
            ("r2", ControlMode.INTERRUPT),
            ("r2", ControlMode.STOP),
            ("r1", ControlMode.INTERRUPT),
        ]
        assert (rooms.resyncs("r1"), rooms.resyncs("r2")) == (0, 1)

    async def test_a_correlation_id_applies_its_signal_once(
        self, rooms: ControlledRooms
    ) -> None:
        """The server does not dedupe; signals without an id cannot be."""
        await rooms.signal(
            "interrupt", scope="room", room_id="r1", correlation_id="ctl-1"
        )
        await rooms.signal(
            "interrupt", scope="room", room_id="r1", correlation_id="ctl-1"
        )
        await rooms.signal(
            "interrupt", scope="room", room_id="r1", correlation_id="ctl-2"
        )
        await rooms.signal(
            "stop", scope="room", room_id="r2", correlation_id="ctl-stop"
        )
        await rooms.signal(
            "play", scope="room", room_id="r2", correlation_id="ctl-play"
        )
        await rooms.signal("stop", scope="room", room_id="r2")
        await rooms.signal("stop", scope="room", room_id="r2")

        assert rooms.heard == [
            ("r1", ControlMode.INTERRUPT),
            ("r1", ControlMode.INTERRUPT),
            ("r2", ControlMode.STOP),
            ("r2", ControlMode.STOP),
            ("r2", ControlMode.STOP),
        ]
        assert rooms.resyncs("r2") == 1


class TestStopSurvivesReconnect:
    async def test_reconnect_while_stopped_does_not_invoke_adapter(self):
        """After stop, a reconnect (ReconnectedEvent -> /next sync incl. stale
        recovery) must NOT re-fire the adapter. Needs no SDK persistence — the
        local _stopped guard + platform /next->204 keep the room quiet.

        Asserts the adapter is NOT invoked (covers both the no-persistence claim
        and the recovery-sweep guard), per architect's Step-4 should-fix.
        """
        link = MagicMock()
        link.agent_id = "agent-123"
        link.rest = MagicMock()
        link.rest.agent_api_participants = MagicMock()
        link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
            return_value=MagicMock(data=[])
        )
        link.rest.agent_api_context = MagicMock()
        link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
            return_value=MagicMock(data=[])
        )
        link.mark_processing = AsyncMock(return_value=True)
        link.mark_processed = AsyncMock(return_value=True)
        link.mark_failed = AsyncMock(return_value=True)
        # Platform /next gate returns 204 (None) for a stopped agent — that path
        # is platform-authoritative. The LOCAL risk is the recovery sweep, which
        # fetches 'processing' messages DIRECTLY (bypassing /next): the stop path
        # leaves the interrupted message there. The _stopped guard must skip it.
        stuck = PlatformMessage(
            id="stuck",
            room_id="room-123",
            content="hi",
            sender_id="u1",
            sender_type="User",
            sender_name="U1",
            message_type="text",
            metadata={},
            created_at=None,
        )
        link.get_stale_processing_messages = AsyncMock(return_value=[stuck])
        link.get_next_message = AsyncMock(return_value=None)  # /next gate: 204

        executed: list[str] = []

        async def on_execute(ctx, event):
            executed.append(event.payload.id)

        ctx = ExecutionContext("room-123", link, on_execute, agent_id="agent-123")
        ctx._stopped = True
        ctx._reconnect_sync_requested = True

        # Drive the reconnect sync path directly.
        await ctx._process_event(ReconnectedEvent())

        assert executed == []  # adapter never invoked while stopped
        link.mark_processing.assert_not_awaited()
        # Recovery sweep (the gate-bypassing path) was skipped locally.
        link.get_stale_processing_messages.assert_not_awaited()
        # Efficiency: the reconnect sync must short-circuit on _stopped locally,
        # same as the idle-timeout and resync-sentinel paths, instead of making
        # a /next call that's guaranteed to come back empty.
        link.get_next_message.assert_not_awaited()


class TestGracefulDegradation:
    async def test_every_mode_is_a_no_op_without_the_optional_hooks(
        self, link: BandLink
    ) -> None:
        """No adapter on_interrupt, and a custom Execution lacking the
        control methods: every mode degrades to a no-op instead of raising."""

        class BareExecution:
            room_id = "r1"

        runtime = AgentRuntime(
            link=link, agent_id=link.agent_id, on_execute=never_executes
        )
        runtime.executions = {"r1": BareExecution()}  # type: ignore[dict-item]

        for mode in ("interrupt", "stop", "play"):
            await runtime.handle_control(_control(mode, scope="room", room_id="r1"))
