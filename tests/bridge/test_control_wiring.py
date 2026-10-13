"""Bridge control wiring."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tests.bridge.kit import (
    build_runner,
    control_signal,
)

pytestmark = pytest.mark.asyncio


class TestControlWiring:
    async def test_connect_wires_on_control_to_handler(self) -> None:
        runner, _, link = build_runner()
        link.__anext__ = AsyncMock(side_effect=StopAsyncIteration())

        await runner._connect_and_consume()

        assert link.on_control == runner._control.handle

    async def test_connect_handles_control_signal_arriving_during_subscription(
        self,
    ) -> None:
        runner, _, link = build_runner()
        handler = AsyncMock()
        runner._control.handle = handler
        control = control_signal("interrupt", scope="room", room_id="r1")

        async def connect() -> None:
            # connect() joins the control channel, so a push can land before it
            # returns — the hook has to be installed by now, not after.
            assert link.on_control == handler
            await link.on_control(control)

        link.connect.side_effect = connect
        link.__anext__ = AsyncMock(side_effect=StopAsyncIteration())

        await runner._connect_and_consume()

        handler.assert_awaited_once_with(control)
