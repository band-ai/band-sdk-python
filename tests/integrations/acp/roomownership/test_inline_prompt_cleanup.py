"""Final cleanup settles and cancels ordinary ACP prompts through their owner."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import CleanupIncompleteError
from band.core.retention import RoomRetentionConfig
from band.integrations.acp.session_config import ACPConfigRequest
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

    async def test_stop_from_config_resolver_preserves_the_requesting_task(
        self,
    ) -> None:
        agent = FakeACPAgent().will_say("settled")
        stop_reported = asyncio.Event()
        release_resolver = asyncio.Event()
        requester_cancellations: list[int] = []

        async def resolve_config(request: ACPConfigRequest) -> None:
            assert request.room_id == DEFAULT_ROOM
            requester = asyncio.current_task()
            assert requester is not None
            try:
                with pytest.raises(CleanupIncompleteError):
                    await session.adapter.stop()
            finally:
                requester_cancellations.append(requester.cancelling())
                stop_reported.set()
            await release_resolver.wait()

        config = fake_agent_config(
            turn_settle_timeout_s=0,
            retention=RoomRetentionConfig(transition_timeout_s=TEST_GUARD_S),
        )
        async with acp_adapter(
            agent, config, resolve_session_config=resolve_config
        ) as session:
            prompt = asyncio.create_task(session.send("configure then stop"))
            try:
                await asyncio.wait_for(stop_reported.wait(), TEST_GUARD_S)
                assert requester_cancellations == [0]
                assert not prompt.done()
                runtime = session.runtime()
                release_resolver.set()
                async with asyncio.timeout(TEST_GUARD_S):
                    reply = await prompt
                    await session.adapter.stop()

                assert reply.errors == []
                assert not prompt.cancelled()
                assert runtime.closed
                assert not session.adapter._rooms
            finally:
                release_resolver.set()
                await asyncio.gather(prompt, return_exceptions=True)
