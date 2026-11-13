"""A Letta turn with no final text logs why Letta stopped, loudly only when it is judged missing."""

from __future__ import annotations

import logging

import pytest

from band.core.protocols import TurnResultAlreadyReported
from band.runtime.tools import BandTool
from band.testing import MISSING_REPLY_FAILURE, failure_reports
from tests.adapters.lettakit import (
    make_letta_message,
    make_letta_response,
    ready_letta_adapter,
    scripted_letta_turn,
)
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input, turn_tools

_LOGGER = "band.adapters.letta"


def _no_text_logs(caplog: pytest.LogCaptureFixture) -> list[tuple[int, str]]:
    return [
        (record.levelno, record.getMessage())
        for record in caplog.records
        if record.name == _LOGGER and "produced no reply or text" in record.getMessage()
    ]


class TestEmptyTurn:
    @pytest.mark.asyncio
    async def test_an_empty_answer_is_reported_missing_with_letta_stop_reason(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        adapter, mock_client = ready_letta_adapter(room_id=ROOM_ID)
        # What Letta streams when the model's final answer is empty text.
        mock_client.agents.messages.create.return_value = make_letta_response(
            make_letta_message("ping"),
            make_letta_message("stop_reason", stop_reason="end_turn"),
        )

        tools = turn_tools()
        with (
            caplog.at_level(logging.DEBUG, logger=_LOGGER),
            pytest.raises(TurnResultAlreadyReported),
        ):
            await adapter.on_event(turn_input(tools))

        assert failure_reports(tools) == [MISSING_REPLY_FAILURE]
        assert _no_text_logs(caplog) == [
            (
                logging.WARNING,
                f"Room {ROOM_ID}: Letta turn produced no reply or text (stop_reason=end_turn)",
            )
        ]

    @pytest.mark.asyncio
    async def test_a_turn_that_did_work_without_text_logs_it_quietly(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        adapter, mock_client = ready_letta_adapter(room_id=ROOM_ID)
        mock_client.agents.messages.create.side_effect = scripted_letta_turn(
            adapter, room_id=ROOM_ID, tool_calls=[(BandTool.CREATE_CHATROOM, {})]
        )

        tools = turn_tools()
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            await adapter.on_event(turn_input(tools))

        assert failure_reports(tools) == []
        assert [level for level, _ in _no_text_logs(caplog)] == [logging.DEBUG]
