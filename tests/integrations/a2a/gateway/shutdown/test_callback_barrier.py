"""Actual gateway setup and sends settle before registered resources close."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from band.integrations.a2a.gateway import A2AGatewayAdapter, A2AGatewayAdapterConfig
from tests.adaptercleanup import HostKind, host_for, resource_peer
from tests.integrations.a2a.gateway.helpers import make_peer, peers_page
from tests.integrations.a2a.gateway.serverkit import hello_message_body
from tests.integrations.acp.serversettlement.kit import held_finalizer


class TestCallbackBarrier:
    @pytest.mark.parametrize("kind", list(HostKind))
    @pytest.mark.parametrize("stage", ["setup", "send"])
    async def test_host_waits_for_gateway_work_even_before_pending_registration(
        self, mock_rest_client: MagicMock, stage: str, kind: HostKind
    ) -> None:
        mock_rest_client.agent_api_peers.list_agent_peers = AsyncMock(
            return_value=peers_page([make_peer("peer", "Weather Agent")])
        )
        adapter = A2AGatewayAdapter(
            A2AGatewayAdapterConfig(port=0), rest_client=mock_rest_client
        )
        async with (
            resource_peer() as resource,
            host_for(adapter, kind) as host,
        ):
            adapter.add_cleanup(resource.aclose)
            await host.start()
            assert adapter._server is not None
            url = f"http://127.0.0.1:{adapter._server.bound_port}/agents/weather-agent/message:send"
            async with (
                held_finalizer() as work,
                httpx.AsyncClient(timeout=None) as client,
            ):
                if stage == "setup":
                    mock_rest_client.agent_api_participants.add_agent_chat_participant.side_effect = work.run
                else:
                    mock_rest_client.agent_api_messages.create_agent_chat_message.side_effect = work.run
                request = asyncio.create_task(
                    client.post(
                        url,
                        headers={"A2A-Version": "1.0"},
                        json={
                            **hello_message_body(),
                            "configuration": {"returnImmediately": True},
                        },
                    )
                )
                await work.entered.wait()
                if stage == "setup":
                    assert not adapter._pending_tasks
                else:
                    (await request).raise_for_status()
                stopping = asyncio.create_task(host.stop())
                try:
                    await work.closing.wait()
                    assert not stopping.done()
                    assert (await resource.get("/resource")).status_code == 200
                finally:
                    work.release.set()
                    await stopping
                    # Setup may never publish a task, so no protocol result is promised.
                    request.cancel()
                    await asyncio.gather(request, return_exceptions=True)
                assert work.closed.is_set()
                assert resource.is_closed
