"""Native result serialization errors remain outside custom tool execution."""

from __future__ import annotations

import pytest
from pydantic import RootModel

from band.adapters.nooa.tools import custom_tool_spec, room_tools_class
from band.testing import FakeAgentTools
from tests.adapters.nooa.kit import OrderInput


class TestResultSerialization:
    async def test_serialization_failure_is_not_reclassified_as_a_handler_failure(
        self,
    ) -> None:
        cyclic: list[object] = []
        cyclic.append(cyclic)
        result = RootModel[list[object]](root=cyclic)

        async def order(args: OrderInput) -> RootModel[list[object]]:
            return result

        room = room_tools_class([custom_tool_spec((OrderInput, order))])(
            emit_tool_calls=False
        )
        async with room.bound_to(FakeAgentTools()):
            with pytest.raises(ValueError, match="Circular reference"):
                await room.order(arguments={"order_id": "A-1"})
