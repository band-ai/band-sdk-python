"""A message that arrives while an approved OpenCode turn is still posting its
reply waits for that turn, and the working indicator stays with the turn that
owns the room."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from unittest.mock import MagicMock

import pytest

from band.adapters.opencode import OpencodeAdapter
from band.agent import Agent
from band.runtime.execution import ExecutionContext
from band.runtime.tools.agent import AgentTools
from tests.adapters.opencode.helpers import (
    APPROVED_ASK_NOTICES,
    FIRST_ANSWER,
    FOLLOW_UP,
    PERMISSION_ID,
    SECOND_ANSWER,
    make_platform_message,
    model_answer,
    parked_turn_client,
    room_turn,
    turn_finished,
)
from tests.mcpbackends import backends_created_by
from tests.runtime.activityownership.kit import HeldReplyPlatform, deliver_event
from tests.runtime.helpers import AGENT_ID, ALICE, IDLE, KEEP_ALIVE, ROOM_ID


class TestOpencodeAdmission:
    @pytest.fixture
    async def activity_platform(self) -> AsyncIterator[HeldReplyPlatform]:
        yield HeldReplyPlatform(held_reply=FIRST_ANSWER)

    async def test_a_message_during_an_approved_turns_reply_is_deferred_until_it_ends(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: HeldReplyPlatform,
    ) -> None:
        client = parked_turn_client(
            after_approval=model_answer("msg-1", FIRST_ANSWER),
            later_prompts=[model_answer("msg-2", SECOND_ANSWER)],
        )
        adapter = OpencodeAdapter(client_factory=lambda _: client, emit=())
        agent = Agent(runtime=MagicMock(agent_id=AGENT_ID), adapter=adapter)
        activity_platform.add_message("original")

        with backends_created_by():
            await adapter.on_started("Test", "Test")
            ctx = execution(agent._on_execute)
            try:
                await deliver_event(ctx, activity_platform.event("original"))
                approval = make_platform_message(
                    room_id=ROOM_ID,
                    content=f"approve {PERMISSION_ID}",
                    sender_id=ALICE["id"],
                )
                assert await adapter.try_handle_control(
                    approval, AgentTools.from_context(ctx)
                )
                await activity_platform.reply_entered.wait()
                first_turn = await room_turn(adapter, room_id=ROOM_ID)

                activity_platform.add_message("new", content=FOLLOW_UP)
                await deliver_event(
                    ctx, activity_platform.event("new", content=FOLLOW_UP)
                )
                assert activity_platform.marked("processed") == ["original"]
                assert activity_platform.marked("failed") == []
                assert len(client.prompt_calls) == 1
                assert activity_platform.reports_without_refreshes == [KEEP_ALIVE]

                activity_platform.release.set()
                await turn_finished(first_turn)
                await ctx._join_activity_completion()
                assert activity_platform.reports_without_refreshes == [KEEP_ALIVE, IDLE]

                await ctx.scheduler.run_pass(recovery=True)
                await ctx._join_activity_completion()

                assert activity_platform.marked("processed") == ["original", "new"]
                assert activity_platform.room_messages == [
                    *APPROVED_ASK_NOTICES,
                    FIRST_ANSWER,
                    SECOND_ANSWER,
                ]
                assert activity_platform.reports_without_refreshes == [
                    KEEP_ALIVE,
                    IDLE,
                    KEEP_ALIVE,
                    IDLE,
                ]
            finally:
                activity_platform.release.set()
                await adapter.on_cleanup(ROOM_ID)
