"""ACP activity never exposes a provider's invocation title."""

from __future__ import annotations

from typing import Any

import pytest
from acp.helpers import start_tool_call, update_tool_call

from band.integrations.acp.types import ToolStatus
from band.runtime.activity import UNKNOWN_ACTIVITY_TOOL_NAME
from band.testing.activity import SHOWN_REASONING, ActivityRecorder, shown_tool
from tests.integrations.acp.acp_toolkit import (
    FakeACPAgent,
    acp_adapter,
    fake_agent_config,
)
from tests.integrations.acp.clientbehavior.kit import observed_tools


class TestActivityPrivacy:
    @pytest.mark.parametrize(
        "raw_input",
        [
            {"path": "/private/sentinel-token.txt"},
            {"server": "foreign", "tool": "band_get_participants", "arguments": {}},
            {"server": "band", "tool": "private-name-sentinel", "arguments": {}},
        ],
        ids=["native-title", "foreign-server", "unregistered-name"],
    )
    async def test_private_title_is_not_sent_as_activity(
        self, fake_agent: FakeACPAgent, raw_input: dict[str, Any]
    ) -> None:
        private_path = "/private/sentinel-token.txt"

        @fake_agent.on_prompt
        async def turn(agent: FakeACPAgent, sid: str) -> None:
            await agent.emit(
                sid,
                start_tool_call(
                    "call-1",
                    f"Read {private_path}",
                    raw_input=raw_input,
                    status=ToolStatus.IN_PROGRESS,
                ),
            )
            await agent.emit(
                sid, update_tool_call("call-1", status=ToolStatus.COMPLETED)
            )

        recorder = ActivityRecorder()
        async with acp_adapter(
            fake_agent, fake_agent_config(inject_band_tools=True), emit=()
        ) as session:
            await session.send("Read the file", tools=observed_tools(recorder))
        assert recorder.events == [
            shown_tool(UNKNOWN_ACTIVITY_TOOL_NAME),
            SHOWN_REASONING,
        ]
