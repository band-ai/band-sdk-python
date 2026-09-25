from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapter
from band.converters.claude_sdk import ClaudeSDKSessionState
from tests.adapters.claude_sdk.helpers import blocking_turn


class TestOnCleanup:
    """Tests for on_cleanup() method."""

    @pytest.mark.asyncio
    async def test_cleans_up_session_and_tools(self):
        """Should cleanup session and remove room tools."""
        adapter = ClaudeSDKAdapter()

        # Set up mock session manager
        mock_session_manager = AsyncMock()
        adapter._session_manager = mock_session_manager
        adapter._room_tools["room-123"] = MagicMock()

        await adapter.on_cleanup("room-123")

        mock_session_manager.cleanup_session.assert_awaited_once_with("room-123")
        assert "room-123" not in adapter._room_tools

    @pytest.mark.asyncio
    async def test_cancels_turn_before_cleaning_up_session(self, mock_tools):
        """Room cleanup must stop a detached turn before closing its client."""
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
            cleanup_saw_completed = []

            async def cleanup_session(_room_id):
                cleanup_saw_completed.append(turn_task.done())

            adapter._session_manager.cleanup_session.side_effect = cleanup_session
            await adapter.on_cleanup("room-123")

        assert cleanup_saw_completed == [True]

    @pytest.mark.asyncio
    async def test_cleanup_without_session_manager_is_safe(self):
        """Should handle cleanup when session manager not initialized."""
        adapter = ClaudeSDKAdapter()
        adapter._room_tools["room-123"] = MagicMock()

        # Should not raise
        await adapter.on_cleanup("room-123")

        assert "room-123" not in adapter._room_tools

    @pytest.mark.asyncio
    async def test_old_turn_cannot_release_a_rejoined_turn(self, mock_tools):
        """A turn surviving cleanup must not release a later turn in the same room."""
        adapter = ClaudeSDKAdapter()
        old_release = asyncio.get_running_loop().create_future()
        adapter._turn_release["room-123"] = old_release
        response_started, release_response, wait_for_response = blocking_turn()

        client = MagicMock()
        client.query = AsyncMock()
        with patch.object(adapter, "_process_response", side_effect=wait_for_response):
            old_turn = asyncio.create_task(
                adapter._run_turn(
                    client,
                    "room-123",
                    mock_tools,
                    "old message",
                    "old-message-id",
                    old_release,
                )
            )
            await response_started.wait()
            adapter._turn_release.pop("room-123")
            rejoined_release = asyncio.get_running_loop().create_future()
            adapter._turn_release["room-123"] = rejoined_release

            release_response.set()
            await old_turn

        assert not rejoined_release.done()

    @pytest.mark.asyncio
    async def test_run_turn_always_releases_its_handed_future(self, mock_tools):
        """``_run_turn`` resolves the release future it was given directly, never
        by looking it up in ``_turn_release`` -- so a caller isn't stranded even
        when that dict never held (or no longer holds) this room's entry."""
        adapter = ClaudeSDKAdapter()
        release_future = asyncio.get_running_loop().create_future()
        client = MagicMock()
        client.query = AsyncMock()

        with patch.object(adapter, "_process_response", new_callable=AsyncMock):
            await adapter._run_turn(
                client,
                "room-123",
                mock_tools,
                "message",
                "message-id",
                release_future,
            )

        assert release_future.done()

    @pytest.mark.asyncio
    async def test_completed_turn_drops_its_task(self, mock_tools):
        """A completed turn must not retain its client and prompt in the room map."""
        adapter = ClaudeSDKAdapter()
        release_future = asyncio.get_running_loop().create_future()
        client = MagicMock()
        client.query = AsyncMock()

        with patch.object(adapter, "_process_response", new_callable=AsyncMock):
            turn_task = asyncio.create_task(
                adapter._run_turn(
                    client,
                    "room-123",
                    mock_tools,
                    "message",
                    "message-id",
                    release_future,
                )
            )
            adapter._turn_tasks["room-123"] = turn_task
            await turn_task

        assert "room-123" not in adapter._turn_tasks

    @pytest.mark.asyncio
    async def test_cancelled_message_cancels_detached_turn(
        self, sample_message, mock_tools
    ):
        """Cancelling the runtime callback must stop its detached Claude turn."""
        adapter = ClaudeSDKAdapter()
        response_started, _release, wait_for_response = blocking_turn()
        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)
        adapter._session_manager = mock_manager

        with patch.object(adapter, "_process_response", side_effect=wait_for_response):
            message_task = asyncio.create_task(
                adapter.on_message(
                    msg=sample_message,
                    tools=mock_tools,
                    history=ClaudeSDKSessionState(text=""),
                    participants_msg=None,
                    contacts_msg=None,
                    is_session_bootstrap=True,
                    room_id="room-123",
                )
            )
            await response_started.wait()
            turn_task = adapter._turn_tasks["room-123"]
            message_task.cancel()

            with pytest.raises(asyncio.CancelledError):
                await message_task

        assert turn_task.cancelled()
        assert "room-123" not in adapter._turn_tasks

    @pytest.mark.asyncio
    async def test_log_turn_task_exception_skips_cancelled_tasks(self):
        """A cancelled task's exception must never be retrieved -- that call raises."""
        adapter = ClaudeSDKAdapter()
        task = asyncio.create_task(asyncio.sleep(10))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        adapter._log_turn_task_exception(task)  # must not raise

    @pytest.mark.asyncio
    async def test_log_turn_task_exception_retrieves_a_failed_turns_exception(self):
        """A failed (non-cancelled) turn's exception is retrieved without raising."""
        adapter = ClaudeSDKAdapter()

        async def failing_turn() -> None:
            raise RuntimeError("boom")

        task = asyncio.create_task(failing_turn())
        with pytest.raises(RuntimeError):
            await task

        adapter._log_turn_task_exception(task)  # must not raise
