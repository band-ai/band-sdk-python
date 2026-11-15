"""ContactsInjection behavior of the Google ADK adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tests.adapters.googleadk.bridge import GoogleADKAdapter
from tests.adapters.googleadk.kit import _empty_async_iter


class TestContactsInjection:
    """Tests for contacts_msg injection."""

    @pytest.mark.asyncio
    async def test_injects_contacts_message(self, sample_message, mock_tools):
        """Should include contacts broadcast in message."""
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")

        captured_messages = []

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()

            def capture_run(**kwargs):
                captured_messages.append(kwargs.get("new_message"))
                return _empty_async_iter()

            mock_runner.run_async = capture_run
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg="Bob is now a contact",
                is_session_bootstrap=True,
                room_id="room-123",
            )

        assert len(captured_messages) == 1
        text = captured_messages[0].parts[0].text
        assert "[System]: Bob is now a contact" in text
