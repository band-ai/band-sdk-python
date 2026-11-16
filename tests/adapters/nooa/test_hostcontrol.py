"""NOOA's SDK tool namespace preserves host control exceptions."""

from __future__ import annotations

import pytest

from band.adapters.nooa.tools import custom_tool_spec, room_tools_class
from band.core.exceptions import BandError
from band.testing import FakeAgentTools
from tests.adapters.nooa.kit import OrderInput
from tests.hostcontrol import host_control_errors


class TestHostControl:
    @pytest.mark.parametrize("control", host_control_errors(), ids=type)
    async def test_custom_tool_preserves_host_control_at_the_sdk_boundary(
        self, control: BandError
    ) -> None:
        async def order(args: OrderInput) -> str:
            raise control

        room = room_tools_class([custom_tool_spec((OrderInput, order))])(
            emit_tool_calls=True
        )
        tools = FakeAgentTools()
        async with room.bound_to(tools):
            with pytest.raises(type(control)) as caught:
                await room.order(arguments={"order_id": "A-1"})

        assert caught.value is control
        assert not tools.turn.complete
