"""ParticipantsContactsInjection behavior for Gemini."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from band.adapters.gemini import GeminiAdapter
from tests.adapters.gemini.kit import _response_with_text


class TestParticipantsContactsInjection:
    @pytest.mark.asyncio
    async def test_participants_msg_injected_into_history(
        self, sample_message, mock_tools
    ):
        adapter = GeminiAdapter()
        await adapter.on_started("TestBot", "Test bot")

        with patch.object(
            adapter, "_call_gemini", AsyncMock(return_value=_response_with_text("ok"))
        ):
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg="Alice, Bob are in the room",
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

        history = adapter._message_history["room-123"]
        # First entry should be participants system message
        assert "[System]: Alice, Bob are in the room" in history[0].parts[0].text

    @pytest.mark.asyncio
    async def test_contacts_msg_injected_into_history(self, sample_message, mock_tools):
        adapter = GeminiAdapter()
        await adapter.on_started("TestBot", "Test bot")

        with patch.object(
            adapter, "_call_gemini", AsyncMock(return_value=_response_with_text("ok"))
        ):
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg="Charlie is now a contact",
                is_session_bootstrap=True,
                room_id="room-123",
            )

        history = adapter._message_history["room-123"]
        assert "[System]: Charlie is now a contact" in history[0].parts[0].text

    @pytest.mark.asyncio
    async def test_both_participants_and_contacts_injected(
        self, sample_message, mock_tools
    ):
        adapter = GeminiAdapter()
        await adapter.on_started("TestBot", "Test bot")

        with patch.object(
            adapter, "_call_gemini", AsyncMock(return_value=_response_with_text("ok"))
        ):
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg="Alice, Bob",
                contacts_msg="Charlie added",
                is_session_bootstrap=True,
                room_id="room-123",
            )

        history = adapter._message_history["room-123"]
        # All user-side content merged into a single Content entry
        user_entry = history[0]
        assert len(user_entry.parts) == 3
        assert "[System]: Alice, Bob" in user_entry.parts[0].text
        assert "[System]: Charlie added" in user_entry.parts[1].text
        assert "Alice" in user_entry.parts[2].text  # user message
