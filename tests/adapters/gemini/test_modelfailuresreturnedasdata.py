"""ModelFailuresReturnedAsData behavior for Gemini."""

from __future__ import annotations

import pytest
from google.genai import types

from band.runtime.tools import BandTool
from band.testing import (
    reported_failures,
)
from tests.adapters.genaikit import (
    ModelFailureCases,
    PlatformSchemaFakeTools,
    text_reply,
    tool_call,
)
from tests.framework_conformance.turnprobes import (
    turn_input,
)


class TestModelFailuresReturnedAsData(ModelFailureCases):
    PROVIDER = "gemini"

    @pytest.mark.asyncio
    async def test_reply_cut_off_at_max_tokens_completes(self, scripted_adapter):
        adapter = await scripted_adapter(
            tool_call(BandTool.SEND_MESSAGE, {"content": "Hi", "mentions": ["Alice"]}),
            text_reply("Anything else", finish=types.FinishReason.MAX_TOKENS),
        )
        tools = PlatformSchemaFakeTools()

        await adapter.on_event(turn_input(tools))

        assert reported_failures(tools) == []
