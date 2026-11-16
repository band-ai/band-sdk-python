"""A Copilot sub-agent's decline ends only its own calls; the root's ends all."""

from __future__ import annotations

import json

from band.adapters.copilot_sdk import _COPILOT_SDK_AVAILABLE
from tests.adapters.copilot_sdk.declinekit import (
    DECLINE,
    PostingTools,
    SkipInput,
    call_tool,
    refused,
    skip,
)
from tests.adapters.copilot_sdk.fakes import (
    FakeCopilotClient,
    FakeCopilotSession,
    make_started_adapter,
    requires_copilot_sdk,
    run_event,
)
from tests.framework_conformance.outcomeprobes import ANSWER, REPLY, REPLY_SENT

pytestmark = requires_copilot_sdk

if _COPILOT_SDK_AVAILABLE:
    from copilot import ToolResult

SUBAGENT = "subagent-1"


class TestSubagentDecline:
    async def test_the_root_still_replies_after_a_sub_agent_declines(self) -> None:
        results: list[ToolResult] = []

        async def sub_agent_declines_then_root_replies(
            session: FakeCopilotSession,
        ) -> None:
            await call_tool(session, DECLINE, agent_id=SUBAGENT)
            results.append(await call_tool(session, REPLY))

        client = FakeCopilotClient(turn_events=[sub_agent_declines_then_root_replies])
        adapter = await make_started_adapter(
            client, additional_tools=[(SkipInput, skip)]
        )
        tools = PostingTools()

        await run_event(adapter, tools)

        assert results == [
            ToolResult(
                text_result_for_llm=json.dumps(REPLY_SENT), result_type="success"
            )
        ]
        assert tools.chat == [ANSWER]

    async def test_a_sub_agent_is_refused_once_the_root_declines(self) -> None:
        results: list[ToolResult] = []

        async def root_declines_then_sub_agent_replies(
            session: FakeCopilotSession,
        ) -> None:
            await call_tool(session, DECLINE)
            results.append(await call_tool(session, REPLY, agent_id=SUBAGENT))

        client = FakeCopilotClient(turn_events=[root_declines_then_sub_agent_replies])
        adapter = await make_started_adapter(
            client, additional_tools=[(SkipInput, skip)]
        )
        tools = PostingTools()

        await run_event(adapter, tools)

        assert results == [refused()]
        assert tools.chat == []
