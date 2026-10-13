"""A model switch takes its room in turn, like a prompt."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import BandConfigError, RoomClosingError
from band.core.model_catalog import ModelSelection
from tests.adapters.waiting import until
from tests.integrations.acp.acp_toolkit import DEFAULT_ROOM, acp_adapter
from tests.integrations.acp.acp_toolkit.agent import MODEL_OPTION_ID
from tests.integrations.acp.roomownership.kit import (
    SWITCHED_MODEL,
    TEST_GUARD_S,
    holds_prompt,
    models,
    recover,
    switch_behind_held_prompt,
)


class TestSettings:
    """A model switch takes its room in turn, like a prompt."""

    @pytest.mark.asyncio
    async def test_a_switch_on_a_recovered_room_starts_no_process(self) -> None:
        agent = models("a", "a", "b")
        async with acp_adapter(agent) as session:
            await session.send("bootstrap")
            session_id = session.session_id(DEFAULT_ROOM)
            room = await recover(session)

            with pytest.raises(BandConfigError, match="no live ACP session"):
                await session.adapter.apply_model_selection(
                    ModelSelection(model="b"), room_id=DEFAULT_ROOM
                )

            assert room.runtime is None
        assert agent.config_selections(session_id) == []

    @pytest.mark.asyncio
    async def test_a_cancelled_switch_caller_stays_cancelled_while_its_room_leaves(
        self,
    ) -> None:
        async with switch_behind_held_prompt() as queued:
            room = queued.session.room()
            async with asyncio.timeout(TEST_GUARD_S):
                queued.held.cancel()
                leaving = asyncio.create_task(
                    queued.session.adapter.on_cleanup(DEFAULT_ROOM)
                )
                # The caller gives up as the room starts closing, in the one
                # loop turn before the cancelled prompt frees the switch to
                # be refused by the closing room.
                await asyncio.sleep(0)
                assert room.retained.closing
                queued.switch.cancel()
                queued.prompting.release.set()
                await leaving
                with pytest.raises(asyncio.CancelledError):
                    await queued.switch
                await asyncio.gather(queued.held, return_exceptions=True)

        assert queued.agent.config_selections(queued.session_id) == []

    @pytest.mark.asyncio
    async def test_a_switch_waits_for_the_rooms_running_prompt(self) -> None:
        async with switch_behind_held_prompt() as queued:
            assert not queued.switch.done()
            assert queued.agent.config_selections(queued.session_id) == []

            queued.prompting.release.set()
            await queued.held
            await queued.switch

        assert queued.agent.config_selections(queued.session_id) == [
            (MODEL_OPTION_ID, SWITCHED_MODEL)
        ]

    @pytest.mark.asyncio
    async def test_a_switch_waiting_for_a_prompt_never_holds_the_rooms_leave(
        self,
    ) -> None:
        async with switch_behind_held_prompt() as queued:
            async with asyncio.timeout(TEST_GUARD_S):
                # The runtime cancels the room's handler, then the room leaves.
                queued.held.cancel()
                leaving = asyncio.create_task(
                    queued.session.adapter.on_cleanup(DEFAULT_ROOM)
                )
                await until(
                    lambda: queued.session_id in queued.agent.cancelled_sessions,
                    failure="the held prompt was never cancelled",
                )
                # The agent honours the cancel, ending its prompt.
                queued.prompting.release.set()
                await leaving
                with pytest.raises(BandConfigError, match="ended mid-switch"):
                    await queued.switch
                await asyncio.gather(queued.held, return_exceptions=True)

        assert queued.agent.config_selections(queued.session_id) == []

    @pytest.mark.asyncio
    async def test_a_switch_on_a_leaving_room_is_refused_before_it_runs(
        self,
    ) -> None:
        agent = models("a", "a", "b")
        prompting = holds_prompt(agent, 2)
        async with acp_adapter(agent) as session:
            await session.send("bootstrap")
            session_id = session.session_id(DEFAULT_ROOM)
            held = asyncio.create_task(session.send("held"))
            await prompting.received.wait()
            async with asyncio.timeout(TEST_GUARD_S):
                leaving = asyncio.create_task(session.adapter.on_cleanup(DEFAULT_ROOM))
                await until(
                    lambda: session.room().retained.closing,
                    failure="the room never started leaving",
                )
                try:
                    with pytest.raises(RoomClosingError):
                        await session.adapter.apply_model_selection(
                            ModelSelection(model="b"), room_id=DEFAULT_ROOM
                        )
                finally:
                    prompting.release.set()
                    await asyncio.gather(leaving, held, return_exceptions=True)

        assert agent.config_selections(session_id) == []

    @pytest.mark.asyncio
    async def test_a_prompt_in_one_room_never_holds_another_rooms_switch(
        self,
    ) -> None:
        agent = models("a", "a", "b")
        prompting = holds_prompt(agent, 3)
        async with acp_adapter(agent) as session:
            await session.send("bootstrap", room="room-a")
            await session.send("bootstrap", room="room-b")
            other = session.session_id("room-b")
            held = asyncio.create_task(session.send("held", room="room-a"))
            await prompting.received.wait()

            async with asyncio.timeout(TEST_GUARD_S):
                await session.adapter.apply_model_selection(
                    ModelSelection(model="b"), room_id="room-b"
                )

            assert agent.config_selections(other) == [(MODEL_OPTION_ID, "b")]
            prompting.release.set()
            await held
