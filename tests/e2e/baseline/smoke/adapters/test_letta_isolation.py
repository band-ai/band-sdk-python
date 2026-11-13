"""Two Band identities on one self-hosted Letta server stay isolated.

Letta dedupes MCP-discovered tool rows by ``(name, organization_id)``. The
adapter therefore scopes each Band identity to its own Letta organization and
user; without that, a second identity's MCP registration would repoint the
first identity's ``band_send_message`` row at its own server, and the first
agent's tool calls would run as the wrong Band identity.
``test_letta.py`` covers one identity started concurrently; this covers two.
"""

from __future__ import annotations

import pytest

from band.adapters.letta import LettaAdapter
from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.toolkit.provisioning import AdapterCell


async def send_tool_server_id(adapter: LettaAdapter) -> object:
    """The MCP server id on the send-tool row ``adapter``'s Letta org resolves."""
    name = adapter._mcp.send_message_tool
    rows = [tool async for tool in adapter._client.tools.list(name=name)]
    assert len(rows) == 1, f"expected one {name!r} row in this org, got {len(rows)}"
    metadata = rows[0].metadata
    assert metadata is not None, f"{name!r} row carries no MCP metadata"
    return metadata["mcp"]["server_id"]


async def visible_agent_ids(adapter: LettaAdapter) -> set[str]:
    """The Letta agents ``adapter``'s org-scoped client can list."""
    return {agent.id async for agent in adapter._client.agents.list()}


class TestLettaIdentityIsolation:
    @per_adapter(Adapter.LETTA)
    @pytest.mark.timeout(extra=120)  # two Letta-side MCP registrations
    @pytest.mark.asyncio(loop_scope="session")
    async def test_two_identities_keep_separate_orgs_tools_and_agents(
        self, cell: AdapterCell
    ) -> None:
        first_identity = await cell.provision(label="letta-iso-a")
        second_identity = await cell.provision(label="letta-iso-b")
        first, second = cell.build(), cell.build()
        assert isinstance(first, LettaAdapter) and isinstance(second, LettaAdapter)
        started: list[LettaAdapter] = []
        created: list[tuple[LettaAdapter, str]] = []
        try:
            await first.on_started(first_identity.name, first_identity.description)
            started.append(first)
            first_server_before = await send_tool_server_id(first)

            await second.on_started(second_identity.name, second_identity.description)
            started.append(second)
            second_server = await send_tool_server_id(second)

            assert (
                first._client.default_headers["user_id"]
                != second._client.default_headers["user_id"]
            )
            assert await send_tool_server_id(first) == first_server_before
            assert first_server_before != second_server

            # Each org is new, so creating an agent proves the base tools were seeded.
            first_agent = await first._create_agent()
            created.append((first, first_agent))
            second_agent = await second._create_agent()
            created.append((second, second_agent))

            assert await visible_agent_ids(first) == {first_agent}
            assert await visible_agent_ids(second) == {second_agent}
        finally:
            for owner, agent_id in created:
                await owner._client.agents.delete(agent_id)
            for adapter in started:
                await adapter.cleanup_all()
