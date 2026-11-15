from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from acp.exceptions import RequestError
from band_sdk_core import AgentFailure

from band.agent import Agent
from band.runtime.sessionconfig import SessionConfig
from tests.integrations.acp.serverfailures.kit import prompt, running_server
from tests.integrations.acp.serverkit import (
    deliver_server_message,
    failure_event,
    wait_for_pending_prompt,
)


class TestPromptCancellation:
    @pytest.mark.asyncio
    async def test_room_cleanup_fails_pending_prompt(
        self, mock_rest_client: MagicMock
    ) -> None:
        adapter, server = running_server(mock_rest_client)
        task = prompt(server)
        await wait_for_pending_prompt(adapter, "room-123")
        await adapter.on_cleanup("room-123")
        with pytest.raises(RequestError) as raised:
            await task
        assert raised.value.code == -32603
        assert (
            raised.value.data
            == AgentFailure(
                "band", "Band room closed before prompt completed."
            ).to_extension_data()
        )

    @pytest.mark.asyncio
    async def test_agent_stop_fails_pending_prompt(
        self, mock_rest_client: MagicMock
    ) -> None:
        adapter, server = running_server(mock_rest_client)
        runtime = MagicMock()
        runtime.agent_name = "TestBot"
        runtime.agent_description = "ACP test agent"
        runtime.feature_flags = None
        runtime.session_config = SessionConfig()
        runtime.initialize = AsyncMock()
        runtime.start = AsyncMock()
        runtime.stop = AsyncMock(return_value=True)
        agent = Agent(runtime=runtime, adapter=adapter)
        await agent.start()

        task = prompt(server)
        await wait_for_pending_prompt(adapter, "room-123")
        await agent.stop()
        with pytest.raises(RequestError) as raised:
            await task
        assert raised.value.code == -32603
        assert (
            raised.value.data
            == AgentFailure(
                "band", "Band agent stopped before prompt completed."
            ).to_extension_data()
        )

    @pytest.mark.asyncio
    async def test_cancel_before_error_keeps_cancelled_outcome(
        self,
        mock_rest_client: MagicMock,
    ) -> None:
        adapter, server = running_server(mock_rest_client)
        task = prompt(server)
        await wait_for_pending_prompt(adapter, "room-123")
        await adapter.cancel_prompt("session-1")
        await deliver_server_message(adapter, failure_event())
        assert (await task).stop_reason == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_during_participant_lookup_does_not_post_prompt(
        self,
        mock_rest_client: MagicMock,
    ) -> None:
        adapter, server = running_server(mock_rest_client)
        entered = asyncio.Event()
        release = asyncio.Event()
        participants = mock_rest_client.agent_api_participants.list_agent_chat_participants.return_value

        async def delayed_participants(**kwargs: object) -> object:
            entered.set()
            await release.wait()
            return participants

        mock_rest_client.agent_api_participants.list_agent_chat_participants = (
            AsyncMock(side_effect=delayed_participants)
        )
        task = prompt(server)
        await entered.wait()
        await adapter.cancel_prompt("session-1")
        try:
            assert (
                await asyncio.wait_for(asyncio.shield(task), 1)
            ).stop_reason == "cancelled"
        finally:
            release.set()
            if not task.done():
                await task
        mock_rest_client.agent_api_messages.create_agent_chat_message.assert_not_awaited()
