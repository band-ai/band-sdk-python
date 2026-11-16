"""A Copilot sub-agent's decline ends only its own calls; the root's ends all."""

from __future__ import annotations

from typing import Any

from band.adapters.copilot_sdk import _COPILOT_SDK_AVAILABLE
from band.integrations.copilot_sdk import ASK_USER_ROOM, render_room_question
from band.integrations.copilot_sdk.room_ask_user import question_delivered_answer
from tests.adapters.copilot_sdk.declinekit import (
    DECLINE,
    PostingTools,
    refused,
    replied,
)
from tests.adapters.copilot_sdk.fakes import (
    QUESTION,
    FakeCopilotClient,
    FakeCopilotSession,
    call_tool,
    make_started_adapter,
    requires_copilot_sdk,
    run_event,
)
from tests.framework_conformance.outcomeprobes import ANSWER, REPLY
from tests.runtime.customtools.kit import SkipInput, skip

pytestmark = requires_copilot_sdk

if _COPILOT_SDK_AVAILABLE:
    from copilot import ToolResult

SUBAGENT, SIBLING = "subagent-1", "subagent-2"


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

        assert results == [replied()]
        assert tools.chat == [ANSWER]

    async def test_a_declined_sub_agent_is_refused_while_its_sibling_posts(
        self,
    ) -> None:
        results: list[ToolResult] = []

        async def sub_agent_declines_then_both_reply(
            session: FakeCopilotSession,
        ) -> None:
            await call_tool(session, DECLINE, agent_id=SUBAGENT)
            results.append(await call_tool(session, REPLY, agent_id=SUBAGENT))
            results.append(await call_tool(session, REPLY, agent_id=SIBLING))

        client = FakeCopilotClient(turn_events=[sub_agent_declines_then_both_reply])
        adapter = await make_started_adapter(
            client, additional_tools=[(SkipInput, skip)]
        )
        tools = PostingTools()

        await run_event(adapter, tools)

        assert results == [refused(), replied()]
        assert tools.chat == [ANSWER]

    async def test_the_roots_question_posts_after_a_sub_agent_declines(
        self,
    ) -> None:
        answers: list[Any] = []

        async def sub_agent_declines_then_root_asks(
            session: FakeCopilotSession,
        ) -> None:
            await call_tool(session, DECLINE, agent_id=SUBAGENT)
            handler = session.kwargs["on_user_input_request"]
            answers.append(await handler(QUESTION, {"session_id": session.session_id}))

        client = FakeCopilotClient(turn_events=[sub_agent_declines_then_root_asks])
        adapter = await make_started_adapter(
            client, ask_user=ASK_USER_ROOM, additional_tools=[(SkipInput, skip)]
        )
        tools = PostingTools()

        await run_event(adapter, tools)

        rendered = render_room_question(QUESTION)
        assert answers == [question_delivered_answer(rendered)]
        assert tools.chat == [rendered]

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
