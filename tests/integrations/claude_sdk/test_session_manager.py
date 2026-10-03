"""Tests for ClaudeSessionManager."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from band.adapters.claude_sdk import _CLAUDE_SDK_AVAILABLE as _HAS_CLAUDE_SDK
from band.integrations.claude_sdk.session_manager import (
    ClaudeSessionManager,
    ClaudeSessionManagerStoppedError,
)
from band.runtime.tools import BAND_MCP_SERVER_NAME

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
        mcp_servers={"band": MagicMock()},
        allowed_tools=["mcp__band__band_send_message"],
        permission_mode="acceptEdits",
    )


class TestInvalidateSession:
    """Tests for invalidate_session() — evicts dead clients without disconnect."""

    @pytest.mark.asyncio
    async def test_invalidate_removes_session_without_disconnect(
        self, mock_options: ClaudeAgentOptions
    ) -> None:
        """invalidate_session should remove the client without calling disconnect()."""

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
            "band.integrations.claude_sdk.session_manager.ClaudeSDKClient",
            return_value=fresh_client,
        ):
            client = await manager.get_or_create_session("room-1")

        assert client is fresh_client
        fresh_client.connect.assert_awaited_once()

        await manager.stop()

    @pytest.mark.asyncio
    async def test_invalidate_does_not_affect_other_rooms(
        self, mock_options: ClaudeAgentOptions
    ) -> None:
        """Invalidating one room should leave other rooms' sessions intact."""

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


async def never_started(manager: ClaudeSessionManager) -> None:
    pass


async def stopped(manager: ClaudeSessionManager) -> None:
    await manager.start()
    await manager.stop()


@pytest.mark.parametrize("state", [never_started, stopped])
@pytest.mark.parametrize(
    "command",
    [
        pytest.param(lambda m: m.cleanup_session("room-1"), id="cleanup_session"),
        pytest.param(lambda m: m.invalidate_session("room-1"), id="invalidate_session"),
        pytest.param(lambda m: m.cleanup_all(), id="cleanup_all"),
    ],
)
async def test_a_command_without_a_running_loop_returns_at_once(
    mock_options: ClaudeAgentOptions,
    state: Callable[[ClaudeSessionManager], Awaitable[None]],
    command: Callable[[ClaudeSessionManager], Awaitable[None]],
) -> None:
    """An agent can leave a room before any message started the loop, or
    after it stopped; nothing would ever answer a queued command."""
    manager = ClaudeSessionManager(mock_options)
    await state(manager)

    async with asyncio.timeout(1):
        await command(manager)


@pytest.mark.parametrize(
    "request_during_stop",
    [
        pytest.param(lambda m: m.cleanup_session("room-1"), id="queued-behind-stop"),
        pytest.param(lambda m: m.get_or_create_session("room-1"), id="new-session"),
    ],
)
async def test_a_request_racing_stop_fails_instead_of_hanging(
    mock_options: ClaudeAgentOptions,
    request_during_stop: Callable[[ClaudeSessionManager], Awaitable[object]],
) -> None:
    """A request made while ``stop`` runs must fail, never wait forever on a
    future nothing will resolve, nor start a new session on a stopping manager."""
    manager = ClaudeSessionManager(mock_options)
    await manager.start()

    async with asyncio.timeout(1):
        stopped, requested = await asyncio.gather(
            manager.stop(),
            request_during_stop(manager),
            return_exceptions=True,
        )

    assert stopped is None
    assert isinstance(requested, ClaudeSessionManagerStoppedError)


async def start_slow_to_stop(options: ClaudeAgentOptions) -> ClaudeSessionManager:
    """A running manager whose shutdown yields while disconnecting a session.

    A helper, not a fixture: async fixtures run on the session loop, and the
    manager's loop task must live on the test's.
    """

    async def slow_disconnect() -> None:
        await asyncio.sleep(0)

    manager = ClaudeSessionManager(options)
    await manager.start()
    manager._sessions["room-1"] = MagicMock(disconnect=slow_disconnect)
    return manager


async def test_a_manager_without_an_mcp_factory_reuses_the_rooms_session(
    mock_options: ClaudeAgentOptions,
) -> None:
    """Only a factory's changed servers recycle a session; without one, the
    room keeps its client instead of reconnecting the CLI every message."""
    manager = ClaudeSessionManager(mock_options)
    client = MagicMock(connect=AsyncMock(), disconnect=AsyncMock())

    with patch(
        "band.integrations.claude_sdk.session_manager.ClaudeSDKClient",
        return_value=client,
    ):
        first = await manager.get_or_create_session("room-1")
        second = await manager.get_or_create_session("room-1")
    await manager.stop()

    assert first is second
    client.connect.assert_awaited_once()


async def test_overlapping_stops_share_one_shutdown(
    mock_options: ClaudeAgentOptions,
) -> None:
    slow_to_stop = await start_slow_to_stop(mock_options)
    async with asyncio.timeout(1):
        results = await asyncio.gather(
            slow_to_stop.stop(), slow_to_stop.stop(), return_exceptions=True
        )

    assert results == [None, None]


async def test_a_cancelled_stop_still_finishes_the_shutdown(
    mock_options: ClaudeAgentOptions,
) -> None:
    slow_to_stop = await start_slow_to_stop(mock_options)
    first = asyncio.ensure_future(slow_to_stop.stop())
    await asyncio.sleep(0)
    first.cancel()

    async with asyncio.timeout(1):
        await slow_to_stop.stop()

    assert not slow_to_stop.has_session("room-1")


async def test_a_stopped_manager_refuses_new_sessions(
    mock_options: ClaudeAgentOptions,
) -> None:
    """``stop()`` is final: the adapter builds a fresh manager to start again,
    so a stopped one must never quietly restart its loop."""
    manager = ClaudeSessionManager(mock_options)
    await manager.start()
    await manager.stop()

    with pytest.raises(ClaudeSessionManagerStoppedError):
        async with asyncio.timeout(1):
            await manager.get_or_create_session("room-1")


class TestBuildOptions:
    """Tests for _build_options() using dataclasses.replace()."""

    def test_preserves_all_base_fields(self, real_options: ClaudeAgentOptions) -> None:
        """_build_options should preserve all base_options fields."""

        manager = ClaudeSessionManager(real_options)
        result = manager._build_options("room-1")

        assert result.model == real_options.model
        assert result.system_prompt == real_options.system_prompt
        assert result.mcp_servers == real_options.mcp_servers
        assert result.allowed_tools == real_options.allowed_tools
        assert result.permission_mode == real_options.permission_mode

    def test_always_returns_copy(self, real_options: ClaudeAgentOptions) -> None:
        """_build_options should return a copy even with no overrides."""

        manager = ClaudeSessionManager(real_options)
        result = manager._build_options("room-1")

        assert result is not real_options

    def test_applies_resume_override(self, real_options: ClaudeAgentOptions) -> None:
        """_build_options should set resume when session_id provided."""

        manager = ClaudeSessionManager(real_options)
        result = manager._build_options("room-1", resume_session_id="sess-abc")

        assert result.resume == "sess-abc"

    def test_applies_can_use_tool_factory(
        self, real_options: ClaudeAgentOptions
    ) -> None:
        """_build_options should bind can_use_tool from factory."""

        mock_callback = MagicMock()
        factory = MagicMock(return_value=mock_callback)

        manager = ClaudeSessionManager(real_options, can_use_tool_factory=factory)
        result = manager._build_options("room-1")

        factory.assert_called_once_with("room-1")
        assert result.can_use_tool is mock_callback

    def test_applies_mcp_servers_factory(
        self, real_options: ClaudeAgentOptions
    ) -> None:
        """_build_options should give each room the factory's MCP servers."""

        def room_servers(room_id: str) -> dict[str, Any]:
            return {BAND_MCP_SERVER_NAME: {"type": "http", "url": room_id}}

        manager = ClaudeSessionManager(real_options, mcp_servers_factory=room_servers)

        servers = [manager._build_options(room).mcp_servers for room in ("a", "b")]

        assert servers == [room_servers("a"), room_servers("b")]

    def test_does_not_mutate_base_options(
        self, real_options: ClaudeAgentOptions
    ) -> None:
        """_build_options should not mutate the original base_options."""

        manager = ClaudeSessionManager(real_options)
        manager._build_options("room-1", resume_session_id="sess-abc")

        # base_options should be unmodified
        assert not hasattr(real_options, "resume") or real_options.resume is None
