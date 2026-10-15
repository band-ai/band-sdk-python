from __future__ import annotations

from unittest.mock import MagicMock

from band.runtime.tools import AgentTools


class TestAgentToolsLookupPeers:
    """Test lookup_peers tool."""

    async def test_lookup_peers_success(self, mock_rest_client: MagicMock) -> None:
        """lookup_peers() should return the Fern response directly."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.lookup_peers(page=1, page_size=50)

        # Now returns full Fern response with .data and .metadata
        assert len(result.data) == 1
        assert result.data[0].name == "Agent Two"
        assert result.metadata.page == 1

    async def test_lookup_peers_filters_by_room(
        self, mock_rest_client: MagicMock
    ) -> None:
        """lookup_peers() should filter by not_in_chat."""
        tools = AgentTools("room-123", mock_rest_client)

        await tools.lookup_peers()

        call_args = mock_rest_client.agent_api_peers.list_agent_peers.call_args
        assert call_args.kwargs["not_in_chat"] == "room-123"
