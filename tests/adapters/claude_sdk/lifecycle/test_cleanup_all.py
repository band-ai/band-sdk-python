from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapter
from tests.adapters.claude_sdk.helpers import blocking_turn


class TestCleanupAll:
    """Tests for cleanup_all() method."""

    @pytest.mark.asyncio
    async def test_cleans_up_all_sessions(self):
        """Should stop session manager and clear room tools."""
        adapter = ClaudeSDKAdapter()

        mock_session_manager = AsyncMock()
        adapter._session_manager = mock_session_manager
        adapter._room_tools["room-1"] = MagicMock()
        adapter._room_tools["room-2"] = MagicMock()

        await adapter.cleanup_all()

        mock_session_manager.stop.assert_awaited_once()
        assert len(adapter._room_tools) == 0

    @pytest.mark.asyncio
    async def test_cancels_turns_before_stopping_session_manager(self, mock_tools):
        """Adapter shutdown must stop detached turns before closing all sessions."""
        adapter = ClaudeSDKAdapter()
        response_started, _release, wait_for_response = blocking_turn()
        client = MagicMock()
        client.query = AsyncMock()

        adapter._session_manager = AsyncMock()
        turn_task = asyncio.create_task(
            adapter._run_turn(
                client,
                "room-123",
                mock_tools,
                "message",
                "message-id",
                asyncio.get_running_loop().create_future(),
            )
        )
        adapter._turn_tasks["room-123"] = turn_task

        with patch.object(adapter, "_process_response", side_effect=wait_for_response):
            await response_started.wait()
            stop_saw_completed = []

            async def stop():
                stop_saw_completed.append(turn_task.done())

            adapter._session_manager.stop.side_effect = stop
            await adapter.cleanup_all()

        assert stop_saw_completed == [True]
