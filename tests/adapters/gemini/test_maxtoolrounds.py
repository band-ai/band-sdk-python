"""MaxToolRounds behavior for Gemini."""

from __future__ import annotations

import pytest

from band.adapters.gemini import GeminiAdapterConfig
from band.core.exceptions import ToolRoundsExceededError
from band.runtime.tools import BandTool
from band.testing import failure_reports
from tests.adapters.genaikit import PlatformSchemaFakeTools, tool_call
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input


class TestMaxToolRounds:
    @pytest.mark.asyncio
    async def test_model_still_calling_tools_fails_at_the_round_cap(
        self, scripted_adapter
    ):
        adapter = await scripted_adapter(
            *(tool_call(BandTool.LOOKUP_PEERS, {}) for _ in range(3)),
            config=GeminiAdapterConfig(max_tool_rounds=2),
        )
        tools = PlatformSchemaFakeTools()

        with pytest.raises(ToolRoundsExceededError) as raised:
            await adapter.on_event(turn_input(tools))

        error = raised.value
        assert isinstance(error, RuntimeError)
        assert adapter.client.aio.models.generate_content.await_count == 2
        assert (error.max_rounds, error.room_id, error.tool_sequence) == (
            2,
            ROOM_ID,
            [[BandTool.LOOKUP_PEERS]] * 2,
        )
        assert failure_reports(tools) == [("gemini", str(error))]
