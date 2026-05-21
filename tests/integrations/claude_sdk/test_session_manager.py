"""Tests for ClaudeSessionManager."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from thenvoi.adapters.claude_sdk import _CLAUDE_SDK_AVAILABLE as _HAS_CLAUDE_SDK

if _HAS_CLAUDE_SDK:
    from claude_agent_sdk import ClaudeAgentOptions

pytestmark = pytest.mark.skipif(
    not _HAS_CLAUDE_SDK,
    reason="claude-agent-sdk not installed (pip install band-sdk[claude_sdk])",
)


@pytest.fixture
def mock_options() -> ClaudeAgentOptions:
    """Create real ClaudeAgentOptions for tests that go through _build_options."""
    return ClaudeAgentOptions(
        model="claude-sonnet-4-5-20250929",
        system_prompt="You are a test bot.",
        mcp_servers={},
        allowed_tools=[],
        permission_mode="acceptEdits",
    )


@pytest.fixture
def real_options() -> ClaudeAgentOptions:
    """Create a real ClaudeAgentOptions dataclass for _build_options tests."""
    return ClaudeAgentOptions(
        model="claude-sonnet-4-5-20250929",
        system_prompt="You are a test bot.",
        mcp_servers={"thenvoi": MagicMock()},
        allowed_tools=["mcp__thenvoi__thenvoi_send_message"],
        permission_mode="acceptEdits",
    )


class TestInvalidateSession:
    """Tests for invalidate_session() — evicts dead clients without disconnect."""

    @pytest.mark.asyncio
    async def test_invalidate_removes_session_without_disconnect(
        self, mock_options: ClaudeAgentOptions
    ) -> None:
        """invalidate_session should remove the client without calling disconnect()."""
        from thenvoi.integrations.claude_sdk.session_manager import (
            ClaudeSessionManager,
        )

        manager = ClaudeSessionManager(mock_options)
        await manager.start()

        # Inject a mock dead client directly into _sessions
        dead_client = MagicMock()
        dead_client.disconnect = AsyncMock()
        manager._sessions["room-dead"] = dead_client

        await manager.invalidate_session("room-dead")

        assert "room-dead" not in manager._sessions
        dead_client.disconnect.assert_not_awaited()

        await manager.stop()

    @pytest.mark.asyncio
    async def test_invalidate_nonexistent_room_is_noop(
        self, mock_options: ClaudeAgentOptions
    ) -> None:
        """invalidate_session on a room that doesn't exist should be a safe no-op."""
        from thenvoi.integrations.claude_sdk.session_manager import (
            ClaudeSessionManager,
        )

        manager = ClaudeSessionManager(mock_options)
        await manager.start()

        # Should not raise
        await manager.invalidate_session("room-nonexistent")

        assert manager.get_session_count() == 0

        await manager.stop()

    @pytest.mark.asyncio
    async def test_get_or_create_after_invalidate_creates_fresh_client(
        self, mock_options: ClaudeAgentOptions
    ) -> None:
        """After invalidation, get_or_create_session should create a new client."""
        from thenvoi.integrations.claude_sdk.session_manager import (
            ClaudeSessionManager,
        )

        manager = ClaudeSessionManager(mock_options)
        await manager.start()

        # Inject a mock dead client
        dead_client = MagicMock()
        dead_client.disconnect = AsyncMock()
        manager._sessions["room-1"] = dead_client

        await manager.invalidate_session("room-1")
        assert "room-1" not in manager._sessions

        # Now create a fresh session
        fresh_client = MagicMock()
        fresh_client.connect = AsyncMock()

        with patch(
            "thenvoi.integrations.claude_sdk.session_manager.ClaudeSDKClient",
            return_value=fresh_client,
        ):
            client = await manager.get_or_create_session("room-1")

        assert client is fresh_client
        fresh_client.connect.assert_awaited_once()

        await manager.stop()

    @pytest.mark.asyncio
    async def test_invalidate_when_not_started_is_noop(
        self, mock_options: ClaudeAgentOptions
    ) -> None:
        """invalidate_session before start() should return immediately."""
        from thenvoi.integrations.claude_sdk.session_manager import (
            ClaudeSessionManager,
        )

        manager = ClaudeSessionManager(mock_options)

        # Should not raise or hang
        await manager.invalidate_session("room-1")

    @pytest.mark.asyncio
    async def test_invalidate_does_not_affect_other_rooms(
        self, mock_options: ClaudeAgentOptions
    ) -> None:
        """Invalidating one room should leave other rooms' sessions intact."""
        from thenvoi.integrations.claude_sdk.session_manager import (
            ClaudeSessionManager,
        )

        manager = ClaudeSessionManager(mock_options)
        await manager.start()

        client_a = MagicMock()
        client_b = MagicMock()
        manager._sessions["room-a"] = client_a
        manager._sessions["room-b"] = client_b

        await manager.invalidate_session("room-a")

        assert "room-a" not in manager._sessions
        assert manager._sessions["room-b"] is client_b

        await manager.stop()


class TestBuildOptions:
    """Tests for _build_options() using dataclasses.replace()."""

    def test_preserves_all_base_fields(self, real_options: ClaudeAgentOptions) -> None:
        """_build_options should preserve all base_options fields."""
        from thenvoi.integrations.claude_sdk.session_manager import (
            ClaudeSessionManager,
        )

        manager = ClaudeSessionManager(real_options)
        result = manager._build_options("room-1")

        assert result.model == real_options.model
        assert result.system_prompt == real_options.system_prompt
        assert result.mcp_servers == real_options.mcp_servers
        assert result.allowed_tools == real_options.allowed_tools
        assert result.permission_mode == real_options.permission_mode

    def test_always_returns_copy(self, real_options: ClaudeAgentOptions) -> None:
        """_build_options should return a copy even with no overrides."""
        from thenvoi.integrations.claude_sdk.session_manager import (
            ClaudeSessionManager,
        )

        manager = ClaudeSessionManager(real_options)
        result = manager._build_options("room-1")

        assert result is not real_options

    def test_applies_resume_override(self, real_options: ClaudeAgentOptions) -> None:
        """_build_options should set resume when session_id provided."""
        from thenvoi.integrations.claude_sdk.session_manager import (
            ClaudeSessionManager,
        )

        manager = ClaudeSessionManager(real_options)
        result = manager._build_options("room-1", resume_session_id="sess-abc")

        assert result.resume == "sess-abc"

    def test_applies_can_use_tool_factory(
        self, real_options: ClaudeAgentOptions
    ) -> None:
        """_build_options should bind can_use_tool from factory."""
        from thenvoi.integrations.claude_sdk.session_manager import (
            ClaudeSessionManager,
        )

        mock_callback = MagicMock()
        factory = MagicMock(return_value=mock_callback)

        manager = ClaudeSessionManager(real_options, can_use_tool_factory=factory)
        result = manager._build_options("room-1")

        factory.assert_called_once_with("room-1")
        assert result.can_use_tool is mock_callback

    def test_does_not_mutate_base_options(
        self, real_options: ClaudeAgentOptions
    ) -> None:
        """_build_options should not mutate the original base_options."""
        from thenvoi.integrations.claude_sdk.session_manager import (
            ClaudeSessionManager,
        )

        manager = ClaudeSessionManager(real_options)
        manager._build_options("room-1", resume_session_id="sess-abc")

        # base_options should be unmodified
        assert not hasattr(real_options, "resume") or real_options.resume is None
