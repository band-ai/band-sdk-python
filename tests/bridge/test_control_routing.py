"""Bridge control routing."""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.client.streaming import AgentControlPayload
from tests.bridge.conftest import FakeForwarder
from tests.bridge.kit import (
    MultiRoomHangingForwarder,
    await_cancelled,
    build_runner,
    control_signal,
    make_message_event,
)

pytestmark = pytest.mark.asyncio


class TestControlRouting:
    async def test_room_scope_only_cancels_target_room(self) -> None:
        fwd = MultiRoomHangingForwarder(rooms=["r1", "r2"])
        runner, _, _ = build_runner(forwarder=fwd)
        t1 = asyncio.create_task(
            runner._safe_handle_event(make_message_event(message_id="m1", room_id="r1"))
        )
        t2 = asyncio.create_task(
            runner._safe_handle_event(make_message_event(message_id="m2", room_id="r2"))
        )
        await fwd.started["r1"].wait()
        await fwd.started["r2"].wait()

        await runner._control.handle(
            control_signal("interrupt", scope="room", room_id="r1")
        )

        await await_cancelled(t1)
        assert not t2.done()

        t2.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t2

    async def test_agent_scope_null_room_cancels_all_active_forwards(self) -> None:
        fwd = MultiRoomHangingForwarder(rooms=["r1", "r2"])
        runner, _, link = build_runner(forwarder=fwd)
        link.rest.agent_api_chats.list_agent_chats = AsyncMock(
            return_value=MagicMock(data=[MagicMock(id="r1"), MagicMock(id="r2")])
        )
        t1 = asyncio.create_task(
            runner._safe_handle_event(make_message_event(message_id="m1", room_id="r1"))
        )
        t2 = asyncio.create_task(
            runner._safe_handle_event(make_message_event(message_id="m2", room_id="r2"))
        )
        await fwd.started["r1"].wait()
        await fwd.started["r2"].wait()

        await runner._control.handle(
            control_signal("interrupt", scope="agent", room_id=None)
        )

        await await_cancelled(t1)
        await await_cancelled(t2)

    async def test_bad_scope_with_no_room_id_is_noop(self) -> None:
        runner, fwd, _ = build_runner()

        await runner._control.handle(
            AgentControlPayload(mode="interrupt", scope="room", agent_id="agent-1")
        )

        assert isinstance(fwd, FakeForwarder)
        assert fwd.forwarded == []
