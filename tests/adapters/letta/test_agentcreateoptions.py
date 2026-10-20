from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from band.adapters.letta import (
    LettaAdapter,
    LettaAdapterConfig,
    LettaMCPConfig,
)
from tests.adapters.lettakit import (
    make_mock_agent,
)


class TestAgentCreateOptions:
    @pytest.mark.asyncio
    async def test_embedding_passed_when_configured(self) -> None:
        adapter = LettaAdapter(
            config=LettaAdapterConfig(embedding="openai/text-embedding-3-small")
        )
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        adapter._mcp.tool_ids = []
        adapter._mcp.server_id = "mcp-server-1"
        mock_client.agents.create.return_value = make_mock_agent()

        await adapter._create_agent("room-1")

        create_kwargs = mock_client.agents.create.call_args.kwargs
        assert create_kwargs["embedding"] == "openai/text-embedding-3-small"

    @pytest.mark.asyncio
    async def test_embedding_omitted_by_default(self) -> None:
        """Letta Cloud picks its own default — None must not be sent."""
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        adapter._mcp.tool_ids = []
        adapter._mcp.server_id = "mcp-server-1"
        mock_client.agents.create.return_value = make_mock_agent()

        await adapter._create_agent("room-1")

        assert "embedding" not in mock_client.agents.create.call_args.kwargs

    @pytest.mark.asyncio
    async def test_per_room_persona_carries_room_id(self) -> None:
        """Self-hosted MCP schemas require room_id per call; a per_room agent
        gets it in its persona block."""
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        adapter._mcp.tool_ids = []
        adapter._mcp.server_id = "mcp-server-1"
        mock_client.agents.create.return_value = make_mock_agent()

        await adapter._create_agent("room-42")

        blocks = mock_client.agents.create.call_args.kwargs["memory_blocks"]
        persona = next(b for b in blocks if b["label"] == "persona")
        assert "room-42" in persona["value"]

    @pytest.mark.asyncio
    async def test_external_mode_persona_omits_room_id(self) -> None:
        """External band-mcp tool schemas carry no room_id argument."""
        adapter = LettaAdapter(
            config=LettaAdapterConfig(mcp=LettaMCPConfig(mode="external"))
        )
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        adapter._mcp.tool_ids = []
        adapter._mcp.server_id = "mcp-server-1"
        mock_client.agents.create.return_value = make_mock_agent()

        await adapter._create_agent("room-42")

        blocks = mock_client.agents.create.call_args.kwargs["memory_blocks"]
        persona = next(b for b in blocks if b["label"] == "persona")
        assert "room-42" not in persona["value"]
