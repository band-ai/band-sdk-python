"""LettaAdapter tests: Agent-create options."""

from __future__ import annotations

from typing import Any

import pytest

from band.adapters.letta import LettaAdapterConfig, LettaMCPConfig
from tests.adapters.lettakit import make_mock_agent, ready_letta_adapter


async def _agent_create_kwargs(
    config: LettaAdapterConfig | None = None, *, room_id: str = "room-1"
) -> dict[str, Any]:
    """The kwargs a started adapter sends to Letta's ``agents.create`` for a room."""
    adapter, mock_client = ready_letta_adapter(config)
    adapter._mcp.tool_ids = []
    mock_client.agents.create.return_value = make_mock_agent()

    await adapter._create_agent(room_id)

    return mock_client.agents.create.call_args.kwargs


def _persona(create_kwargs: dict[str, Any]) -> str:
    return next(
        block["value"]
        for block in create_kwargs["memory_blocks"]
        if block["label"] == "persona"
    )


class TestAgentCreateOptions:
    @pytest.mark.asyncio
    async def test_embedding_passed_when_configured(self) -> None:
        create_kwargs = await _agent_create_kwargs(
            LettaAdapterConfig(embedding="openai/text-embedding-3-small")
        )

        assert create_kwargs["embedding"] == "openai/text-embedding-3-small"

    @pytest.mark.asyncio
    async def test_embedding_omitted_by_default(self) -> None:
        """Letta Cloud picks its own default — None must not be sent."""
        assert "embedding" not in await _agent_create_kwargs()

    @pytest.mark.asyncio
    async def test_model_settings_passed_when_configured(self) -> None:
        model_settings = {
            "provider_type": "openai",
            "reasoning": {"reasoning_effort": "low"},
        }

        create_kwargs = await _agent_create_kwargs(
            LettaAdapterConfig(model_settings=model_settings)
        )

        assert create_kwargs["model_settings"] == model_settings

    @pytest.mark.asyncio
    async def test_model_settings_omitted_by_default(self) -> None:
        """Letta keeps its per-model defaults — None must not be sent."""
        assert "model_settings" not in await _agent_create_kwargs()

    @pytest.mark.asyncio
    async def test_per_room_persona_carries_room_id(self) -> None:
        """Self-hosted MCP schemas require room_id per call; a per_room agent
        gets it in its persona block."""
        create_kwargs = await _agent_create_kwargs(room_id="room-42")

        assert "room-42" in _persona(create_kwargs)

    @pytest.mark.asyncio
    async def test_external_mode_persona_omits_room_id(self) -> None:
        """External band-mcp tool schemas carry no room_id argument."""
        create_kwargs = await _agent_create_kwargs(
            LettaAdapterConfig(mcp=LettaMCPConfig(mode="external")), room_id="room-42"
        )

        assert "room-42" not in _persona(create_kwargs)
