from __future__ import annotations

from typing import Any

import pytest

from band.adapters.letta import (
    LettaAdapterConfig,
)
from band.converters.letta import LettaSessionState
from band.core.protocols import (
    TurnResultAlreadyReported,
)
from band.core.types import Capability
from band.runtime.tools import BandTool
from band.testing import (
    MISSING_REPLY_FAILURE,
    FakeAgentTools,
    failure_reports,
    reported_failures,
)
from tests.adapters.letta.kit import REPLY_SETTLING_CALLS, sent_contents
from tests.adapters.lettakit import (
    make_platform_message,
    ready_letta_adapter,
    scripted_letta_turn,
)
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input, turn_tools


class TestAutoRelayDisabled:
    @pytest.mark.asyncio
    async def test_disabled_relay_drops_the_text_and_the_verdict_reports_it(
        self,
    ) -> None:
        adapter, mock_client = ready_letta_adapter(
            LettaAdapterConfig(auto_relay=False), room_id=ROOM_ID
        )
        mock_client.agents.messages.create.side_effect = scripted_letta_turn(
            adapter, room_id=ROOM_ID, tool_calls=[], final_text="I'll help you!"
        )

        tools = turn_tools()
        with pytest.raises(TurnResultAlreadyReported):
            await adapter.on_event(turn_input(tools))

        assert tools.messages_sent == []
        assert failure_reports(tools) == [MISSING_REPLY_FAILURE]

    @pytest.mark.asyncio
    async def test_disabled_relay_completes_a_turn_that_did_real_work(self) -> None:
        adapter, mock_client = ready_letta_adapter(
            LettaAdapterConfig(auto_relay=False), room_id=ROOM_ID
        )
        mock_client.agents.messages.create.side_effect = scripted_letta_turn(
            adapter,
            room_id=ROOM_ID,
            tool_calls=[(BandTool.CREATE_CHATROOM, {})],
            final_text="Created it.",
        )

        tools = turn_tools()
        await adapter.on_event(turn_input(tools))

        assert tools.messages_sent == []
        assert failure_reports(tools) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("reply_tool", "arguments"), REPLY_SETTLING_CALLS)
    async def test_disabled_relay_quiet_when_a_reply_tool_settled_the_turn(
        self, reply_tool: str, arguments: dict[str, Any]
    ) -> None:
        adapter, mock_client = ready_letta_adapter(
            LettaAdapterConfig(auto_relay=False), capabilities={Capability.FILES}
        )
        mock_client.agents.messages.create.side_effect = scripted_letta_turn(
            adapter,
            room_id="room-1",
            tool_calls=[(reply_tool, arguments)],
            final_text="Done!",
        )

        tools = FakeAgentTools()
        await adapter.on_message(
            make_platform_message(),
            tools,
            LettaSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        assert "Done!" not in sent_contents(tools)
        assert not reported_failures(tools)
