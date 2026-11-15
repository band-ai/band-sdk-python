"""A stop is confirmed by the agent answering the prompt, never by the cancel."""

from __future__ import annotations

import asyncio

import pytest

from band.core.retention import Residency
from tests.integrations.acp.acp_toolkit import (
    DEFAULT_ROOM,
    FakeACPAgent,
    released,
)
from tests.integrations.acp.acp_toolkit.agent import CANCEL_RECORD
from tests.integrations.acp.roomownership.kit import holds_prompt, inline, stop
from tests.toolkit.tasks import background


class TestStopConfirmation:
    """``session/cancel`` is a notification: the room stays blocked until the
    agent answers the prompt, or its process is released."""

    @pytest.mark.asyncio
    async def test_an_answered_prompt_confirms_the_stop_and_keeps_the_process(
        self,
    ) -> None:
        agent = FakeACPAgent()
        prompting = holds_prompt(agent, 1)
        async with (
            inline(agent) as session,
            background(session.send("work")) as working,
        ):
            await prompting.received.wait()
            turns = session.adapter.backend_turns
            runtime = session.runtime()
            (stopped,) = agent.session_ids()

            turns.cancel(DEFAULT_ROOM)
            await turns.drain()
            # The cancel went out and nothing has answered the prompt yet.
            assert agent.lifecycle == [f"{CANCEL_RECORD} {stopped}"]
            assert turns.blocker(DEFAULT_ROOM) is not None

            prompting.release.set()
            await working
            assert turns.blocker(DEFAULT_ROOM) is None
            assert turns.unconfirmed_handle(DEFAULT_ROOM) is None

            served = await session.send("again")

            assert served.errors == []
            assert session.runtime() is runtime and not runtime.closed
            assert session.room().generation == 1

    @pytest.mark.asyncio
    async def test_an_abandoned_prompt_wait_recovers_the_process(self) -> None:
        agent = FakeACPAgent().will_say("ok")
        prompting = holds_prompt(agent, 1)
        async with inline(agent) as session:
            handler = asyncio.create_task(session.send("work"))
            try:
                await prompting.received.wait()
                room, abandoned = session.room(), session.runtime()

                await stop(session, handler)
                await session.adapter.backend_turns.drain()
                await released(room)
            finally:
                prompting.release.set()

            served = await session.send("again")

            assert abandoned.closed
            assert served.errors == []
            assert session.runtime() is not abandoned
            assert session.room().generation == 2

    @pytest.mark.asyncio
    async def test_a_cancel_that_cannot_be_sent_recovers_while_the_prompt_waits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = FakeACPAgent().will_say("ok")
        prompting = holds_prompt(agent, 1)
        async with (
            inline(agent) as session,
            background(session.send("work")) as working,
        ):
            await prompting.received.wait()
            room, runtime = session.room(), session.runtime()
            turns = session.adapter.backend_turns

            async def refuse_cancel(session_id: str) -> None:
                raise ConnectionError(session_id)

            monkeypatch.setattr(runtime, "cancel_turn", refuse_cancel)
            turns.cancel(DEFAULT_ROOM)
            await turns.drain()

            # The prompt is still awaited, yet nothing can confirm this stop.
            assert not working.done()
            assert turns.unconfirmed_handle(DEFAULT_ROOM) is not None
            assert room.retained.snapshot.state == Residency.CLEANUP_PENDING

            prompting.release.set()
            await working
            await released(room)

            served = await session.send("again")

            assert runtime.closed
            assert served.errors == []
            assert session.room().generation == 2
