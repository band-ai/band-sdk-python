"""CustomIdentity at the released CrewAI tool boundary."""

from __future__ import annotations

import json

from crewai.tools import BaseTool
from typing import Any

from band.integrations.crewai.tools import (
    CrewAIToolContext,
    NoopReporter,
    build_band_crewai_tools,
)
from band.testing.fake_tools import FakeAgentTools
from tests.adapters.crewaiturnend.kit import DeclineInput


class TestCustomIdentity:
    def test_a_custom_platform_name_does_not_inherit_its_decline_effect(self) -> None:
        tools = FakeAgentTools()
        context = CrewAIToolContext("room", tools)
        input_model = type("Band_No_ReplyInput", (DeclineInput,), {})

        async def observe(args: Any) -> Any:
            return "observed"

        wrapped = build_band_crewai_tools(
            get_context=lambda: context,
            reporter=NoopReporter(),
            custom_tools=[(input_model, observe)],
        )[-1]

        assert isinstance(wrapped, BaseTool)
        assert wrapped.name == "band_no_reply"
        assert not wrapped.result_as_answer
        assert wrapped._run(reason="observe") == json.dumps(
            {"status": "success", "result": "observed"}
        )
        assert not tools.turn.complete
