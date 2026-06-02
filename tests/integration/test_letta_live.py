"""Live integration tests for Letta adapter.

Requires a running self-hosted Letta server and band-mcp server.
Skipped in CI by default.

Environment variables:
    LETTA_BASE_URL     Letta server URL (default: http://localhost:8283)
    LETTA_API_KEY      Letta API key (optional for self-hosted)
    LETTA_AGENT_ID     Pre-existing agent ID for reuse tests
    MCP_SERVER_URL     band-mcp server URL (default: http://localhost:8002/sse)

Run with:
    uv run pytest tests/integration/test_letta_live.py -v -s --no-cov
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.requires_api

LETTA_BASE_URL = os.environ.get("LETTA_BASE_URL", "http://localhost:8283")
LETTA_API_KEY = os.environ.get("LETTA_API_KEY", "")
LETTA_AGENT_ID = os.environ.get("LETTA_AGENT_ID", "")
MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:8002/sse")

skip_no_server = pytest.mark.skipif(
    not os.environ.get("LETTA_BASE_URL"),
    reason="LETTA_BASE_URL not set (no running Letta server)",
)

skip_no_agent = pytest.mark.skipif(
    not LETTA_AGENT_ID,
    reason="LETTA_AGENT_ID not set",
)

skip_no_mcp = pytest.mark.skipif(
    not os.environ.get("MCP_SERVER_URL"),
    reason="MCP_SERVER_URL not set (no running MCP server)",
)


@skip_no_server
@pytest.mark.asyncio
async def test_existing_agent_message() -> None:
    """Send a message to a pre-existing Letta agent and verify response."""
    from letta_client import AsyncLetta

    client_kwargs: dict[str, str] = {"base_url": LETTA_BASE_URL}
    if LETTA_API_KEY:
        client_kwargs["api_key"] = LETTA_API_KEY

    client = AsyncLetta(**client_kwargs)

    # Create a temporary agent for this test
    agent = await client.agents.create(
        memory_blocks=[{"label": "persona", "value": "You are a test assistant."}],
        include_base_tools=True,
    )

    try:
        response = await client.agents.messages.create(
            agent_id=agent.id,
            messages=[{"role": "user", "content": "Say hello in exactly one word."}],
        )
        assert response.messages
        msg_types = [getattr(m, "message_type", None) for m in response.messages]
        assert any(t == "assistant_message" for t in msg_types), (
            f"Expected assistant_message, got: {msg_types}"
        )
    finally:
        await client.agents.delete(agent.id)


@skip_no_server
@skip_no_mcp
@pytest.mark.asyncio
async def test_mcp_server_registration() -> None:
    """Test MCP server registration with Letta."""
    from letta_client import AsyncLetta

    client_kwargs: dict[str, str] = {"base_url": LETTA_BASE_URL}
    if LETTA_API_KEY:
        client_kwargs["api_key"] = LETTA_API_KEY

    client = AsyncLetta(**client_kwargs)

    server = await client.mcp_servers.create(
        server_name="band-test",
        config={
            "mcp_server_type": "sse",
            "server_url": MCP_SERVER_URL,
        },
    )
    assert server.id

    try:
        tools = await client.mcp_servers.tools.list(mcp_server_id=server.id)
        tool_names = [t.name for t in tools]
        assert len(tool_names) > 0, "MCP server should expose at least one tool"
    finally:
        # Clean up
        try:
            await client.mcp_servers.delete(server.id)
        except Exception:
            pass


@skip_no_server
@pytest.mark.asyncio
async def test_conversations_api() -> None:
    """Test the Conversations API for shared agent mode."""
    from letta_client import AsyncLetta

    client_kwargs: dict[str, str] = {"base_url": LETTA_BASE_URL}
    if LETTA_API_KEY:
        client_kwargs["api_key"] = LETTA_API_KEY

    client = AsyncLetta(**client_kwargs)

    # Create agent
    agent = await client.agents.create(
        memory_blocks=[{"label": "persona", "value": "You are a test assistant."}],
        include_base_tools=True,
    )

    try:
        # Create two conversations for the same agent
        conv1 = await client.conversations.create(agent_id=agent.id)
        conv2 = await client.conversations.create(agent_id=agent.id)

        assert conv1.id != conv2.id

        # Send messages to different conversations
        stream1 = await client.conversations.messages.create(
            conversation_id=conv1.id,
            messages=[{"role": "user", "content": "Hello from room 1"}],
        )
        stream2 = await client.conversations.messages.create(
            conversation_id=conv2.id,
            messages=[{"role": "user", "content": "Hello from room 2"}],
        )

        messages1 = [m async for m in stream1]
        messages2 = [m async for m in stream2]
        assert messages1
        assert messages2
    finally:
        await client.agents.delete(agent.id)
