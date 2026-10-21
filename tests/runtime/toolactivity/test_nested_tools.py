from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import MagicMock

import pytest

from band.runtime.tools import AgentTools
from band.runtime.tools.types import BandTool
from band.testing.activity import SHOWN_REASONING, ActivityRecorder, shown_tool


class TestNestedToolActivity:
    """A Band tool that calls other Band tools shows only itself."""

    @pytest.mark.parametrize(
        ("name", "call", "rest_method"),
        [
            (
                BandTool.ADD_PARTICIPANT,
                lambda tools: tools.add_participant("Agent Two"),
                "add_agent_chat_participant",
            ),
            (
                BandTool.REMOVE_PARTICIPANT,
                lambda tools: tools.remove_participant("User One"),
                "remove_agent_chat_participant",
            ),
        ],
        ids=["add", "remove"],
    )
    async def test_the_change_runs_under_the_called_tool(
        self,
        tools: AgentTools,
        recorder: ActivityRecorder,
        mock_rest_client: MagicMock,
        name: BandTool,
        call: Callable[[AgentTools], Awaitable[Any]],
        rest_method: str,
    ) -> None:
        participants = mock_rest_client.agent_api_participants
        change = getattr(participants, rest_method)
        shown_during_change: list[dict[str, Any]] = []
        answer = change.return_value

        async def record_change(*_args: Any, **_kwargs: Any) -> Any:
            shown_during_change.extend(recorder.events)
            return answer

        change.side_effect = record_change
        await call(tools)

        assert shown_during_change == [shown_tool(name)]
        assert recorder.events == [shown_tool(name), SHOWN_REASONING]
