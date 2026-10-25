"""Letta MCP tool attachment to provider agents."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.adapters.letta import (
    LettaAdapter,
)
from tests.adapters.lettakit import (
    make_mock_agent,
    make_mock_tool_page,
)


class TestMCPToolAttachment:
    @pytest.mark.asyncio
    async def test_mcp_tools_attached_on_agent_creation(self) -> None:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        adapter._mcp.tool_ids = ["tool-1", "tool-2", "tool-3"]

        mock_agent = make_mock_agent("new-agent")
        mock_client.agents.create.return_value = mock_agent

        agent_id = await adapter._create_agent()

        assert agent_id == "new-agent"
        assert mock_client.agents.tools.attach.call_count == 3
        attach_calls = mock_client.agents.tools.attach.call_args_list
        for i, call in enumerate(attach_calls):
            assert call.kwargs["agent_id"] == "new-agent"
            assert call.kwargs["tool_id"] == f"tool-{i + 1}"

    @pytest.mark.asyncio
    async def test_verify_and_reattach_missing_tools(self) -> None:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._mcp.tool_ids = ["t1", "t2", "t3"]

        # Agent has only t1 attached
        existing_tool = MagicMock()
        existing_tool.id = "t1"
        mock_client.agents.tools.list.return_value = make_mock_tool_page(existing_tool)

        await adapter._verify_mcp_tools_attached("agent-1")

        # Exactly the missing tools are re-attached, to the right agent
        reattached = [
            (call.kwargs["agent_id"], call.kwargs["tool_id"])
            for call in mock_client.agents.tools.attach.call_args_list
        ]
        assert reattached == [("agent-1", "t2"), ("agent-1", "t3")]

    @pytest.mark.asyncio
    async def test_attach_failure_logs_warning(self) -> None:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._mcp.tool_ids = ["t1"]

        mock_client.agents.tools.attach.side_effect = Exception("attach failed")
        mock_agent = make_mock_agent()
        mock_client.agents.create.return_value = mock_agent

        # Should not raise — just log warning
        agent_id = await adapter._create_agent()
        assert agent_id == mock_agent.id
