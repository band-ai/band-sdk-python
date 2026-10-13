"""A ``self.room`` call belongs to the turn that made it."""

from __future__ import annotations

import pytest

from band.adapters.nooa import NooaAdapter
from band.adapters.nooa.tools import platform_tool_spec, room_tools_class
from band.core.exceptions import BandToolError
from band.core.types import Emit
from band.runtime.tools import TOOL_DEFINITIONS, BandTool
from band.testing import FakeAgentTools
from tests.adapters.nooa.kit import (
    Gate,
    LeftoverReply,
    SlowInput,
    dispatched,
    room_message,
    run_turn,
    tool_results,
)


class TestTurnOwnership:
    async def test_a_tool_called_outside_a_turn_is_refused(self) -> None:
        spec = platform_tool_spec(TOOL_DEFINITIONS[BandTool.NO_REPLY])
        room = room_tools_class([spec])(emit_tool_calls=False)

        with pytest.raises(BandToolError, match="only available while"):
            await getattr(room, BandTool.NO_REPLY)()

    async def test_work_left_running_by_one_turn_cannot_act_in_the_next(self) -> None:
        leftover = LeftoverReply()
        adapter = NooaAdapter(agent=leftover.room_agent())
        await adapter.on_started("Agent", "A test agent")
        second_turn = FakeAgentTools()

        await run_turn(adapter, FakeAgentTools(), message=room_message("first"))
        await run_turn(
            adapter,
            second_turn,
            message=room_message("second"),
            is_session_bootstrap=False,
        )

        assert leftover.outcome == (
            "BandToolError: band_send_message is only available while a Band "
            "room message is being handled"
        )
        assert dispatched(second_turn) == [BandTool.NO_REPLY]

    async def test_a_tool_call_still_running_when_the_turn_ends_is_cancelled(
        self, tools: FakeAgentTools
    ) -> None:
        gate = Gate()
        adapter = NooaAdapter(
            agent=gate.room_agent(),
            additional_tools=[(SlowInput, gate.handler)],
            emit={Emit.TOOL_CALLS},
        )
        await adapter.on_started("Agent", "A test agent")

        await run_turn(adapter, tools)
        gate.opened.set()

        assert gate.cancelled
        assert not gate.committed
        assert {
            result["name"]: result["is_error"] for result in tool_results(tools)
        } == {"slow": True, BandTool.NO_REPLY: False}
