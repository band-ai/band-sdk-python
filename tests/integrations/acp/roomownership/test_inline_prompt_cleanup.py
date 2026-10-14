"""Final cleanup settles and cancels ordinary ACP prompts through their owner."""

from __future__ import annotations

import asyncio

import pytest

from band.core.retention import RoomRetentionConfig
from tests.adapters.waiting import until
from tests.integrations.acp.acp_toolkit import (
    DEFAULT_ROOM,
    FakeACPAgent,
    acp_adapter,
    fake_agent_config,
)
from tests.integrations.acp.roomownership.kit import TEST_GUARD_S, holds_prompt


class TestInlinePromptCleanup:
    @pytest.mark.parametrize("cleanup", ["stop", "leave"])
    async def test_cleanup_cancels_a_prompt_after_its_settle_grace(
        self, cleanup: str
    ) -> None:
        agent = FakeACPAgent()
        held = holds_prompt(agent, 2)
        config = fake_agent_config(
            turn_settle_timeout_s=0.01,
            retention=RoomRetentionConfig(transition_timeout_s=TEST_GUARD_S),
        )
        async with acp_adapter(agent, config) as session:
            await session.send("bootstrap")
            runtime = session.runtime()
            native_id = session.session_id(DEFAULT_ROOM)
            prompt = asyncio.create_task(session.send("held"))
            await asyncio.wait_for(held.received.wait(), TEST_GUARD_S)
            try:
                match cleanup:
                    case "stop":
                        await session.adapter.stop()
                    case "leave":
                        await session.adapter.on_cleanup(DEFAULT_ROOM)

                with pytest.raises(asyncio.CancelledError):
                    await prompt
                assert agent.cancelled_sessions == [native_id]
                assert runtime.closed
            finally:
                held.release.set()
                await asyncio.gather(prompt, return_exceptions=True)

    @pytest.mark.parametrize("cleanup", ["stop", "leave"])
    async def test_cleanup_allows_a_prompt_to_finish_within_its_grace(
        self, cleanup: str
    ) -> None:
        agent = FakeACPAgent()
        held = holds_prompt(agent, 2)
        config = fake_agent_config(
            turn_settle_timeout_s=TEST_GUARD_S,
            retention=RoomRetentionConfig(transition_timeout_s=TEST_GUARD_S),
        )
        async with acp_adapter(agent, config) as session:
            await session.send("bootstrap")
            runtime = session.runtime()
            room = session.room()
            prompt = asyncio.create_task(session.send("held"))
            await asyncio.wait_for(held.received.wait(), TEST_GUARD_S)
            match cleanup:
                case "stop":
                    closing = asyncio.create_task(session.adapter.stop())
                case "leave":
                    closing = asyncio.create_task(
                        session.adapter.on_cleanup(DEFAULT_ROOM)
                    )
            try:
                await until(
                    lambda: room.retained.closing,
                    failure="cleanup never closed room admission",
                )
                held.release.set()
                async with asyncio.timeout(TEST_GUARD_S):
                    reply = await prompt
                    await closing

                assert reply.errors == []
                assert agent.cancelled_sessions == []
                assert runtime.closed
            finally:
                held.release.set()
                await asyncio.gather(prompt, closing, return_exceptions=True)
