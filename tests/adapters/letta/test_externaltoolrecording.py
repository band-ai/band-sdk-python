from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

import pytest
from letta_client.types.tool_return_message import ToolReturnMessage

from band.adapters.letta import (
    LettaAdapterConfig,
    LettaMCPConfig,
)
from band.converters.letta import LettaSessionState
from band.core.protocols import (
    TurnResultAlreadyReported,
)
from band.runtime.tools import BandTool
from band.testing import (
    MISSING_REPLY_FAILURE,
    FakeAgentTools,
    failure_reports,
)
from tests.adapters.letta.kit import sent_contents
from tests.adapters.lettakit import (
    call_id,
    make_assistant_message,
    make_letta_response,
    make_parallel_tool_messages,
    make_platform_message,
    make_tool_call_message,
    make_tool_return_message,
    ready_letta_adapter,
)
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input, turn_tools


class TestExternalToolRecording:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("mcp_mode", "status", "replied"),
        [
            pytest.param("external", "success", True, id="external-success"),
            pytest.param("external", "error", False, id="external-error"),
            # Self-hosted calls run on the room's own tools, which record them.
            pytest.param("self_host", "success", False, id="self-host"),
        ],
    )
    async def test_streamed_tool_return_records_only_for_external_band_mcp(
        self,
        mcp_mode: Literal["self_host", "external"],
        status: Literal["success", "error"],
        replied: bool,
    ) -> None:
        adapter, mock_client = ready_letta_adapter(
            LettaAdapterConfig(mcp=LettaMCPConfig(mode=mcp_mode))
        )
        mock_client.agents.messages.create.return_value = make_letta_response(
            make_tool_call_message(BandTool.SEND_MESSAGE),
            make_tool_return_message(BandTool.SEND_MESSAGE, status=status),
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

        assert tools.turn.replied is replied

    @pytest.mark.asyncio
    async def test_ungrouped_tool_return_is_matched_to_its_call(self) -> None:
        """A server that sends no ``tool_returns`` puts the one result on the
        message itself."""
        adapter, mock_client = ready_letta_adapter(
            LettaAdapterConfig(mcp=LettaMCPConfig(mode="external"))
        )
        mock_client.agents.messages.create.return_value = make_letta_response(
            make_tool_call_message(BandTool.SEND_MESSAGE),
            ToolReturnMessage(
                id="message-return",
                date=datetime.now(UTC),
                message_type="tool_return_message",
                status="success",
                tool_call_id=call_id(BandTool.SEND_MESSAGE),
                tool_return="result",
            ),
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

        assert tools.turn.replied

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("effect_tool", "replied"),
        [
            pytest.param(BandTool.ADD_PARTICIPANT, False, id="act"),
            pytest.param(BandTool.SEND_MESSAGE, True, id="reply"),
            pytest.param(BandTool.NO_REPLY, True, id="decline"),
        ],
    )
    @pytest.mark.parametrize("reverse_calls", [False, True])
    async def test_parallel_success_completes_turn_independent_of_order(
        self,
        effect_tool: str,
        replied: bool,
        reverse_calls: bool,
    ) -> None:
        adapter, mock_client = ready_letta_adapter(
            LettaAdapterConfig(mcp=LettaMCPConfig(mode="external"), auto_relay=False),
            room_id=ROOM_ID,
        )
        results: list[tuple[str, Literal["success", "error"]]] = [
            (BandTool.GET_PARTICIPANTS, "success"),
            (effect_tool, "success"),
        ]
        if reverse_calls:
            results.reverse()
        mock_client.agents.messages.create.return_value = make_letta_response(
            *make_parallel_tool_messages(results, reverse_returns=False)
        )
        tools = turn_tools()

        await adapter.on_event(turn_input(tools))

        assert tools.turn.complete
        assert tools.turn.replied is replied
        assert tools.messages_sent == []
        assert failure_reports(tools) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "effect_tool",
        [BandTool.ADD_PARTICIPANT, BandTool.SEND_MESSAGE, BandTool.NO_REPLY],
    )
    @pytest.mark.parametrize("reverse_returns", [False, True])
    async def test_parallel_failed_effect_does_not_complete_turn(
        self, effect_tool: str, reverse_returns: bool
    ) -> None:
        adapter, mock_client = ready_letta_adapter(
            LettaAdapterConfig(mcp=LettaMCPConfig(mode="external"), auto_relay=False),
            room_id=ROOM_ID,
        )
        mock_client.agents.messages.create.return_value = make_letta_response(
            *make_parallel_tool_messages(
                [
                    (BandTool.GET_PARTICIPANTS, "success"),
                    (effect_tool, "error"),
                ],
                reverse_returns=reverse_returns,
            )
        )
        tools = turn_tools()

        with pytest.raises(TurnResultAlreadyReported):
            await adapter.on_event(turn_input(tools))

        assert not tools.turn.replied
        assert tools.messages_sent == []
        assert failure_reports(tools) == [MISSING_REPLY_FAILURE]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("effect_tool", [BandTool.SEND_MESSAGE, BandTool.NO_REPLY])
    @pytest.mark.parametrize("reverse_returns", [False, True])
    async def test_parallel_failed_reply_does_not_suppress_fallback(
        self, effect_tool: str, reverse_returns: bool
    ) -> None:
        adapter, mock_client = ready_letta_adapter(
            LettaAdapterConfig(mcp=LettaMCPConfig(mode="external")),
            room_id=ROOM_ID,
        )
        mock_client.agents.messages.create.return_value = make_letta_response(
            *make_parallel_tool_messages(
                [(effect_tool, "error"), (BandTool.GET_PARTICIPANTS, "success")],
                reverse_returns=reverse_returns,
            ),
            make_assistant_message("Fallback reply"),
        )
        tools = turn_tools()

        await adapter.on_event(turn_input(tools))

        assert sent_contents(tools) == ["Fallback reply"]
        assert failure_reports(tools) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("reverse_returns", [False, True])
    async def test_parallel_results_match_call_ids_not_positions(
        self, reverse_returns: bool
    ) -> None:
        adapter, mock_client = ready_letta_adapter(
            LettaAdapterConfig(mcp=LettaMCPConfig(mode="external"), auto_relay=False),
            room_id=ROOM_ID,
        )
        mock_client.agents.messages.create.return_value = make_letta_response(
            *make_parallel_tool_messages(
                [
                    (BandTool.SEND_MESSAGE, "error"),
                    (BandTool.ADD_PARTICIPANT, "success"),
                ],
                reverse_returns=reverse_returns,
            )
        )
        tools = turn_tools()

        await adapter.on_event(turn_input(tools))

        assert tools.turn.complete
        assert not tools.turn.replied
        assert tools.messages_sent == []
        assert failure_reports(tools) == []
