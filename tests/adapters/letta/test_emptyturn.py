"""A Letta turn whose model answered with nothing is reported missing and logs why Letta stopped."""

from __future__ import annotations

import logging

import pytest

from band.core.protocols import TurnResultAlreadyReported
from band.testing import MISSING_REPLY_FAILURE, failure_reports
from tests.adapters.lettakit import (
    make_letta_message,
    make_letta_response,
    ready_letta_adapter,
)
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input, turn_tools


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
            caplog.at_level(logging.WARNING, logger="band.adapters.letta"),
            pytest.raises(TurnResultAlreadyReported),
        ):
            await adapter.on_event(turn_input(tools))

        assert failure_reports(tools) == [MISSING_REPLY_FAILURE]
        assert (
            logging.WARNING,
            f"Room {ROOM_ID}: Letta turn produced no reply or text (stop_reason=end_turn)",
        ) in [
            (record.levelno, record.getMessage())
            for record in caplog.records
            if record.name == "band.adapters.letta"
        ]
