"""A switch on a room left without a session applies to its next session."""

from __future__ import annotations

import pytest

from band.core.model_catalog import ModelSelection
from band.integrations.acp.session_config import CONFIG_FAILURE_PREFIX
from band.integrations.acp.types import REFUSAL_STOP
from tests.integrations.acp.acp_toolkit import DEFAULT_ROOM, acp_adapter
from tests.integrations.acp.acp_toolkit.agent import END_TURN_STOP, MODEL_OPTION_ID
from tests.integrations.acp.roomownership.kit import models


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

        (error,) = refused.errors
        assert error.startswith(CONFIG_FAILURE_PREFIX)
        assert len(agent.prompts) == 2
        assert agent.config_selections() == []
