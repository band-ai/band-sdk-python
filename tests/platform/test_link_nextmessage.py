"""GetNextMessage behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from band_rest.core.api_error import ApiError
from band_rest.types.chat_message_metadata import ChatMessageMetadata

from band.platform.link import BandLink


class TestGetNextMessage:
    """Tests for the /next REST wrapper."""

    @pytest.mark.asyncio
    async def test_returns_none_on_204(self) -> None:
        """204 No Content is the platform's "no actionable message" signal —
        the only ``ApiError`` that should resolve to ``None``."""

        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_messages.with_raw_response.get_agent_next_message = (
            AsyncMock(return_value=MagicMock(status_code=204))
        )

        assert await link.get_next_message("room-1") is None

    @pytest.mark.asyncio
    async def test_raises_on_non_204_api_error(self) -> None:
        """Regression: a 5xx or other API failure must propagate so callers
        can distinguish "no pending" from "lookup failed." The old behavior
        swallowed both as ``None``, which silently dropped messages at the
        OneShot claim step."""

        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_messages.with_raw_response.get_agent_next_message = (
            AsyncMock(side_effect=ApiError(status_code=503, body="upstream down"))
        )

        with pytest.raises(ApiError):
            await link.get_next_message("room-1")

    @pytest.mark.asyncio
    async def test_raises_on_transport_error(self) -> None:
        """Connection errors / timeouts also propagate — same reason."""
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_messages.with_raw_response.get_agent_next_message = (
            AsyncMock(side_effect=ConnectionError("dns failure"))
        )

        with pytest.raises(ConnectionError):
            await link.get_next_message("room-1")

    @pytest.mark.asyncio
    async def test_returns_platform_message_on_success(self) -> None:
        """The happy path: a real response body is projected into a
        PlatformMessage — no test elsewhere in the suite exercises this
        construction, only the error/edge branches around it."""
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        item = MagicMock(
            id="msg-1",
            chat_room_id="room-1",
            content="hello",
            sender_id="user-1",
            sender_type="User",
            sender_name="User One",
            message_type="text",
            metadata=ChatMessageMetadata.model_validate({"mentions": []}),
            inserted_at=None,
        )
        link.rest.agent_api_messages.with_raw_response.get_agent_next_message = (
            AsyncMock(
                return_value=MagicMock(status_code=200, data=MagicMock(data=item))
            )
        )

        message = await link.get_next_message("room-1")

        assert message is not None
        assert message.id == "msg-1"
        assert message.room_id == "room-1"
        assert message.content == "hello"
        assert message.sender_name == "User One"

    @pytest.mark.asyncio
    async def test_rejects_empty_success_response(self) -> None:
        """An empty success response cannot prove there is no pending work."""
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        response = MagicMock(status_code=200, data=None)
        link.rest.agent_api_messages.with_raw_response.get_agent_next_message = (
            AsyncMock(return_value=response)
        )

        with pytest.raises(ApiError):
            await link.get_next_message("room-1")
