"""A turn waits for the Band MCP server only for the transition budget."""

from __future__ import annotations

import asyncio

import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapterConfig
from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE
from tests.adapters.claude_sdk.helpers import REPORTING_BUDGET, OpenRoom
from tests.mcpbackends import FakeBandMCPBackend, backends_created_by


@pytest.mark.looptime
class TestMcpStartBudget:
    async def test_a_server_start_that_never_finishes_fails_the_turn_in_the_budget(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room(ClaudeSDKAdapterConfig(retention=REPORTING_BUDGET))
        room.claude.script([room.model_reply("Hello.")])
        await room.send("hi")
        await room.crash_band_server()
        start_released = asyncio.Event()

        async def held_start() -> FakeBandMCPBackend:
            await start_released.wait()
            return FakeBandMCPBackend()

        with backends_created_by(then=held_start):
            try:
                with pytest.raises(TimeoutError):
                    await room.send("is the server back?")
            finally:
                start_released.set()

        # Budget abandons the wait: the prior reply stands, the CLI is not
        # re-prompted, and the failed turn reports once.
        assert room.chat == ["Hello."]
        assert len(room.claude.prompts) == 1
        assert room.failures == [GENERIC_PROVIDER_FAILURE_MESSAGE]
