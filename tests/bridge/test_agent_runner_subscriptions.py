"""Agent runner subscriptions."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import MagicMock

import pytest

from band.platform.event import (
    RoomDeletedEvent,
)
from tests.bridge.conftest import FakeForwarder
from tests.bridge.kit import (
    build_runner,
    make_room_added_event,
    make_room_removed_event,
)


class TestAgentRunnerSubscriptions:
    async def test_room_added_subscribes(self) -> None:
        runner, _, link = build_runner()
        await runner._safe_handle_event(make_room_added_event(room_id="r-new"))

        link.subscribe_room.assert_awaited_once_with("r-new")

    async def test_room_removed_unsubscribes(self) -> None:
        runner, _, link = build_runner()
        await runner._safe_handle_event(make_room_removed_event(room_id="r-gone"))

        link.unsubscribe_room.assert_awaited_once_with("r-gone")

    async def test_room_deleted_also_unsubscribes(self) -> None:
        runner, _, link = build_runner()
        event = RoomDeletedEvent(
            type="room_deleted",
            room_id="r-deleted",
            payload=MagicMock(model_dump=MagicMock(return_value={})),
            raw={},
        )

        await runner._safe_handle_event(event)

        link.unsubscribe_room.assert_awaited_once_with("r-deleted")

    async def test_subscribe_failure_is_logged_not_raised(self) -> None:
        runner, fwd, link = build_runner()
        link.subscribe_room.side_effect = RuntimeError("subscribe failed")

        await runner._safe_handle_event(make_room_added_event())

        # Event is still forwarded even though subscribe failed.
        assert isinstance(fwd, FakeForwarder)
        assert len(fwd.forwarded) == 1

    @pytest.mark.parametrize(
        "event_factory, operation",
        [
            (make_room_added_event, "subscribe_room"),
            (make_room_removed_event, "unsubscribe_room"),
        ],
    )
    async def test_filter_keeps_subscription_management(
        self, event_factory: Callable[..., object], operation: str
    ) -> None:
        runner, forwarder, link = build_runner(messages_only=True)
        await runner._safe_handle_event(event_factory(room_id="filtered-room"))
        getattr(link, operation).assert_awaited_once_with("filtered-room")
        assert forwarder.forwarded == []
