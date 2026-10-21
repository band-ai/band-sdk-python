"""A message that arrives while an approved OpenCode turn is still posting its
reply waits for that turn, and the working indicator stays with the turn that
owns the room."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from unittest.mock import MagicMock

import pytest

from band.adapters.opencode import OpencodeAdapter
from band.agent import Agent
from band.runtime.execution import ExecutionContext, TurnOutcome
from band.runtime.tools.agent import AgentTools
from tests.adapters.opencode.helpers import (
    PERMISSION_ID,
    make_platform_message,
    model_answer,
    parked_turn_client,
)
from tests.conftest import make_message_event
from tests.mcpbackends import backends_created_by
from tests.runtime.activityownership.kit import HeldReplyPlatform
from tests.runtime.helpers import AGENT_ID, ALICE, IDLE, KEEP_ALIVE, ROOM_ID

FIRST_ANSWER = "first answer"
SECOND_ANSWER = "second answer"


@pytest.fixture
async def activity_platform() -> AsyncIterator[HeldReplyPlatform]:
    yield HeldReplyPlatform(held_reply=FIRST_ANSWER)


class TestOpencodeAdmission:
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
        activity_platform.add_message("new", content="and then?")

        with backends_created_by():
            await adapter.on_started("Test", "Test")
            ctx = execution(agent._on_execute)
            try:
                await ctx._process_event(make_message_event(msg_id="original"))
                approval = make_platform_message(
                    room_id=ROOM_ID,
                    content=f"approve {PERMISSION_ID}",
                    sender_id=ALICE["id"],
                )
                assert await adapter.try_handle_control(
                    approval, AgentTools.from_context(ctx)
                )
                await activity_platform.reply_entered.wait()
                first_turn = (await adapter._get_or_create_room_state(ROOM_ID)).turn
                assert first_turn is not None and first_turn.watcher is not None

                outcome = await ctx._process_event(
                    make_message_event(msg_id="new", content="and then?")
                )
                assert outcome is TurnOutcome.NOT_ACCEPTED
                assert activity_platform.marked("processed") == ["original"]
                assert activity_platform.marked("failed") == []
                assert len(client.prompt_calls) == 1
                assert activity_platform.activity_reports == [KEEP_ALIVE]

                activity_platform.release.set()
                await first_turn.watcher
                await ctx._join_activity_completion()
                assert activity_platform.activity_reports == [KEEP_ALIVE, IDLE]

                await ctx._reconcile()
                await ctx._join_activity_completion()

                assert activity_platform.marked("processed") == ["original", "new"]
                assert activity_platform.room_messages[-2:] == [
                    FIRST_ANSWER,
                    SECOND_ANSWER,
                ]
                assert activity_platform.activity_reports == [
                    KEEP_ALIVE,
                    IDLE,
                    KEEP_ALIVE,
                    IDLE,
                ]
            finally:
                activity_platform.release.set()
                await adapter.on_cleanup(ROOM_ID)
