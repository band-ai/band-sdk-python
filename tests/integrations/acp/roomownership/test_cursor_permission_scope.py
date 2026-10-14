"""An always grant applies to one Cursor turn, never to its successor."""

from __future__ import annotations

import asyncio

import pytest
from acp.schema import ToolCallUpdate

from band.integrations.acp.cursor import (
    DECISION_NOT_PENDING_TEMPLATE,
    ROOM_COMMAND,
    CursorCommandWord,
)
from band.runtime.tools import BAND_MCP_SERVER_NAME, BandTool
from tests.integrations.acp.acp_toolkit import (
    DEFAULT_ROOM,
    FakeACPAgent,
    TranscriptTools,
    accepted_work_finished,
)
from tests.integrations.acp.roomownership.cursorflow import (
    ALLOW_ALWAYS,
    advertised_decisions,
    permission_menu,
    permission_selections,
)
from tests.integrations.acp.roomownership.kit import (
    TEST_GUARD_S,
    cursor,
    decisions,
    restart,
)


class TestCursorPermissionScope:
    @pytest.mark.parametrize("successor", ["next-turn", "leave-rejoin", "stop-restart"])
    async def test_an_always_grant_and_its_old_answer_do_not_authorize_a_successor(
        self, successor: str
    ) -> None:
        peer = FakeACPAgent()

        @peer.on_prompt
        async def request_shell(agent: FakeACPAgent, session_id: str) -> None:
            count = 2 if len(agent.prompts) == 1 else 1
            for number in range(count):
                await agent.ask_permission(
                    session_id,
                    ToolCallUpdate(tool_call_id=f"shell-{number}", title="shell"),
                    permission_menu(),
                )
            await agent.call_mcp_tool(
                session_id=session_id,
                server=BAND_MCP_SERVER_NAME,
                tool_name=BandTool.NO_REPLY,
                arguments={},
            )

        async with (
            cursor(peer, inject_band_tools=True) as session,
            asyncio.timeout(TEST_GUARD_S),
        ):
            original_tools = TranscriptTools()
            original = await session.send_judged("first turn", tools=original_tools)
            first_token = advertised_decisions(original)[CursorCommandWord.SELECT]
            await session.send_judged(
                f"{ROOM_COMMAND} {CursorCommandWord.SELECT} {first_token} {ALLOW_ALWAYS}"
            )
            await accepted_work_finished(session.room())
            assert permission_selections(peer) == [ALLOW_ALWAYS, ALLOW_ALWAYS]
            assert len(decisions(original)) == 1
            assert original_tools.turn.complete

            match successor:
                case "leave-rejoin":
                    await session.adapter.on_cleanup(DEFAULT_ROOM)
                case "stop-restart":
                    await session.adapter.cleanup_all()
                    await restart(session)

            successor_tools = TranscriptTools()
            current = await session.send_judged("next turn", tools=successor_tools)
            current_token = advertised_decisions(current)[CursorCommandWord.SELECT]
            old_answer = await session.send_judged(
                f"{ROOM_COMMAND} {CursorCommandWord.SELECT} {first_token} {ALLOW_ALWAYS}"
            )
            assert old_answer.texts == [
                DECISION_NOT_PENDING_TEMPLATE.format(token=first_token)
            ]
            assert permission_selections(peer) == [ALLOW_ALWAYS, ALLOW_ALWAYS]
            await session.send_judged(
                f"{ROOM_COMMAND} {CursorCommandWord.DENY} {current_token}"
            )
            await accepted_work_finished(session.room())

            assert permission_selections(peer) == [ALLOW_ALWAYS, ALLOW_ALWAYS, None]
            assert successor_tools.turn.complete
            assert original.errors == current.errors == []
