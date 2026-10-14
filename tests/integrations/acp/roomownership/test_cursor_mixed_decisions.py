"""Concurrent native decisions remain scoped to their kind and originating room."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable

from band.integrations.acp.cursor import (
    DECISION_NOT_PENDING_TEMPLATE,
    DECISION_RESOLVED_TEMPLATE,
    ROOM_COMMAND,
    CursorCommandWord,
)
from band.runtime.tools import BAND_MCP_SERVER_NAME, BandTool
from tests.adapters.waiting import until
from tests.integrations.acp.acp_toolkit import (
    FakeACPAgent,
    TranscriptTools,
    accepted_work_finished,
)
from tests.integrations.acp.roomownership.cursorflow import (
    ALLOW_ONCE,
    advertised_decisions,
)
from tests.integrations.acp.roomownership.kit import (
    CANCELLED,
    TEST_GUARD_S,
    answer,
    answered,
    ask_mode,
    ask_shell_permission,
    cursor,
    decisions,
)


class TestCursorMixedDecisions:
    async def test_wrong_kind_room_and_duplicate_answers_cannot_claim_other_decisions(
        self,
    ) -> None:
        peer = FakeACPAgent()
        outcomes: dict[str, object] = {}

        async def record(kind: str, request: Awaitable[object]) -> None:
            outcomes[kind] = await request

        @peer.on_prompt
        async def ask_every_kind(agent: FakeACPAgent, session_id: str) -> None:
            await asyncio.gather(
                record("permission", ask_shell_permission(agent, session_id)),
                record("question", ask_mode(agent, session_id)),
                record("plan", agent.ask_cursor_plan(session_id, "Repair the project")),
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
            original = await session.send_judged(
                "prepare repairs", tools=original_tools
            )
            await until(
                lambda: len(decisions(original)) == 3,
                failure="Cursor did not post all three native decisions",
            )
            tokens = advertised_decisions(original)
            permission = tokens[CursorCommandWord.SELECT]
            question = tokens[CursorCommandWord.ANSWER]
            plan = tokens[CursorCommandWord.ACCEPT]

            wrong_room = await session.send_judged(
                answer(question, "agent"), room="other-room"
            )
            assert wrong_room.texts == [
                DECISION_NOT_PENDING_TEMPLATE.format(token=question)
            ]
            for command in (
                f"{ROOM_COMMAND} {CursorCommandWord.ACCEPT} {permission}",
                f"{ROOM_COMMAND} {CursorCommandWord.SELECT} {plan} {ALLOW_ONCE}",
                answer(question, "missing-option"),
            ):
                refused = await session.send_judged(command)
                assert refused.errors == []
            assert outcomes == {}
            assert not original_tools.turn.complete

            accept_plan = f"{ROOM_COMMAND} {CursorCommandWord.ACCEPT} {plan}"
            resolved = await session.send_judged(accept_plan)
            assert resolved.texts == [
                DECISION_RESOLVED_TEMPLATE.format(kind="plan", token=plan)
            ]
            await until(
                lambda: "plan" in outcomes, failure="Native plan request stayed waiting"
            )
            duplicate = await session.send_judged(accept_plan)
            assert duplicate.texts == [DECISION_NOT_PENDING_TEMPLATE.format(token=plan)]
            await session.send_judged(answer(question, "agent"))
            await until(
                lambda: "question" in outcomes,
                failure="Native question request stayed waiting",
            )
            await session.send_judged(
                f"{ROOM_COMMAND} {CursorCommandWord.DENY} {permission}"
            )
            await until(
                lambda: "permission" in outcomes,
                failure="Native permission request stayed waiting",
            )
            await accepted_work_finished(session.room())

            assert outcomes == {
                "permission": CANCELLED,
                "question": answered("agent"),
                "plan": {"outcome": {"outcome": "accepted"}},
            }
            assert original_tools.turn.complete
            assert original.errors == []
            assert len(peer.prompts) == 1
