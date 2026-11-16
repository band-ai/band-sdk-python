"""Two Band identities on one self-hosted Letta server stay isolated.

Letta dedupes MCP-discovered tool rows by ``(name, organization_id)``. The
adapter therefore scopes each Band identity to its own Letta organization and
user; without that, a second identity's MCP registration would repoint the
first identity's ``band_send_message`` row at its own server, and the first
agent's tool calls would run as the wrong Band identity.
``test_letta.py`` covers one identity started concurrently; this covers two.

Each identity's org is read through an independent client scoped by the public
``resolve_org_scoped_headers``, so a mis-scoped adapter cannot vouch for itself.
Letta Cloud has no org scoping, so the test needs a self-hosted server.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import pytest

from band.adapters.letta import LettaAdapter
from band.integrations.letta import SEND_MESSAGE_TOOL_NAMES
from band.integrations.letta.config import is_letta_cloud_url
from band.integrations.letta.orgscope import resolve_org_scoped_headers
from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.toolkit.provisioning import AdapterCell, ProvisionedAgent

SEND_MESSAGE_TOOL = SEND_MESSAGE_TOOL_NAMES[0]


@asynccontextmanager
async def org_client(
    adapter: LettaAdapter, identity: ProvisionedAgent
) -> AsyncGenerator[Any, None]:
    """A Letta client scoped to the org ``identity``'s adapter should use."""
    from letta_client import AsyncLetta  # noqa: PLC0415 -- the letta extra is lane-only

    config = adapter.config
    headers = await resolve_org_scoped_headers(
        base_url=config.base_url,
        agent_name=identity.name,
        bearer_token=config.provider_key,
    )
    async with AsyncLetta(
        base_url=config.base_url,
        api_key=config.provider_key,
        default_headers=headers,
    ) as client:
        yield client


async def create_agent(client: Any, adapter: LettaAdapter) -> str:
    """Create an agent with Letta's base tools, which a fresh org lacks unseeded."""
    config = adapter.config
    agent = await client.agents.create(
        include_base_tools=True,
        model=config.model,
        embedding=config.embedding,
        model_settings=config.model_settings,
    )
    return agent.id


async def send_tool_server_id(client: Any) -> object:
    """The MCP server id on the send-tool row ``client``'s org resolves."""
    rows = [tool async for tool in client.tools.list(name=SEND_MESSAGE_TOOL)]
    assert len(rows) == 1, f"expected one {SEND_MESSAGE_TOOL!r} row, got {len(rows)}"
    metadata = rows[0].metadata
    assert metadata is not None, f"{SEND_MESSAGE_TOOL!r} row carries no MCP metadata"
    return metadata["mcp"]["server_id"]


async def visible_agent_ids(client: Any) -> set[str]:
    """The Letta agents ``client``'s org lists."""
    return {agent.id async for agent in client.agents.list()}


class TestLettaIdentityIsolation:
    @per_adapter(Adapter.LETTA)
    @pytest.mark.timeout(extra=120)  # two Letta-side MCP registrations
    @pytest.mark.asyncio(loop_scope="session")
    async def test_two_identities_keep_separate_orgs_tools_and_agents(
        self, cell: AdapterCell
    ) -> None:
        first, second = cell.build(), cell.build()
        assert isinstance(first, LettaAdapter) and isinstance(second, LettaAdapter)
        if is_letta_cloud_url(first.config.base_url):
            pytest.skip(
                "Letta Cloud has no org scoping; set a self-hosted LETTA_BASE_URL"
            )
        first_identity = await cell.provision(label="letta-iso-a")
        second_identity = await cell.provision(label="letta-iso-b")
        async with AsyncExitStack() as stack:
            await first.on_started(first_identity.name, first_identity.description)
            stack.push_async_callback(first.cleanup_all)
            first_org = await stack.enter_async_context(
                org_client(first, first_identity)
            )
            # Created before any tools.list, which seeds base tools itself.
            first_agent = await create_agent(first_org, first)
            stack.push_async_callback(first_org.agents.delete, first_agent)
            first_server_before = await send_tool_server_id(first_org)

            await second.on_started(second_identity.name, second_identity.description)
            stack.push_async_callback(second.cleanup_all)
            second_org = await stack.enter_async_context(
                org_client(second, second_identity)
            )
            second_agent = await create_agent(second_org, second)
            stack.push_async_callback(second_org.agents.delete, second_agent)
            second_server = await send_tool_server_id(second_org)

            assert await send_tool_server_id(first_org) == first_server_before
            assert first_server_before != second_server
            assert await visible_agent_ids(first_org) == {first_agent}
            assert await visible_agent_ids(second_org) == {second_agent}
