"""A switch on a room without a live session waits for its next session, or
is refused when that session is not the switch's to configure."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import BandConfigError
from band.core.model_catalog import ModelSelection
from band.integrations.acp.session_config import CONFIG_FAILURE_PREFIX
from band.integrations.acp.types import REFUSAL_STOP
from tests.adapters.waiting import until
from tests.integrations.acp.acp_toolkit import (
    DEFAULT_ROOM,
    FakeACPAgent,
    acp_adapter,
)
from tests.integrations.acp.acp_toolkit.agent import (
    EFFORT_OPTION_ID,
    END_TURN_STOP,
    MODEL_OPTION_ID,
)
from tests.integrations.acp.acp_toolkit.harness import fake_agent_config
from tests.integrations.acp.roomownership.kit import models, recover, stop

EFFORTS = ("low", "medium", "high")


class TestSwitchWithoutSession:
    """A refused seed leaves the process up without a session; a switch then
    waits for the next session and is checked against its catalog there."""

    @pytest.mark.asyncio
    async def test_the_next_session_starts_on_the_switched_model(self) -> None:
        agent = models("a", "a", "b").stops_with(REFUSAL_STOP)
        async with acp_adapter(agent) as session:
            await session.send("first?")
            await session.adapter.apply_model_selection(
                ModelSelection(model="b"), room_id=DEFAULT_ROOM
            )
            agent.stops_with(END_TURN_STOP)
            await session.send("second?")

        refused, fresh = agent.session_ids()
        assert agent.prompts[1]["session_id"] == fresh
        assert agent.config_selections(refused) == []
        assert agent.config_selections(fresh) == [(MODEL_OPTION_ID, "b")]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("switch", "applied"),
        [
            (
                ModelSelection(model="b"),
                [(MODEL_OPTION_ID, "b")],
            ),
            (
                ModelSelection(model="a"),
                [(MODEL_OPTION_ID, "a"), (EFFORT_OPTION_ID, "high")],
            ),
            (
                ModelSelection(reasoning_effort="low"),
                [(MODEL_OPTION_ID, "a"), (EFFORT_OPTION_ID, "low")],
            ),
        ],
        ids=["new-model-drops-the-effort", "same-model-keeps-it", "effort-only"],
    )
    async def test_the_switch_updates_the_rooms_selection(
        self, switch: ModelSelection, applied: list[tuple[str, str]]
    ) -> None:
        agent = FakeACPAgent().advertises_models(
            {"a": EFFORTS, "b": EFFORTS}, current="a"
        )
        agent.stops_with(REFUSAL_STOP)
        config = fake_agent_config(model="a", reasoning_effort="high")
        async with acp_adapter(agent, config) as session:
            await session.send("first?")
            await session.adapter.apply_model_selection(switch, room_id=DEFAULT_ROOM)
            agent.stops_with(END_TURN_STOP)
            await session.send("second?")

        _, fresh = agent.session_ids()
        assert agent.config_selections(fresh) == applied

    @pytest.mark.asyncio
    async def test_an_unoffered_model_fails_the_next_turn_once(self) -> None:
        agent = models("a", "a", "b").stops_with(REFUSAL_STOP)
        async with acp_adapter(agent) as session:
            await session.send("first?")
            await session.adapter.apply_model_selection(
                ModelSelection(model="z"), room_id=DEFAULT_ROOM
            )
            agent.stops_with(END_TURN_STOP)
            refused = await session.send_judged("second?")
            await session.send("third?")

        refused_seed, _, reverted = agent.session_ids()
        assert refused.errors == [
            f'{CONFIG_FAILURE_PREFIX}model "z" is not advertised; available: a, b'
        ]
        assert [prompt["session_id"] for prompt in agent.prompts] == [
            refused_seed,
            reverted,
        ]
        assert agent.config_selections() == []

    @pytest.mark.asyncio
    async def test_a_room_without_a_process_refuses_the_switch(self) -> None:
        agent = models("a", "a", "b").stops_with(REFUSAL_STOP)
        async with acp_adapter(agent) as session:
            await session.send("first?")
            room = await recover(session)

            with pytest.raises(BandConfigError, match="no live ACP session"):
                await session.adapter.apply_model_selection(
                    ModelSelection(model="b"), room_id=DEFAULT_ROOM
                )

            assert room.runtime is None
        assert agent.config_selections() == []

    @pytest.mark.asyncio
    async def test_a_session_being_set_up_refuses_the_switch(self) -> None:
        """The setup applies the selection it started with, so a switch that
        reaches the room meanwhile is refused rather than lost."""
        agent = models("a", "a", "b")
        replies = agent.holds_config_replies(MODEL_OPTION_ID)
        async with acp_adapter(agent, fake_agent_config(model="a")) as session:
            first = asyncio.create_task(session.send("first?"))
            await replies.received.wait()
            room = session.room()
            accepted = room.retained.snapshot.accepted
            switch = asyncio.create_task(
                session.adapter.apply_model_selection(
                    ModelSelection(model="b"), room_id=DEFAULT_ROOM
                )
            )
            await until(
                lambda: room.retained.snapshot.accepted > accepted,
                failure="switch was not admitted",
            )
            second = asyncio.create_task(session.send("second?"))
            await until(
                lambda: room.session_waiters == 2,
                failure="successor prompt did not queue",
            )
            await stop(session, first)
            try:
                with pytest.raises(BandConfigError, match="no live ACP session"):
                    await switch
            finally:
                replies.release.set()
                await second
            served = session.session_id(DEFAULT_ROOM)

        assert [prompt["session_id"] for prompt in agent.prompts] == [served]
        assert agent.config_selections(served) == [(MODEL_OPTION_ID, "a")]
