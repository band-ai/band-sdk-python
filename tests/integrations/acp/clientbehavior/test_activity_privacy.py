"""ACP activity never exposes a provider's invocation title."""

from __future__ import annotations

from typing import Any

import pytest
from acp.helpers import start_tool_call, update_tool_call

from band.integrations.acp.types import ToolStatus
from band.runtime.activity import UNKNOWN_ACTIVITY_TOOL_NAME
from band.runtime.tools import BAND_MCP_SERVER_NAME
from band.runtime.tools.types import BandTool
from band.testing.activity import SHOWN_REASONING, ActivityRecorder, shown_tool
from tests.integrations.acp.acp_toolkit import (
    FakeACPAgent,
    acp_adapter,
    fake_agent_config,
)
from tests.integrations.acp.clientbehavior.kit import band_activity_call, observed_tools

PRIVATE_PATH = "/private/sentinel-token.txt"


class TestActivityPrivacy:
    @pytest.mark.parametrize(
        "raw_input",
        [
            {"path": PRIVATE_PATH},
            {"server": "foreign", "tool": BandTool.GET_PARTICIPANTS, "arguments": {}},
            {
                "server": BAND_MCP_SERVER_NAME,
                "tool": "private-name-sentinel",
                "arguments": {},
            },
        ],
        ids=["native-title", "foreign-server", "unregistered-name"],
    )
    async def test_private_title_is_not_sent_as_activity(
        self, fake_agent: FakeACPAgent, raw_input: dict[str, Any]
    ) -> None:
        @fake_agent.on_prompt
        async def turn(agent: FakeACPAgent, sid: str) -> None:
            await agent.emit(
                sid,
                start_tool_call(
                    "call-1",
                    f"Read {PRIVATE_PATH}",
                    raw_input=raw_input,
                    status=ToolStatus.IN_PROGRESS.value,
                ),
            )
            await agent.emit(
                sid, update_tool_call("call-1", status=ToolStatus.COMPLETED.value)
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

    @pytest.mark.parametrize(
        ("inject_band_tools", "metadata"),
        [(False, None), (True, {"mcp": None})],
        ids=["injection-disabled", "rejected-metadata-with-valid-raw-fallback"],
    )
    async def test_untrusted_registered_identity_is_not_sent_as_activity(
        self,
        fake_agent: FakeACPAgent,
        inject_band_tools: bool,
        metadata: dict[str, object] | None,
    ) -> None:
        @fake_agent.on_prompt
        async def turn(agent: FakeACPAgent, sid: str) -> None:
            call = band_activity_call(
                "call-1", BandTool.GET_PARTICIPANTS, ToolStatus.IN_PROGRESS
            ).model_copy(update={"field_meta": metadata})
            await agent.emit(sid, call)
            await agent.emit(
                sid, update_tool_call("call-1", status=ToolStatus.COMPLETED.value)
            )

        recorder = ActivityRecorder()
        async with acp_adapter(
            fake_agent, fake_agent_config(inject_band_tools=inject_band_tools), emit=()
        ) as session:
            await session.send("work", tools=observed_tools(recorder))
        assert recorder.events == [
            shown_tool(UNKNOWN_ACTIVITY_TOOL_NAME),
            SHOWN_REASONING,
        ]
