"""A dead CLI cannot reopen its room while a custom MCP effect still runs."""

from __future__ import annotations

import asyncio

import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapterConfig
from band.core.exceptions import ProviderUnavailableError
from band.core.retention import RoomRetentionConfig
from tests.adapters.claude_sdk.helpers import OpenRoom
from tests.adapters.claude_sdk.lifecycle.effects import HeldEffect, HeldEffectInput


@pytest.mark.looptime
class TestCallbackRecovery:
    async def test_process_exit_does_not_allow_reuse_until_custom_effect_returns(
        self, claude_room: OpenRoom
    ) -> None:
        effect = HeldEffect()
        room = await claude_room(
            ClaudeSDKAdapterConfig(
                retention=RoomRetentionConfig(transition_timeout_s=1)
            ),
            additional_tools=[(HeldEffectInput, effect)],
        )
        room.claude.script(
            [room.model_call("heldeffect")], [room.model_reply("recovered")]
        )
        sending = room.send_in_background("perform effect")
        try:
            await effect.started.wait()
            session = room.session_manager.session(room.room_id)
            assert session is not None
            old = room.claude.sessions[0]
            old.die()
            await asyncio.gather(sending, return_exceptions=True)
            with pytest.raises(ProviderUnavailableError):
                await room.send("must not run")
            assert not effect.finished.is_set()
            assert len(room.claude.sessions) == 1
            effect.release.set()
            await effect.finished.wait()
            await session.retained.wait_released(generation=session.generation)
            await room.send("fresh input")
            assert room.chat == ["recovered"]
            assert len(room.claude.sessions) == 2
        finally:
            effect.release.set()
            await asyncio.gather(sending, return_exceptions=True)
