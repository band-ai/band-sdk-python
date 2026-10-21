from __future__ import annotations

import json
from typing import Any

import pytest

from band.integrations.crewai.tools import (
    CrewAIToolContext,
    NoopReporter,
    build_band_crewai_tools,
)
from band.testing import FakeAgentTools
from band.testing.activity import ActivityRecorder, shown_tool
from tests.integrations.activitykit import LookupInput

pytest.importorskip("crewai")


class TestCrewAIActivity:
    @pytest.mark.parametrize("failed", [False, True])
    def test_real_base_tool_reports_without_narration_and_ends_on_error(
        self, failed: bool
    ) -> None:
        recorder = ActivityRecorder()
        tools = recorder.observing(FakeAgentTools())
        shown_while_running: list[dict[str, Any]] = []
        received: list[str] = []

        async def lookup(args: LookupInput) -> str:
            shown_while_running.extend(recorder.events)
            received.append(args.note)
            if failed:
                raise ValueError("private-error")
            return "private-result"

        built = build_band_crewai_tools(
            get_context=lambda: CrewAIToolContext(room_id="room", tools=tools),
            reporter=NoopReporter(),
            custom_tools=[(LookupInput, lookup)],
        )
        lookup_tool = next(tool for tool in built if tool.name == "lookup")
        result = json.loads(lookup_tool.run(note="private-argument"))
        assert result["status"] == ("error" if failed else "success")
        assert received == ["private-argument"]
        assert shown_while_running == [shown_tool("lookup")]
        recorder.assert_completed_tool("lookup")
        assert not tools.events_sent
