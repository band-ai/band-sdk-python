"""Agent runner subscriptions."""

from __future__ import annotations

from unittest.mock import MagicMock

from band.platform.event import (
    RoomDeletedEvent,
)
from tests.bridge.conftest import FakeForwarder
from tests.bridge.kit import (
    build_runner,
    make_message_event,
    make_room_added_event,
    make_room_removed_event,
)


class TestAgentRunnerSubscriptions:
    async def test_room_added_subscribes(self) -> None:
        runner, _, link = build_runner()
        await runner._handle_event(make_room_added_event(room_id="r-new"))

        link.subscribe_room.assert_awaited_once_with("r-new")

    async def test_room_removed_unsubscribes(self) -> None:
        runner, _, link = build_runner()
        await runner._handle_event(make_room_removed_event(room_id="r-gone"))

        link.unsubscribe_room.assert_awaited_once_with("r-gone")

    async def test_room_deleted_also_unsubscribes(self) -> None:
        runner, _, link = build_runner()
        event = RoomDeletedEvent(
            type="room_deleted",
            room_id="r-deleted",
            payload=MagicMock(model_dump=MagicMock(return_value={})),
            raw={},
        )

        await runner._handle_event(event)

        link.unsubscribe_room.assert_awaited_once_with("r-deleted")

    async def test_subscribe_failure_is_logged_not_raised(self) -> None:
        runner, fwd, link = build_runner()
        link.subscribe_room.side_effect = RuntimeError("subscribe failed")

        await runner._handle_event(make_room_added_event())

        # Event is still forwarded even though subscribe failed.
        assert isinstance(fwd, FakeForwarder)
        assert len(fwd.forwarded) == 1

    async def test_room_removed_evicts_room_lock(self) -> None:
        """The per-room lock is dropped once the room is gone so ``_room_locks``
        does not grow unbounded over a long-lived bridge."""
        runner, _, _ = build_runner()

        # A live message creates the lock.
        await runner._handle_event(make_message_event(room_id="r-gone"))
        assert "r-gone" in runner._room_locks

        # Room teardown evicts it (after its own forward releases the lock).
        await runner._handle_event(make_room_removed_event(room_id="r-gone"))
        assert "r-gone" not in runner._room_locks

    async def test_room_deleted_evicts_room_lock(self) -> None:
        runner, _, _ = build_runner()
        await runner._handle_event(make_message_event(room_id="r-del"))
        assert "r-del" in runner._room_locks

        event = RoomDeletedEvent(
            type="room_deleted",
            room_id="r-del",
            payload=MagicMock(model_dump=MagicMock(return_value={})),
            raw={},
        )
        await runner._handle_event(event)
        assert "r-del" not in runner._room_locks
