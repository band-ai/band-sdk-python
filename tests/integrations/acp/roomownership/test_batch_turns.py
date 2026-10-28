"""A batch turn rides the retained-room contract a single message does."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import pytest
from acp import RequestError

from band.core.exceptions import InputNotAccepted
from band.runtime.formatters import InputField
from tests.adapters.waiting import until
from tests.integrations.acp.acp_toolkit import (
    DEFAULT_ROOM,
    AcpSession,
    FakeACPAgent,
    live_message,
)
from tests.integrations.acp.roomownership.kit import (
    cursor,
    fails_prompt,
    holds_prompt,
    inline,
)
from tests.runtime.currentrecords import current_records
from tests.toolkit.tasks import background

# One person writing twice around another.
BATCH = tuple(
    live_message(f"input {number}", sender_id=sender)
    for number, sender in enumerate(("alice", "bob", "alice"), start=1)
)

Opened = Callable[..., AbstractAsyncContextManager[AcpSession]]


def senders_shown(agent: FakeACPAgent) -> list[str | None]:
    """The sender of each current input in the agent's latest prompt."""
    return [
        record[InputField.SENDER_ID]
        for record in current_records(agent.prompt_texts()[-1])
    ]


OPENED = pytest.mark.parametrize("opened", [inline, cursor], ids=["inline", "cursor"])


class TestBatchTurns:
    @OPENED
    @pytest.mark.asyncio
    async def test_a_batch_after_a_crash_prompts_the_reloaded_session(
        self, opened: Opened
    ) -> None:
        agent = FakeACPAgent(supports_session_load=True)
        fails_prompt(agent, 2, RequestError.internal_error({"detail": "wedged"}))

        async with opened(agent) as session:
            await session.send("seeding turn")
            with pytest.raises(RequestError):
                await session.send("hello")
            retained = session.session_id(DEFAULT_ROOM)
            crashed = session.room().runtime
            agent.knows_session(retained)

            await session.send_batch(*BATCH)

            assert session.runtime() is not crashed
        assert agent.session_load_requests == [retained]
        assert senders_shown(agent) == ["alice", "bob", "alice"]

    @OPENED
    @pytest.mark.asyncio
    async def test_a_batch_is_refused_while_its_room_is_closing(
        self, opened: Opened
    ) -> None:
        agent = FakeACPAgent().will_say("ok")

        async with opened(agent) as session:
            await session.send("hello")
            exiting = agent.exits_slowly()
            leaving = asyncio.create_task(session.adapter.on_cleanup(DEFAULT_ROOM))
            await exiting.received.wait()

            with pytest.raises(InputNotAccepted):
                await session.send_batch(*BATCH)
            exiting.release.set()
            await leaving

        assert len(agent.prompts) == 1

    @pytest.mark.asyncio
    async def test_a_batch_prompts_only_after_the_rooms_running_prompt_ends(
        self,
    ) -> None:
        agent = FakeACPAgent()
        running = holds_prompt(agent, 1)

        async with (
            inline(agent) as session,
            background(session.send("first")) as first,
        ):
            try:
                await running.received.wait()
                async with background(session.send_batch(*BATCH)) as batch:
                    try:
                        await until(
                            lambda: session.room().session_waiters == 2 or batch.done(),
                            failure="the batch never queued behind the running prompt",
                        )
                        assert not batch.done()
                        prompts_while_running = len(agent.prompts)
                    finally:
                        running.release.set()
                    await asyncio.gather(first, batch)
            finally:
                running.release.set()

        assert prompts_while_running == 1
        assert senders_shown(agent) == ["alice", "bob", "alice"]
