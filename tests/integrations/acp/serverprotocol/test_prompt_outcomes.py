"""Real ACP client/server exchanges for session lifecycle and failure outcomes."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from acp import text_block
from acp.exceptions import RequestError
from band_sdk_core import AgentFailure

from band.core.types import MessageType
from band.integrations.acp.push_handler import ACPPushHandler
from band.integrations.acp.server_adapter import BandACPServerAdapter
from tests.integrations.acp.protocolkit import protocol_pair
from tests.integrations.acp.serverkit import (
    deliver_server_message,
    failure_event,
    make_platform_message,
    wait_for_pending_prompt,
)


class TestPromptOutcomes:
    @pytest.mark.asyncio
    async def test_failure_and_concurrent_prompt_over_acp_wire(
        self,
        mock_rest_client: MagicMock,
    ) -> None:
        adapter = BandACPServerAdapter(rest_client=mock_rest_client)
        adapter._session_to_room["session-1"] = "room-123"
        adapter._room_to_session["room-123"] = "session-1"
        adapter.set_push_handler(ACPPushHandler(adapter))
        failure = AgentFailure("peer", "Broken", detail={"reason": "upstream"})

        async with protocol_pair(adapter) as (conn, client, _server):
            first = asyncio.create_task(
                conn.prompt(session_id="session-1", prompt=[text_block("First")])
            )
            await wait_for_pending_prompt(adapter, "room-123")

            with pytest.raises(RequestError) as concurrent:
                await conn.prompt(session_id="session-1", prompt=[text_block("Second")])
            assert concurrent.value.code == -32602

            await deliver_server_message(
                adapter,
                failure_event({"failure": failure.to_dict()}, content="Broken"),
            )
            with pytest.raises(RequestError) as rejected:
                await first
            assert rejected.value.code == -32603
            assert rejected.value.data == failure.to_extension_data()
            await client.wait_for_updates(1)
            received = client.updates[0]
            assert received.session_id == "session-1"
            assert received.chunk.content.text == "[Error] Broken"
            assert received.chunk.field_meta == failure.to_extension_data()
            assert received.notification_fields == {}

            await deliver_server_message(
                adapter,
                failure_event({"failure": failure.to_dict()}, content="Broken again"),
            )
            await client.wait_for_updates(2)
            received = client.updates[1]
            assert received.session_id == "session-1"
            assert received.chunk.field_meta == failure.to_extension_data()
            assert received.notification_fields == {}

            cancelled = asyncio.create_task(
                conn.prompt(session_id="session-1", prompt=[text_block("Cancel")])
            )
            await wait_for_pending_prompt(adapter, "room-123")
            await conn.cancel(session_id="session-1")
            assert (await cancelled).stop_reason == "cancelled"

            completed = asyncio.create_task(
                conn.prompt(session_id="session-1", prompt=[text_block("Done")])
            )
            await wait_for_pending_prompt(adapter, "room-123")
            await deliver_server_message(
                adapter, make_platform_message("Done", message_type=MessageType.TEXT)
            )
            assert (await completed).stop_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_interleaved_rooms_keep_failure_and_completion_separate_over_acp_wire(
        self, mock_rest_client: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "band.integrations.acp.server_adapter._PROMPT_COMPLETION_GRACE_SECONDS", 1.0
        )
        adapter = BandACPServerAdapter(rest_client=mock_rest_client)
        failure = AgentFailure(
            "peer",
            "Bearer secret-token",
            detail={
                "token=sk-private": {"nested": "token=private-value"},
                "headers": {"Authorization": "ApiKey sk-live-value"},
                "token_count": 12,
            },
        )
        expected = AgentFailure(
            "peer",
            "Bearer [REDACTED]",
            detail={
                "token=[REDACTED]": {"nested": "token=[REDACTED]"},
                "headers": {"Authorization": "[REDACTED]"},
                "token_count": 12,
            },
        ).to_extension_data()

        async with protocol_pair(adapter) as (conn, client, _server):
            first_session = (await conn.new_session(cwd="/workspace/first")).session_id
            second_session = (
                await conn.new_session(cwd="/workspace/second")
            ).session_id
            first_room = adapter._session_to_room[first_session]
            second_room = adapter._session_to_room[second_session]
            first = asyncio.create_task(
                conn.prompt(session_id=first_session, prompt=[text_block("First")])
            )
            second = asyncio.create_task(
                conn.prompt(session_id=second_session, prompt=[text_block("Second")])
            )
            await wait_for_pending_prompt(adapter, first_room)
            await wait_for_pending_prompt(adapter, second_room)
            posts = mock_rest_client.agent_api_messages.create_agent_chat_message
            async with asyncio.timeout(5):
                while posts.await_count < 2:
                    await asyncio.sleep(0)
            assert posts.await_count == 2
            assert {call.kwargs["chat_id"] for call in posts.await_args_list} == {
                first_room,
                second_room,
            }

            await deliver_server_message(
                adapter, make_platform_message("Partial", room_id=first_room)
            )
            await client.wait_for_updates(1)
            assert client.updates[0].session_id == first_session
            assert client.updates[0].chunk.content.text == "Partial"

            await deliver_server_message(
                adapter,
                failure_event(
                    {"failure": failure.to_dict()},
                    content="token=room-secret",
                    room_id=first_room,
                ),
            )
            with pytest.raises(RequestError) as rejected:
                await first
            assert rejected.value.code == -32603
            assert rejected.value.data == expected
            assert not second.done()
            await client.wait_for_updates(2)
            assert client.updates[1].session_id == first_session
            assert client.updates[1].chunk.content.text == "[Error] token=[REDACTED]"
            assert client.updates[1].chunk.field_meta == expected

            await deliver_server_message(
                adapter, make_platform_message("Second done", room_id=second_room)
            )
            assert (await second).stop_reason == "end_turn"
            await client.wait_for_updates(3)
            assert client.updates[2].session_id == second_session
            assert client.updates[2].chunk.content.text == "Second done"
