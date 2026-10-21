"""Prompt results wait for cancelled REST-send finalizers."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from acp import text_block
from acp.exceptions import RequestError

from band.integrations.acp.server_adapter import BandACPServerAdapter
from tests.integrations.acp.protocolkit import protocol_pair
from tests.integrations.acp.serversettlement.kit import held_finalizer


class TestSendSettlement:
    async def test_successful_connection_close_joins_request_children(
        self, mock_rest_client: MagicMock
    ) -> None:
        adapter = BandACPServerAdapter(rest_client=mock_rest_client)
        async with protocol_pair(adapter) as (connection, _client, server):
            session = await connection.new_session(cwd=".", mcp_servers=[])
            async with held_finalizer() as send:
                mock_rest_client.agent_api_messages.create_agent_chat_message.side_effect = send.run
                prompt = asyncio.create_task(
                    connection.prompt(
                        session_id=session.session_id, prompt=[text_block("Pending")]
                    )
                )
                await send.entered.wait()
                closing = asyncio.create_task(server.close())
                await send.closing.wait()
                assert not closing.done()
                send.release.set()
                await closing
                await connection.close()
                await asyncio.gather(prompt, return_exceptions=True)
                assert send.closed.is_set()

    @pytest.mark.parametrize("terminal", ["cancel", "close", "shutdown"])
    async def test_terminal_prompt_joins_send_after_mapping_removed(
        self, mock_rest_client: MagicMock, terminal: str
    ) -> None:
        adapter = BandACPServerAdapter(rest_client=mock_rest_client)
        async with protocol_pair(adapter) as (connection, _client, _server):
            session = await connection.new_session(cwd=".", mcp_servers=[])
            async with held_finalizer() as send:
                mock_rest_client.agent_api_messages.create_agent_chat_message.side_effect = send.run
                prompt = asyncio.create_task(
                    connection.prompt(
                        session_id=session.session_id, prompt=[text_block("Pending")]
                    )
                )
                await send.entered.wait()
                match terminal:
                    case "cancel":
                        await connection.cancel(session_id=session.session_id)
                    case "close":
                        await connection.close_session(session_id=session.session_id)
                    case "shutdown":
                        await adapter.cleanup_all()
                await send.closing.wait()
                await connection.list_sessions()
                assert not prompt.done()
                send.release.set()
                if terminal == "shutdown":
                    with pytest.raises(RequestError):
                        await prompt
                else:
                    assert (await prompt).stop_reason == "cancelled"
                assert send.closed.is_set()
