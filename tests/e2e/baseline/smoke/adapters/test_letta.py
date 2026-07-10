"""Letta showcase smokes — the self-hosted MCP tool path, proven live.

Letta is a matrix adapter (the ``@per_adapter`` scenarios are its primary
coverage); these are the *supplementary* smokes for what is specific to Letta:
the adapter self-hosts a Band MCP server in-process and registers it with the
Letta server, which executes platform tools by calling back into it. Two things
the generic matrix cannot isolate get proven here:

1. the registration health of that callback hop (Letta discovered the band
   tools over the advertised URL — a live MCP round-trip), and
2. that a reply actually travels through the MCP ``band_send_message`` tool —
   with auto-relay disabled, so a dead tool path cannot hide behind the
   adapter relaying plain assistant text.

Run with:
    E2E_TESTS_ENABLED=true BAND_E2E_LANE=letta uv run pytest \\
        tests/e2e/baseline/smoke/adapters/test_letta.py -v -s --no-cov
"""

from __future__ import annotations

import pytest

from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import (
    AdapterCell,
    ResourceManager,
    running_agent,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps


@per_adapter(Adapter.LETTA)
@pytest.mark.timeout(extra=120)  # Letta-side MCP sync stalls are 60s each
@pytest.mark.asyncio(loop_scope="session")
async def test_self_hosted_mcp_registration_health(cell: AdapterCell) -> None:
    """Letta reaches the adapter's self-hosted MCP server and sees its tools.

    Tool discovery is served over the registered URL by the Letta server
    itself, so a non-empty tool list proves the whole callback hop (container →
    advertised host → in-process LocalMCPServer) — not just a config echo. The
    resolved send tool pins the surface the enforcement prompt was built for.
    """
    identity = await cell.provision()
    adapter = cell.build()
    assert adapter.config.mcp.mode == "self_host", (
        "this smoke proves the self-hosted MCP path; unset MCP_SERVER_URL "
        "(it switches the builder to an external band-mcp)"
    )
    with cell.resources.track_running(identity.id):
        async with running_agent(identity, adapter, cell.settings):
            assert adapter._mcp.server_id, "adapter registered no MCP server with Letta"
            assert adapter._mcp.tool_ids, (
                "Letta discovered no tools from the self-hosted MCP server — "
                "the advertised URL is not reachable from the Letta server"
            )
            assert adapter._mcp.send_message_tool == "band_send_message"


@per_adapter(Adapter.LETTA)
@pytest.mark.timeout(extra=120)  # Letta-side MCP sync stalls are 60s each
@pytest.mark.asyncio(loop_scope="session")
async def test_reply_arrives_via_mcp_send_tool(
    cell: AdapterCell,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """The agent's reply travels through the MCP send tool, not auto-relay.

    Auto-relay (the adapter forwarding plain assistant text) would keep the
    room green even with a dead MCP tool path, so it is disabled for this cell:
    the only way any reply can land is the Letta server calling
    ``band_send_message`` on the self-hosted MCP server.
    """
    identity = await cell.provision()
    adapter = cell.build()
    adapter.config.auto_relay = False
    room_id = await resource_manager.provision_room(
        title="e2e-letta-mcp-send", participants=[identity.id]
    )
    with cell.resources.track_running(identity.id):
        async with (
            running_agent(identity, adapter, cell.settings),
            reply_capture(room_id) as capture,
        ):
            trigger = await user_ops.send_message(
                room_id,
                "Please reply with a short greeting.",
                mention_id=identity.id,
                mention_name=identity.name,
            )
            replies = await capture.wait_for_reply(trigger, identity.id)
    replies.assert_present(
        what="a reply sent through the MCP band_send_message tool (relay disabled)"
    )
