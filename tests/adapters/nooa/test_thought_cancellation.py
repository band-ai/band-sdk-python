"""Separate unscoped fake-tool cancellation from the production post-scope guard."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from nooa.unifiedllm import FakeLLMClient

from band.adapters.nooa import NooaAdapter
from band.core.types import Emit
from band.runtime.execution import ExecutionContext
from band.runtime.tools.agent import AgentTools
from band.testing.nooa import code_turn, room_agent_class
from tests.adapters.nooa.kit import ROOM_ID, carrying, room_message
from tests.adapters.nooa.resources import BlockedThoughtTools


class TestThoughtCancellation:
    @pytest.mark.parametrize(
        "scoped", [False, True], ids=["unscoped-fake", "real-scoped"]
    )
    async def test_cancelled_thought_publication_respects_execution_scope(
        self, scoped: bool, mock_rest_client: MagicMock
    ) -> None:
        entered = asyncio.Event()
        llm = FakeLLMClient(
            [
                code_turn(
                    "self.entered.set()\nawait asyncio.Event().wait()",
                    reasoning="Working on the request.",
                )
            ]
        )
        adapter = NooaAdapter(
            agent=room_agent_class(llm, namespace=carrying("entered", entered)),
            emit={Emit.THOUGHTS},
        )
        await adapter.on_started("Agent", "A test agent")
        ctx = ExecutionContext(ROOM_ID, MagicMock(rest=mock_rest_client), AsyncMock())
        await ctx._begin_scope()
        fake_tools = BlockedThoughtTools()
        tools = AgentTools.from_context(ctx) if scoped else fake_tools
        task = asyncio.create_task(
            adapter.on_message(
                room_message(),
                tools,
                [],
                None,
                None,
                is_session_bootstrap=True,
                room_id=ROOM_ID,
            )
        )

        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            if scoped:
                assert ctx.current_scope is not None
                ctx.current_scope.terminate(shutdown=True)
            task.cancel()

            if not scoped:
                await asyncio.wait_for(fake_tools.posting.wait(), timeout=5)
                assert not task.done()
                fake_tools.release.set()

            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=5)
            mock_rest_client.agent_api_events.create_agent_chat_event.assert_not_awaited()
        finally:
            fake_tools.release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
