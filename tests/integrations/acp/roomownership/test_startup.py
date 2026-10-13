"""A process starts within the room's bounded acquisition."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import pytest

from band.core.retention import RoomRetentionConfig
from tests.integrations.acp.acp_toolkit import (
    AcpSession,
    FakeACPAgent,
    accepted_work_finished,
    acp_adapter,
    fake_agent_config,
    released,
)
from tests.integrations.acp.roomownership.kit import (
    HUNG_START_TIMEOUT_S,
    SHORT_TRANSITION_S,
    TEST_GUARD_S,
    cursor,
    inline,
    stop,
)


class TestStartup:
    """A process starts within the room's bounded acquisition."""

    @pytest.mark.asyncio
    async def test_a_start_that_never_answers_fails_at_its_bound_and_recovers(
        self,
    ) -> None:
        agent = FakeACPAgent().will_say("ok")
        agent.holds_next_initialize()
        config = fake_agent_config(start_timeout_s=HUNG_START_TIMEOUT_S)
        async with acp_adapter(agent, config) as session:
            with pytest.raises(TimeoutError, match="did not start"):
                await session.send("hello")

            served = await session.send("again")

            assert served.errors == []
            assert session.room().generation == 2
        assert len(agent.prompts) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("opened", [inline, cursor], ids=["inline", "cursor"])
    async def test_a_stop_during_a_start_that_never_answers_frees_the_room(
        self, opened: Callable[..., AbstractAsyncContextManager[AcpSession]]
    ) -> None:
        agent = FakeACPAgent().will_say("ok")
        starting = agent.holds_next_initialize()
        # Far above the bound below: the stop, not the start bound, frees it.
        async with opened(agent, start_timeout_s=60) as session:
            first = asyncio.create_task(session.send("hello"))
            await starting.received.wait()

            await stop(session, first)
            async with asyncio.timeout(TEST_GUARD_S):
                served = await session.send("again")
                await accepted_work_finished(session.room())

            assert served.errors == []
        assert len(agent.prompts) == 1

    @pytest.mark.asyncio
    async def test_a_start_outlasting_the_transition_timeout_fails_and_recovers(
        self,
    ) -> None:
        agent = FakeACPAgent().will_say("ok")
        starting = agent.holds_next_initialize()
        config = fake_agent_config(
            retention=RoomRetentionConfig(transition_timeout_s=SHORT_TRANSITION_S)
        )
        async with acp_adapter(agent, config) as session:
            with pytest.raises(TimeoutError):
                await session.send("hello")
            starting.release.set()
            await released(session.room())

            served = await session.send("again")

            assert served.errors == []
            assert session.room().generation == 2
        assert len(agent.prompts) == 1
