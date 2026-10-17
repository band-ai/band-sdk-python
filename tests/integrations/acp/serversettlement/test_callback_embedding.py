"""The embedding connection barrier precedes host-owned resource release."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from acp import text_block

from band.integrations.acp.server_adapter import BandACPServerAdapter
from tests.adaptercleanup import HostKind, host_for, resource_peer
from tests.integrations.acp.protocolkit import protocol_pair
from tests.integrations.acp.serversettlement.kit import held_finalizer


class TestCallbackEmbedding:
    async def test_completed_external_connection_close_precedes_host_disposal(
        self, mock_rest_client: MagicMock
    ) -> None:
        adapter = BandACPServerAdapter(rest_client=mock_rest_client)
        async with (
            resource_peer() as resource,
            host_for(adapter, HostKind.ONESHOT) as host,
        ):
            adapter.add_cleanup(resource.aclose)
            await host.start()
            async with protocol_pair(adapter) as (connection, _client, server):
                session = await connection.new_session(cwd=".", mcp_servers=[])
                async with held_finalizer() as send:
                    mock_rest_client.agent_api_messages.create_agent_chat_message.side_effect = send.run
                    prompt = asyncio.create_task(
                        connection.prompt(
                            session_id=session.session_id,
                            prompt=[text_block("Pending")],
                        )
                    )
                    await send.entered.wait()
                    closing = asyncio.create_task(server.close())
                    try:
                        await send.closing.wait()
                        assert not closing.done()
                        assert (await resource.get("/resource")).status_code == 200
                    finally:
                        send.release.set()
                        await closing
                        await connection.close()
                        await asyncio.gather(prompt, return_exceptions=True)
            await host.stop()
            assert send.closed.is_set()
            assert resource.is_closed
