"""A2AGatewayAdapter showcase smoke -- driving a live gateway with the
official a2a-sdk reference client, independent of ``A2AAdapter``.

A2A is a protocol bridge, not an LLM-agent adapter (listed in
``NON_AGENT_ADAPTERS``), so the gateway itself is built bespoke and handed to
``running_provisioned_agent``, like ``test_parlant.py``. The *target* peer it
exposes, though, is an ordinary Anthropic-backed Band agent provisioned
through ``@with_adapters`` -- which also derives this smoke's home lane
(``core``), so no ``@lane`` pin is needed here.

Drives the gateway with a real ``a2a.client.Client`` (the a2a-sdk reference
client), not our own ``A2AAdapter`` -- this validates the gateway's JSON-RPC
server against the actual upstream implementation, independent of any bug
the two could otherwise share.

Run with:
    E2E_TESTS_ENABLED=true uv run pytest \\
        tests/e2e/baseline/smoke/adapters/test_a2a_gateway.py -v -s --no-cov
"""

from __future__ import annotations

import httpx
import pytest
from a2a.client import ClientConfig, ClientFactory
from a2a.helpers import get_message_text, new_text_message
from a2a.types import Role, SendMessageRequest

from band.integrations.a2a.adapter import _SSE_READ_TIMEOUT_S
from band.integrations.a2a.gateway import A2AGatewayAdapter

from tests.e2e.baseline.agents import Adapter, with_adapters
from tests.e2e.baseline.flaky import flaky_infra
from tests.e2e.baseline.toolkit.provisioning import (
    ProvisionedAgent,
    ResourceManager,
    running_provisioned_agent,
)
from tests.ports import reserve_port

_SHORT = "You are a friendly assistant in a chat room. Reply in one short sentence."


@with_adapters(Adapter.ANTHROPIC, prompt=_SHORT)
@flaky_infra("retry a transient live-turn timeout; assertion failures fail loud")
@pytest.mark.timeout(extra=120)
@pytest.mark.asyncio(loop_scope="session")
async def test_gateway_serves_a_real_a2a_client(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
) -> None:
    """A raw a2a-sdk client drives the gateway's JSON-RPC endpoint for a live
    Band peer and receives its real reply back over A2A."""
    port = reserve_port()
    gateway = A2AGatewayAdapter(gateway_url=f"http://127.0.0.1:{port}", port=port)

    async with running_provisioned_agent(gateway, resource_manager, label="a2a-gw"):
        # The Anthropic peer's own id is a stable alias the gateway always
        # serves (alongside its slug), so the client needs no slug lookup.
        # Same generous-but-bounded read timeout as A2AAdapter itself (see
        # its module docstring): httpx's default 5s fires on the normal,
        # multi-second gap between SSE events during a real LLM turn.
        http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, read=_SSE_READ_TIMEOUT_S)
        )
        factory = ClientFactory(ClientConfig(streaming=True, httpx_client=http_client))
        client = await factory.create_from_url(
            f"http://127.0.0.1:{port}/agents/{agent.id}"
        )
        reply_text = ""
        try:
            message = new_text_message("Please say hello.", role=Role.ROLE_USER)
            async for event in client.send_message(SendMessageRequest(message=message)):
                if event.HasField(
                    "status_update"
                ) and event.status_update.status.HasField("message"):
                    text = get_message_text(event.status_update.status.message)
                    if text:
                        reply_text = text
        finally:
            await client.close()
            await http_client.aclose()

    assert reply_text, "expected a reply relayed from the live Band peer over A2A"
