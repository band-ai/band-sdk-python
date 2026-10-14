"""A process starts within the room's bounded acquisition."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import pytest

from band.core.retention import RoomRetentionConfig
from band.integrations.acp import client_adapter
from band.integrations.acp.client_adapter import PROCESS_NOT_READY_ERROR
from tests.integrations.acp.acp_toolkit import (
    DEFAULT_ROOM,
    AcpSession,
    FakeACPAgent,
    accepted_work_finished,
    acp_adapter,
    fake_agent_config,
    released,
)
from tests.integrations.acp.roomownership.kit import (
    SHORT_TRANSITION_S,
    TEST_GUARD_S,
    cursor,
    inline,
    stop,
)


def adapter_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == client_adapter.__name__ and record.levelno == logging.WARNING
    ]


class TestStartup:
    """A process starts within the room's bounded acquisition."""

    @pytest.mark.asyncio
    async def test_a_start_that_never_answers_fails_at_its_bound_and_recovers(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        agent = FakeACPAgent().will_say("ok")
        agent.holds_next_initialize()
        config = fake_agent_config(
            retention=RoomRetentionConfig(transition_timeout_s=SHORT_TRANSITION_S)
        )
        async with acp_adapter(agent, config) as session:
            with pytest.raises(TimeoutError):
                await session.send("hello")
            assert session.last_reply.errors == [PROCESS_NOT_READY_ERROR]
            assert adapter_warnings(caplog) == [
                (
                    f"Room {DEFAULT_ROOM}: ACP agent process not ready: "
                    f"Room {DEFAULT_ROOM} start abandoned after {SHORT_TRANSITION_S}s"
                )
            ]

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
        # The default start bound is far away: the stop, not the bound, frees it.
        async with opened(agent) as session:
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
