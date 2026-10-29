"""An ACP agent's tool calls show as the turn's activity, whatever the emit set."""

from __future__ import annotations

import pytest
from acp.helpers import start_tool_call, update_tool_call

from band.core.types import Emit
from band.integrations.acp.types import ToolStatus
from band.testing.activity import SHOWN_REASONING, ActivityRecorder, shown_tool
from tests.integrations.acp.acp_toolkit import FakeACPAgent, acp_adapter
from tests.integrations.acp.acp_toolkit.agent import (
    CURSOR_MCP_PLACEHOLDER_TITLE,
    cursor_mcp_naming,
    cursor_mcp_placeholder,
)
from tests.integrations.acp.clientbehavior.kit import observed_tools


class TestToolActivity:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("emit", [(), Emit.TOOL_CALLS], ids=["silent", "narrated"])
    async def test_tool_call_shows_under_its_narrated_name_whatever_the_emit_set(
        self, fake_agent: FakeACPAgent, emit: Emit | tuple[()]
    ) -> None:
        """Emit gates only the room's tool_call/tool_result events; the
        indicator still shows the call, under the canonical name narration uses."""
        fake_agent.will_call_tool(
            "tc-1", "band-band_create_chatroom", raw_input={"title": "x"}, result="ok"
        )
        recorder = ActivityRecorder()
        async with acp_adapter(fake_agent, emit=emit) as session:
            await session.send("make a room", tools=observed_tools(recorder))

        assert recorder.events == [shown_tool("band_create_chatroom"), SHOWN_REASONING]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [ToolStatus.COMPLETED, ToolStatus.FAILED])
    async def test_terminal_status_ends_the_call_activity(
        self, fake_agent: FakeACPAgent, status: ToolStatus
    ) -> None:
        fake_agent.will_call_tool("tc-1", "get_weather", result="72F", status=status)
        recorder = ActivityRecorder()
        async with acp_adapter(fake_agent) as session:
            await session.send("weather?", tools=observed_tools(recorder))

        assert recorder.events == [shown_tool("get_weather"), SHOWN_REASONING]

    @pytest.mark.asyncio
    async def test_call_first_reported_finished_never_shows_as_running(
        self, fake_agent: FakeACPAgent
    ) -> None:
        """An agent may announce a call only once it has finished; no later
        update ends it, so showing it would hold the indicator for the turn."""

        @fake_agent.on_prompt
        async def turn(agent: FakeACPAgent, sid: str) -> None:
            await agent.emit(
                sid, start_tool_call("tc-done", "write", status=ToolStatus.COMPLETED)
            )
            await agent.emit(sid, start_tool_call("tc-read", "read_file"))
            await agent.emit(
                sid, update_tool_call("tc-read", status=ToolStatus.COMPLETED)
            )

        recorder = ActivityRecorder()
        async with acp_adapter(fake_agent) as session:
            await session.send("write it", tools=observed_tools(recorder))

        assert recorder.events == [shown_tool("read_file"), SHOWN_REASONING]

    @pytest.mark.asyncio
    async def test_call_named_late_is_renamed_while_it_runs(
        self, fake_agent: FakeACPAgent
    ) -> None:
        """Cursor opens an MCP call as a nameless placeholder and names it in a
        later update, which renames the running call to its narrated name."""
        fake_agent.will_call_mcp_tool_named_late(
            "tc-1", "band", "band_list_contacts", arguments={}, result="[]"
        )
        recorder = ActivityRecorder()
        async with acp_adapter(fake_agent) as session:
            reply = await session.send("contacts?", tools=observed_tools(recorder))

        assert reply.tool_call_names == ["band_list_contacts"]
        assert recorder.events == [
            shown_tool(CURSOR_MCP_PLACEHOLDER_TITLE),
            shown_tool("band_list_contacts"),
            SHOWN_REASONING,
        ]

    @pytest.mark.asyncio
    async def test_call_keeps_the_name_it_was_posted_under(
        self, fake_agent: FakeACPAgent
    ) -> None:
        """A call posted under its placeholder because another call started
        first keeps that name: the indicator never names a call differently
        from the room's narration of it."""

        @fake_agent.on_prompt
        async def turn(agent: FakeACPAgent, sid: str) -> None:
            await agent.emit(sid, cursor_mcp_placeholder("tc-mcp"))
            await agent.emit(sid, start_tool_call("tc-read", "read_file"))
            await agent.emit(
                sid, cursor_mcp_naming("tc-mcp", "band", "band_list_contacts", {})
            )
            await agent.emit(
                sid, update_tool_call("tc-read", status=ToolStatus.COMPLETED)
            )
            await agent.emit(
                sid, update_tool_call("tc-mcp", status=ToolStatus.COMPLETED)
            )

        recorder = ActivityRecorder()
        async with acp_adapter(fake_agent) as session:
            reply = await session.send("contacts?", tools=observed_tools(recorder))

        assert reply.tool_call_names == [CURSOR_MCP_PLACEHOLDER_TITLE, "read_file"]
        assert recorder.events == [
            shown_tool(CURSOR_MCP_PLACEHOLDER_TITLE),
            shown_tool("read_file"),
            shown_tool(CURSOR_MCP_PLACEHOLDER_TITLE),
            SHOWN_REASONING,
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("end_order", "expected"),
        [
            (
                ("tc-first", "tc-second"),
                [shown_tool("first"), shown_tool("second"), SHOWN_REASONING],
            ),
            (
                ("tc-second", "tc-first"),
                [
                    shown_tool("first"),
                    shown_tool("second"),
                    shown_tool("first"),
                    SHOWN_REASONING,
                ],
            ),
        ],
        ids=["oldest-ends-first", "newest-ends-first"],
    )
    async def test_overlapping_calls_show_the_newest_still_running(
        self,
        fake_agent: FakeACPAgent,
        end_order: tuple[str, str],
        expected: list[dict[str, str]],
    ) -> None:
        @fake_agent.on_prompt
        async def turn(agent: FakeACPAgent, sid: str) -> None:
            await agent.emit(sid, start_tool_call("tc-first", "first"))
            await agent.emit(sid, start_tool_call("tc-second", "second"))
            for tool_call_id in end_order:
                await agent.emit(
                    sid, update_tool_call(tool_call_id, status=ToolStatus.COMPLETED)
                )

        recorder = ActivityRecorder()
        async with acp_adapter(fake_agent) as session:
            await session.send("work", tools=observed_tools(recorder))

        assert recorder.events == expected

    @pytest.mark.asyncio
    async def test_call_left_running_stays_with_the_turn_that_started_it(
        self, fake_agent: FakeACPAgent
    ) -> None:
        """Each turn reports to its own observer, so a call one turn leaves
        running, even one the agent finishes during the next turn, never shows
        on the next turn's indicator."""

        @fake_agent.on_prompt
        async def first_turn(agent: FakeACPAgent, sid: str) -> None:
            await agent.emit(sid, start_tool_call("tc-long", "long_job"))

        first = ActivityRecorder()
        second = ActivityRecorder()
        async with acp_adapter(fake_agent) as session:
            await session.send("start the job", tools=observed_tools(first))

            @fake_agent.on_prompt
            async def second_turn(agent: FakeACPAgent, sid: str) -> None:
                await agent.emit(
                    sid, update_tool_call("tc-long", status=ToolStatus.COMPLETED)
                )
                await agent.emit(sid, start_tool_call("tc-next", "next_job"))
                await agent.emit(
                    sid, update_tool_call("tc-next", status=ToolStatus.COMPLETED)
                )

            await session.send("next", tools=observed_tools(second))

        assert first.events == [shown_tool("long_job")]
        assert second.events == [shown_tool("next_job"), SHOWN_REASONING]
