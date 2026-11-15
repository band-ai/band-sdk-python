"""ADK decline decisions belong only to the current runner invocation."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel

from band.adapters.google_adk import GoogleADKAdapter
from band.runtime.custom_tools import declares_turn_effect
from band.runtime.tools.types import TurnEffect
from band.testing import FakeAgentTools
from tests.adapters.genaikit import text_reply
from tests.framework_conformance.turnprobes import turn_input
from tests.integrations.googleadk.kit import (
    DECLINED,
    FOLLOWUP,
    DeclineInput,
    GatedWork,
    GoogleModelPeer,
    WorkInput,
    batch,
    decline,
)


class TestIsolation:
    async def test_a_new_turn_does_not_reuse_an_old_stop_decision(
        self, google_model: GoogleModelPeer
    ) -> None:
        calls: list[str] = []

        @declares_turn_effect(TurnEffect.DECLINE)
        async def counted_decline() -> str:
            calls.append(DECLINED)
            return DECLINED

        google_model.responses.extend([batch(("decline", {})), batch(("decline", {}))])
        adapter = GoogleADKAdapter(additional_tools=[(DeclineInput, counted_decline)])
        await adapter.on_started("Agent", "An agent")
        await adapter.on_event(turn_input(FakeAgentTools()))
        await adapter.on_event(turn_input(FakeAgentTools()))

        assert calls == [DECLINED, DECLINED]
        assert len(google_model.requests) == 2

    async def test_one_rooms_decline_does_not_stop_another_rooms_model_loop(
        self, google_model: GoogleModelPeer
    ) -> None:
        class OtherInput(BaseModel):
            """Perform work in the other room."""

        completed: list[str] = []

        @declares_turn_effect(TurnEffect.ACT)
        async def other() -> str:
            completed.append(FOLLOWUP)
            return FOLLOWUP

        work = GatedWork()
        google_model.responses.extend(
            [
                batch(("decline", {}), ("work", {})),
                batch(("other", {})),
                text_reply(FOLLOWUP),
            ]
        )
        adapter = GoogleADKAdapter(
            additional_tools=[
                (DeclineInput, decline),
                (WorkInput, work),
                (OtherInput, other),
            ]
        )
        await adapter.on_started("Agent", "An agent")
        first = asyncio.create_task(
            adapter.on_turn(
                [],
                FakeAgentTools(),
                [],
                None,
                None,
                is_session_bootstrap=True,
                room_id="first",
            )
        )
        try:
            await work.entered.wait()
            await adapter.on_turn(
                [],
                FakeAgentTools(),
                [],
                None,
                None,
                is_session_bootstrap=True,
                room_id="other",
            )
            assert completed == [FOLLOWUP]
            work.release.set()
            await first
        finally:
            work.release.set()
            await asyncio.gather(first, return_exceptions=True)

        assert work.values == [1]
        assert len(google_model.requests) == 3
