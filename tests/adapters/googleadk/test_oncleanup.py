"""OnCleanup behavior of the Google ADK adapter."""

from __future__ import annotations

import pytest

from tests.adapters.googleadk.bridge import GoogleADKAdapter


class TestOnCleanup:
    """Tests for on_cleanup() method."""

    @pytest.mark.asyncio
    async def test_cleans_up_room_session(self):
        """Should remove room session on cleanup."""
        adapter = GoogleADKAdapter()
        adapter._room_sessions["room-123"] = "session-abc"

        await adapter.on_cleanup("room-123")

        assert "room-123" not in adapter._room_sessions

    @pytest.mark.asyncio
    async def test_cleanup_nonexistent_room(self):
        """Should not raise when cleaning up nonexistent room."""
        adapter = GoogleADKAdapter()
        await adapter.on_cleanup("nonexistent-room")

    @pytest.mark.asyncio
    async def test_cleanup_called_twice(self):
        """Should not raise when cleanup is called twice for the same room."""
        adapter = GoogleADKAdapter()
        adapter._room_history["room-123"] = [{"role": "user", "content": "hi"}]
        adapter._room_sessions["room-123"] = "session-abc"

        await adapter.on_cleanup("room-123")
        await adapter.on_cleanup("room-123")

        assert "room-123" not in adapter._room_history
        assert "room-123" not in adapter._room_sessions

    @pytest.mark.asyncio
    async def test_cleanup_one_room_while_another_active(self):
        """Should only clean up the specified room, leaving others untouched."""
        adapter = GoogleADKAdapter()
        adapter._room_history["room-A"] = [{"role": "user", "content": "A"}]
        adapter._room_sessions["room-A"] = "session-A"
        adapter._room_history["room-B"] = [{"role": "user", "content": "B"}]
        adapter._room_sessions["room-B"] = "session-B"

        await adapter.on_cleanup("room-A")

        assert "room-A" not in adapter._room_history
        assert "room-A" not in adapter._room_sessions
        assert adapter._room_history["room-B"] == [{"role": "user", "content": "B"}]
        assert adapter._room_sessions["room-B"] == "session-B"
