"""Failed calls and adapter narration leave the reply owed."""

from __future__ import annotations

import pytest

from band.runtime.tools import AgentTools, BandTool


class TestCallsThatRecordNothing:
    async def test_a_raising_call_records_nothing(self, tools: AgentTools) -> None:
        with pytest.raises(ValueError, match="Unknown participant"):
            await tools.send_message("hi", mentions=["@user1"])

        assert not tools.turn.replied

    async def test_a_blank_send_records_nothing(self, tools: AgentTools) -> None:
        assert await tools.send_message("  ", mentions=["@alice"]) is None

        assert not tools.turn.replied

    async def test_an_adapter_notice_is_never_the_reply(
        self, tools: AgentTools
    ) -> None:
        await tools.send_notice("Approve this tool call?", mentions=["@alice"])

        assert not tools.turn.complete

    async def test_narration_leaves_the_reply_owed(self, tools: AgentTools) -> None:
        """The nightly loss: a send to an unknown handle fails, the model
        narrates through band_send_event, then stops."""
        with pytest.raises(ValueError, match="Unknown participant"):
            await tools.send_message("hi", mentions=["@user1"])
        await tools.execute_tool_call(
            BandTool.SEND_EVENT, {"content": "retrying", "message_type": "thought"}
        )

        assert not tools.turn.complete
