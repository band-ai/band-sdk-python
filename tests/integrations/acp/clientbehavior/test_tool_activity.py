"""An ACP agent's tool calls show as the turn's activity, whatever the emit set."""

from __future__ import annotations

import pytest
from acp.helpers import start_tool_call, update_tool_call

from band.core.types import Emit
from band.integrations.acp.types import ToolStatus
from band.runtime.activity import UNKNOWN_ACTIVITY_TOOL_NAME
from band.runtime.tools.types import BandTool
from band.testing.activity import SHOWN_REASONING, ActivityRecorder, shown_tool
from tests.integrations.acp.acp_toolkit import (
    FakeACPAgent,
    acp_adapter,
    fake_agent_config,
)
from tests.integrations.acp.acp_toolkit.agent import (
    CURSOR_MCP_PLACEHOLDER_TITLE,
    cursor_mcp_naming,
    cursor_mcp_placeholder,
)
from tests.integrations.acp.clientbehavior.kit import band_activity_call, observed_tools


class TestToolActivity:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("emit", [(), Emit.TOOL_CALLS], ids=["silent", "narrated"])
    async def test_running_call_shows_without_exposing_its_title_whatever_the_emit_set(
        self, fake_agent: FakeACPAgent, emit: Emit | tuple[()]
    ) -> None:
        """Narration gates neither running activity nor its privacy boundary."""
        fake_agent.will_call_tool(
            "tc-1",
            "band-band_create_chatroom",
            raw_input={"title": "x"},
            result="ok",
            start_status=ToolStatus.IN_PROGRESS,
        )
        recorder = ActivityRecorder()
        async with acp_adapter(fake_agent, emit=emit) as session:
            await session.send("make a room", tools=observed_tools(recorder))

        assert recorder.events == [
            shown_tool(UNKNOWN_ACTIVITY_TOOL_NAME),
            SHOWN_REASONING,
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [ToolStatus.COMPLETED, ToolStatus.FAILED])
    async def test_terminal_status_ends_the_call_activity(
        self, fake_agent: FakeACPAgent, status: ToolStatus
    ) -> None:
        fake_agent.will_call_tool(
            "tc-1",
            "get_weather",
            result="72F",
            status=status,
            start_status=ToolStatus.IN_PROGRESS,
        )
        recorder = ActivityRecorder()
        async with acp_adapter(fake_agent) as session:
            await session.send("weather?", tools=observed_tools(recorder))

        assert recorder.events == [
            shown_tool(UNKNOWN_ACTIVITY_TOOL_NAME),
            SHOWN_REASONING,
        ]

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
            await agent.emit(
                sid,
                start_tool_call("tc-read", "read_file", status=ToolStatus.IN_PROGRESS),
            )
            await agent.emit(
                sid, update_tool_call("tc-read", status=ToolStatus.COMPLETED)
            )

        recorder = ActivityRecorder()
        async with acp_adapter(fake_agent) as session:
            await session.send("write it", tools=observed_tools(recorder))

        assert recorder.events == [
            shown_tool(UNKNOWN_ACTIVITY_TOOL_NAME),
            SHOWN_REASONING,
        ]

    @pytest.mark.asyncio
    async def test_call_named_late_is_renamed_while_it_runs(
        self, fake_agent: FakeACPAgent
    ) -> None:
        """A pending Cursor placeholder starts activity once it becomes running."""
        fake_agent.will_call_mcp_tool_named_late(
            "tc-1", "band", "band_list_contacts", arguments={}, result="[]"
        )
        recorder = ActivityRecorder()
        async with acp_adapter(
            fake_agent, fake_agent_config(inject_band_tools=True)
        ) as session:
            reply = await session.send("contacts?", tools=observed_tools(recorder))

        assert reply.tool_call_names == ["band_list_contacts"]
        assert recorder.events == [
            shown_tool("band_list_contacts"),
            SHOWN_REASONING,
        ]

    @pytest.mark.asyncio
    async def test_call_keeps_the_name_it_was_posted_under(
        self, fake_agent: FakeACPAgent
    ) -> None:
        """Late identity preserves append-only narration independently of activity."""

        @fake_agent.on_prompt
        async def turn(agent: FakeACPAgent, sid: str) -> None:
            await agent.emit(sid, cursor_mcp_placeholder("tc-mcp"))
            await agent.emit(
                sid,
                start_tool_call("tc-read", "read_file", status=ToolStatus.IN_PROGRESS),
            )
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
        async with acp_adapter(
            fake_agent, fake_agent_config(inject_band_tools=True)
        ) as session:
            reply = await session.send("contacts?", tools=observed_tools(recorder))

        assert reply.tool_call_names == [CURSOR_MCP_PLACEHOLDER_TITLE, "read_file"]
        assert recorder.events == [
            shown_tool(UNKNOWN_ACTIVITY_TOOL_NAME),
            SHOWN_REASONING,
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("end_order", "expected"),
        [
            (
                ("tc-first", "tc-second"),
                [
                    shown_tool(BandTool.GET_PARTICIPANTS),
                    shown_tool(BandTool.LOOKUP_PEERS),
                    SHOWN_REASONING,
                ],
            ),
            (
                ("tc-second", "tc-first"),
                [
                    shown_tool(BandTool.GET_PARTICIPANTS),
                    shown_tool(BandTool.LOOKUP_PEERS),
                    shown_tool(BandTool.GET_PARTICIPANTS),
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
            await agent.emit(
                sid,
                band_activity_call(
                    "tc-first", BandTool.GET_PARTICIPANTS, ToolStatus.IN_PROGRESS
                ),
            )
            await agent.emit(
                sid,
                band_activity_call(
                    "tc-second", BandTool.LOOKUP_PEERS, ToolStatus.IN_PROGRESS
                ),
            )
            for tool_call_id in end_order:
                await agent.emit(
                    sid, update_tool_call(tool_call_id, status=ToolStatus.COMPLETED)
                )

        recorder = ActivityRecorder()
        async with acp_adapter(
            fake_agent, fake_agent_config(inject_band_tools=True)
        ) as session:
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
            await agent.emit(
                sid,
                start_tool_call("tc-long", "long_job", status=ToolStatus.IN_PROGRESS),
            )

        first = ActivityRecorder()
        second = ActivityRecorder()
        async with acp_adapter(fake_agent) as session:
            await session.send("start the job", tools=observed_tools(first))

            @fake_agent.on_prompt
            async def second_turn(agent: FakeACPAgent, sid: str) -> None:
                await agent.emit(
                    sid, update_tool_call("tc-long", status=ToolStatus.COMPLETED)
                )
                await agent.emit(
                    sid,
                    start_tool_call(
                        "tc-next", "next_job", status=ToolStatus.IN_PROGRESS
                    ),
                )
                await agent.emit(
                    sid, update_tool_call("tc-next", status=ToolStatus.COMPLETED)
                )

            await session.send("next", tools=observed_tools(second))

        assert first.events == [shown_tool(UNKNOWN_ACTIVITY_TOOL_NAME)]
        assert second.events == [
            shown_tool(UNKNOWN_ACTIVITY_TOOL_NAME),
            SHOWN_REASONING,
        ]
