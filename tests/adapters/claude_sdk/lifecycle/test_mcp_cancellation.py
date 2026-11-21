"""Final disposal cancels and joins Claude's active HTTP tool request."""

from __future__ import annotations

import asyncio

import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapterConfig
from band.core.exceptions import CleanupIncompleteError
from band.runtime.custom_tools import get_custom_tool_name
from tests.adapters.claude_sdk.helpers import REPORTING_BUDGET, OpenRoom
from tests.adapters.claude_sdk.lifecycle.effects import HeldEffectInput
from tests.adapters.hold import Hold


class TestMcpCancellation:
    @pytest.mark.looptime
    async def test_shutdown_rejoins_the_tool_finalizer_before_releasing_resources(
        self, claude_room: OpenRoom
    ) -> None:
        active, finalizing = Hold(), Hold()
        settled = asyncio.Event()

        async def tool(args: HeldEffectInput) -> str:
            try:
                await active.wait()
            except asyncio.CancelledError:
                await finalizing.wait()
                settled.set()
                raise
            return "completed normally"

        room = await claude_room(
            ClaudeSDKAdapterConfig(retention=REPORTING_BUDGET, turn_settle_timeout_s=0),
            additional_tools=[(HeldEffectInput, tool)],
        )
        room.claude.script([room.model_call(get_custom_tool_name(HeldEffectInput))])
        sending = room.send_in_background("perform effect")
        backend = room.mcp_backend
        try:
            await active.arrived()
            [cli] = room.claude.sessions
            with pytest.raises(CleanupIncompleteError):
                await room.leave()

            assert finalizing.reached.is_set()
            with pytest.raises(CleanupIncompleteError):
                await room.adapter.cleanup_all()

            assert not settled.is_set()
            assert cli.alive and cli.close_calls == 0
            assert backend.is_running and not room.disposed
        finally:
            active.released.set()
            finalizing.released.set()
            await asyncio.gather(sending, return_exceptions=True)
            await room.adapter.cleanup_all()

        assert settled.is_set()
        assert room.disposed
        assert sending.cancelled()
        assert not cli.alive
        assert not backend.is_running
