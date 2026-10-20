from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.adapters.letta import (
    LettaAdapter,
)
from tests.adapters.lettakit import (
    default_enforcement,
)


class TestInstructionBlockUpdate:
    @pytest.mark.asyncio
    async def test_updates_persona_block(self) -> None:
        adapter = LettaAdapter()
        adapter._system_prompt = "Test system prompt"
        mock_client = AsyncMock()
        adapter._client = mock_client

        await adapter._update_instruction_block("agent-1", "room-1")

        # Default config self-hosts the MCP server in per_room mode, so the
        # enforcement carries the room id the tool schemas require.
        mock_client.agents.blocks.update.assert_called_once_with(
            "persona",
            agent_id="agent-1",
            value=default_enforcement(room_id="room-1") + "Test system prompt",
        )

    @pytest.mark.asyncio
    async def test_falls_back_to_next_label(self) -> None:
        adapter = LettaAdapter()
        adapter._system_prompt = "Test prompt"
        mock_client = AsyncMock()
        adapter._client = mock_client

        # First label fails, second succeeds
        mock_client.agents.blocks.update.side_effect = [
            Exception("not found"),
            None,
            None,
        ]

        await adapter._update_instruction_block("agent-1", "room-1")

        assert mock_client.agents.blocks.update.call_count == 2
        second_call = mock_client.agents.blocks.update.call_args_list[1]
        assert second_call.args[0] == "custom_instructions"

    @pytest.mark.asyncio
    async def test_creates_persona_when_all_labels_fail(self) -> None:
        adapter = LettaAdapter()
        adapter._system_prompt = "Test prompt"
        mock_client = AsyncMock()
        adapter._client = mock_client

        mock_client.agents.blocks.update.side_effect = Exception("not found")
        mock_block = MagicMock()
        mock_block.id = "block-1"
        mock_client.blocks.create.return_value = mock_block

        await adapter._update_instruction_block("agent-1", "room-1")

        mock_client.blocks.create.assert_called_once()
        assert mock_client.blocks.create.call_args.kwargs["label"] == "persona"
        mock_client.agents.blocks.attach.assert_called_once_with(
            "block-1",
            agent_id="agent-1",
        )
