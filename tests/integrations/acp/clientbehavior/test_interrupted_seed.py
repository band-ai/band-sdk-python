from __future__ import annotations

import asyncio

import pytest

from band.converters.acp_client import ACP_CLIENT_RESUME_RECORD
from tests.integrations.acp.acp_toolkit import (
    FakeACPAgent,
    TranscriptTools,
    acp_adapter,
)
from tests.integrations.acp.acp_toolkit.agent import Gate
from tests.integrations.acp.clientbehavior.kit import REPLAY_HEADER_LINE

EARLIER = {
    "id": "m1",
    "message_type": "text",
    "sender_id": "user-marco",
    "sender_type": "User",
    "sender_name": "Marco",
    "content": "My favorite color is blue.",
}


class TestInterruptedSeed:
    @pytest.mark.asyncio
    async def test_an_interrupted_seeding_prompt_is_unrecorded_and_replayed_again(
        self,
    ) -> None:
        """Only a completed seeding prompt seeds the session: an interrupted one
        posts no resume record, and the next prompt still carries the replay."""
        first_prompt = Gate()
        agent = FakeACPAgent()

        @agent.on_prompt
        async def _hold_first_prompt(a: FakeACPAgent, sid: str) -> None:
            if not first_prompt.received.is_set():
                await first_prompt.hold()
            await a.say(sid, "Blue.")

        interrupted_tools = TranscriptTools()
        async with acp_adapter(agent) as session:
            seeding = asyncio.create_task(
                session.send("Hi.", room_context=[EARLIER], tools=interrupted_tools)
            )
            await first_prompt.received.wait()
            seeding.cancel()
            with pytest.raises(asyncio.CancelledError):
                await seeding
            first_prompt.release.set()
            await session.send("What is my favorite color?", room_context=[EARLIER])

        seed, retry = agent.prompt_texts()
        assert REPLAY_HEADER_LINE in seed
        assert ACP_CLIENT_RESUME_RECORD.candidates(interrupted_tools.events_sent) == {}
        assert REPLAY_HEADER_LINE in retry
        assert "[Marco]: My favorite color is blue." in retry
