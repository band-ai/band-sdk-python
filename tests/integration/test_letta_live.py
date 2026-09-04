"""Live integration tests for Letta adapter.

Requires a running self-hosted Letta server (docker). No band-mcp is needed:
the adapter self-hosts its Band MCP server in-process and registers it with
Letta. Skipped in CI by default (``requires_api``); once run, a missing
prerequisite **fails loudly** — it never skips.

Environment variables:
    LETTA_BASE_URL              Letta server URL (default: http://localhost:8283)
    LETTA_API_KEY               Letta API key (optional for self-hosted)
    LETTA_MODEL                 LLM model for agent create — the server rejects a
                                create without one (default: openai/gpt-5.4-mini)
    LETTA_EMBEDDING             Embedding model for agent create — required by
                                Letta's Docker server
                                (default: openai/text-embedding-3-small)
    LETTA_MCP_ADVERTISED_HOST   Host the Letta server uses to reach the
                                self-hosted MCP server (default:
                                host.docker.internal for a dockerized Letta;
                                set 127.0.0.1 for a natively-run one)

Run with:
    docker run -d --add-host=host.docker.internal:host-gateway \
      -p 8283:8283 -e OPENAI_API_KEY="$OPENAI_API_KEY" letta/letta:latest
    uv run pytest tests/integration/test_letta_live.py -v -s --no-cov
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic_settings import BaseSettings, SettingsConfigDict

from band.integrations.letta.orgscope import resolve_org_scoped_headers

pytestmark = pytest.mark.requires_api


class LettaLiveSettings(BaseSettings):
    """Live-test knobs (field name == env var, see module docstring)."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    letta_base_url: str = "http://localhost:8283"
    letta_api_key: str = ""
    letta_model: str = "openai/gpt-5.4-mini"
    letta_embedding: str = "openai/text-embedding-3-small"
    letta_mcp_advertised_host: str = "host.docker.internal"


_SETTINGS = LettaLiveSettings()
LETTA_BASE_URL = _SETTINGS.letta_base_url
LETTA_API_KEY = _SETTINGS.letta_api_key
LETTA_MODEL = _SETTINGS.letta_model
LETTA_EMBEDDING = _SETTINGS.letta_embedding
LETTA_MCP_ADVERTISED_HOST = _SETTINGS.letta_mcp_advertised_host


# A fixed name for the raw-client tests below: they all share one Letta
# org/user (as opposed to test_two_instances_stay_isolated_in_shared_org,
# which deliberately uses two).
_RAW_CLIENT_AGENT_NAME = "IntegrationBotRawClient"


async def _make_client() -> object:
    """An org-scoped Letta client, matching how the adapter builds its own.

    Once CI's Letta server sets LETTA_NO_DEFAULT_ACTOR=true (see
    .github/scripts/setup-letta.sh), a client with no user_id header raises
    on every call instead of silently falling back to the default org — so
    this helper must resolve org-scoped headers the same way the adapter's
    on_started does, not rely on the default org.
    """
    from letta_client import AsyncLetta

    client_kwargs: dict[str, object] = {"base_url": LETTA_BASE_URL}
    if LETTA_API_KEY:
        client_kwargs["api_key"] = LETTA_API_KEY
    client_kwargs["default_headers"] = await resolve_org_scoped_headers(
        base_url=LETTA_BASE_URL,
        agent_name=_RAW_CLIENT_AGENT_NAME,
        bearer_token=LETTA_API_KEY or None,
    )
    client = AsyncLetta(**client_kwargs)
    # A fresh org has zero Tool rows; force the lazy base-tool seed (see
    # LettaAdapter.on_started) before these tests' agents.create(
    # include_base_tools=True) calls, which would otherwise raise.
    await client.tools.list()
    return client


@pytest.mark.asyncio
async def test_existing_agent_message() -> None:
    """Send a message to a pre-existing Letta agent and verify response."""
    client = await _make_client()

    # Create a temporary agent for this test
    # The server rejects an agent create without a model; the Docker server
    # additionally requires an embedding model.
    agent = await client.agents.create(
        memory_blocks=[{"label": "persona", "value": "You are a test assistant."}],
        include_base_tools=True,
        model=LETTA_MODEL,
        embedding=LETTA_EMBEDDING,
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


@pytest.mark.asyncio
async def test_adapter_self_hosted_mcp_registration() -> None:
    """The adapter's self-hosted MCP server registers with a live Letta.

    Proves the whole self-host wiring end to end: the in-process LocalMCPServer
    starts, Letta registers its advertised URL, and Letta's tool discovery over
    that URL reports the band platform tools (i.e. Letta could actually reach
    the server — discovery is a live MCP round-trip, not a config echo).
    """
    from band.adapters.letta import LettaAdapter, LettaAdapterConfig, LettaMCPConfig

    loopback = LETTA_MCP_ADVERTISED_HOST in ("127.0.0.1", "localhost")
    adapter = LettaAdapter(
        config=LettaAdapterConfig(
            base_url=LETTA_BASE_URL,
            provider_key=LETTA_API_KEY or None,
            model=LETTA_MODEL,
            embedding=LETTA_EMBEDDING,
            mcp=LettaMCPConfig(
                bind_host="127.0.0.1" if loopback else "0.0.0.0",
                advertised_host=LETTA_MCP_ADVERTISED_HOST,
            ),
        ),
    )

    await adapter.on_started("IntegrationBot", "Band Letta integration test bot")
    try:
        assert adapter._mcp.server_id, "MCP server was not registered with Letta"
        assert adapter._mcp.tool_ids, "Letta discovered no tools from the MCP server"
        assert adapter._mcp.send_message_tool == "band_send_message"
    finally:
        # Releases the registration; the local server dies with the process
        # (stopping it here would wedge Letta's sync worker — see the adapter).
        await adapter.cleanup_all()


@pytest.mark.asyncio
async def test_two_instances_stay_isolated_in_shared_org() -> None:
    """Two adapter instances on one shared self-hosted Letta stay isolated.

    Regression test for the org+user scoping fix (INT-985): without it,
    Letta dedupes MCP-discovered Tool rows by (name, organization_id), so a
    second instance's registration silently re-points the first instance's
    band_send_message tool row to its own MCP server — the first instance's
    agent then routes tool calls into the wrong instance's identity.
    """
    from letta_client import NotFoundError

    from band.adapters.letta import LettaAdapter, LettaAdapterConfig, LettaMCPConfig

    loopback = LETTA_MCP_ADVERTISED_HOST in ("127.0.0.1", "localhost")
    # A unique suffix per run guarantees each adapter lands in a genuinely
    # fresh org (not an already-seeded one from a prior run), which is what
    # makes the agents.create() calls below a real proof of the base-tool
    # bootstrap in on_started.
    suffix = uuid4().hex[:8]

    def make_adapter() -> LettaAdapter:
        return LettaAdapter(
            config=LettaAdapterConfig(
                base_url=LETTA_BASE_URL,
                provider_key=LETTA_API_KEY or None,
                model=LETTA_MODEL,
                embedding=LETTA_EMBEDDING,
                mcp=LettaMCPConfig(
                    bind_host="127.0.0.1" if loopback else "0.0.0.0",
                    advertised_host=LETTA_MCP_ADVERTISED_HOST,
                ),
            ),
        )

    async def send_message_tool_metadata(adapter: LettaAdapter) -> dict:
        tools = await adapter._client.tools.list()
        tool = next(t for t in tools if t.name == adapter._mcp.send_message_tool)
        assert tool.metadata is not None
        return tool.metadata

    adapter_a = make_adapter()
    adapter_b = make_adapter()
    try:
        await adapter_a.on_started(f"IsolationBotA-{suffix}", "Isolation test bot A")
        meta_a_before = await send_message_tool_metadata(adapter_a)

        await adapter_b.on_started(f"IsolationBotB-{suffix}", "Isolation test bot B")
        meta_b = await send_message_tool_metadata(adapter_b)

        user_id_a = adapter_a._client.default_headers["user_id"]
        user_id_b = adapter_b._client.default_headers["user_id"]
        assert user_id_a != user_id_b

        # B registering its own MCP server must not repoint A's tool row —
        # the exact collision this fix exists to prevent.
        meta_a_after = await send_message_tool_metadata(adapter_a)
        assert meta_a_before["mcp"]["server_id"] == meta_a_after["mcp"]["server_id"]
        assert meta_a_after["mcp"]["server_id"] != meta_b["mcp"]["server_id"]

        # Fresh-org agent creation succeeds for both — proves the tools.list()
        # bootstrap in on_started actually seeded base tools; without it this
        # raises before either agent exists.
        agent_id_a = await adapter_a._create_agent()
        agent_id_b = await adapter_b._create_agent()

        # Cross-org visibility: neither instance's client can retrieve the
        # other's agent, but each can retrieve its own.
        with pytest.raises(NotFoundError):
            await adapter_b._client.agents.retrieve(agent_id_a)
        await adapter_a._client.agents.retrieve(agent_id_a)

        with pytest.raises(NotFoundError):
            await adapter_a._client.agents.retrieve(agent_id_b)
        await adapter_b._client.agents.retrieve(agent_id_b)
    finally:
        await adapter_a.cleanup_all()
        await adapter_b.cleanup_all()


@pytest.mark.asyncio
async def test_conversations_api() -> None:
    """Test the Conversations API for shared agent mode."""
    client = await _make_client()

    # Create agent
    # The server rejects an agent create without a model; the Docker server
    # additionally requires an embedding model.
    agent = await client.agents.create(
        memory_blocks=[{"label": "persona", "value": "You are a test assistant."}],
        include_base_tools=True,
        model=LETTA_MODEL,
        embedding=LETTA_EMBEDDING,
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
