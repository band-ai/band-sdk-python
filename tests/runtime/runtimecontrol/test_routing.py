"""The runtime's own interrupt()/stop_room() only cancel the task that"""

from __future__ import annotations

from band.client.streaming import AgentControlPayload, ControlMode, WireEvent
from tests.runtime.runtimecontrol.kit import (
    ControlledRooms,
)


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

    async def test_wire_interrupt_does_not_stop_the_room(
        self, rooms: ControlledRooms
    ) -> None:
        payload = AgentControlPayload.from_wire(
            WireEvent.AGENT_CONTROL,
            {
                "mode": "interrupt",
                "scope": "room",
                "agent_id": "agent-123",
                "correlation_id": "ctl-wire-interrupt",
                "execution_id": None,
                "room_id": "r1",
            },
        )

        await rooms.runtime.handle_control(payload)

        assert rooms.executions["r1"]._stopped is False
        assert rooms.heard == [("r1", ControlMode.INTERRUPT)]

    async def test_a_signal_that_reached_no_room_applies_when_repeated_later(
        self, rooms: ControlledRooms
    ) -> None:
        await rooms.signal("stop", scope="room", room_id="r3", correlation_id="early")
        rooms.route("r3")
        await rooms.signal("stop", scope="room", room_id="r3", correlation_id="early")

        assert rooms.heard == [("r3", ControlMode.STOP)]

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
