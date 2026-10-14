"""Interrupted partial switches affect the live session but are never remembered."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import BandConfigError
from band.core.model_catalog import ModelSelection
from tests.integrations.acp.acp_toolkit import (
    DEFAULT_ROOM,
    FakeACPAgent,
    acp_adapter,
    fake_agent_config,
)
from tests.integrations.acp.acp_toolkit.agent import EFFORT_OPTION_ID, MODEL_OPTION_ID
from tests.integrations.acp.roomownership.kit import TEST_GUARD_S, recover


class TestInterruptedSettings:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("interruption", ["cancel", "disconnect"])
    async def test_a_partially_applied_switch_is_not_restored(
        self, interruption: str
    ) -> None:
        configured = ModelSelection(model="a", reasoning_effort="low")
        agent = (
            FakeACPAgent(supports_session_load=True)
            .advertises_models(
                {"a": ("low", "high"), "b": ("low", "high")}, current="a"
            )
            .will_say("still serving")
        )
        async with acp_adapter(
            agent, fake_agent_config(model="a", reasoning_effort="low")
        ) as session:
            await session.send("bootstrap")
            retained = session.session_id(DEFAULT_ROOM)
            agent.knows_session(retained)
            effort_reply = agent.holds_config_replies(EFFORT_OPTION_ID)
            switch = asyncio.create_task(
                session.adapter.apply_model_selection(
                    ModelSelection(model="b", reasoning_effort="high"),
                    room_id=DEFAULT_ROOM,
                )
            )
            try:
                async with asyncio.timeout(TEST_GUARD_S):
                    await effort_reply.received.wait()
                    assert agent.current_value(MODEL_OPTION_ID) == "b"
                    assert agent.current_value(EFFORT_OPTION_ID) == "high"
                    assert session.adapter._room_selection(DEFAULT_ROOM) == configured
                    if interruption == "cancel":
                        switch.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await switch
                    else:
                        agent.hang_up()
                        with pytest.raises(BandConfigError):
                            await switch
            finally:
                effort_reply.release.set()
                await asyncio.gather(switch, return_exceptions=True)

            await recover(session)
            restored = await session.send("recover")

            assert restored.errors == []
            assert session.adapter._room_selection(DEFAULT_ROOM) == configured
            assert agent.current_value(MODEL_OPTION_ID) == "a"
            assert agent.current_value(EFFORT_OPTION_ID) == "low"
            assert agent.session_load_requests == [retained]
            assert agent.config_selections(retained)[-2:] == [
                (MODEL_OPTION_ID, "a"),
                (EFFORT_OPTION_ID, "low"),
            ]
