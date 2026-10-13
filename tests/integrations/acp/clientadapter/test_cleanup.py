"""Tests for ACPClientAdapter cleanup."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest
from acp.schema import (
    NewSessionResponse,
)

from band.integrations.acp import client_adapter
from band.integrations.acp.client_adapter import (
    ACPClientAdapter,
)
from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent
from tests.integrations.acp.acp_toolkit.harness import (
    acp_adapter,
    fake_agent_config,
    room_holder,
)
from tests.integrations.acp.clientadapter.kit import CODEX
from tests.mcpbackends import backends_created_by


class TestACPClientAdapterCleanup:
    """Tests for ACPClientAdapter cleanup."""

    @pytest.mark.asyncio
    async def test_on_cleanup_disposes_the_room_but_not_the_shared_backend(
        self,
    ) -> None:
        agent = FakeACPAgent().will_say("ok")
        with backends_created_by():
            async with acp_adapter(
                agent, fake_agent_config(inject_band_tools=True)
            ) as session:
                await session.send("hello", room="room-123")
                runtime = session.runtime("room-123")
                backend = session.adapter._mcp.current

                await session.adapter.on_cleanup("room-123")

                assert runtime.closed
                assert "room-123" not in session.adapter._rooms
                assert session.adapter._workspaces.rooms == ()
                assert session.adapter._mcp.current is backend

    @pytest.mark.asyncio
    async def test_on_cleanup_idempotent(self) -> None:
        """Should handle cleanup of non-existent room."""
        adapter = ACPClientAdapter(CODEX)

        await adapter.on_cleanup("nonexistent-room")

    @pytest.mark.asyncio
    async def test_on_cleanup_twice(self) -> None:
        """Should handle cleanup called twice."""
        async with acp_adapter(FakeACPAgent().will_say("ok")) as session:
            await session.send("hello", room="room-123")

            await session.adapter.on_cleanup("room-123")
            await session.adapter.on_cleanup("room-123")

            assert "room-123" not in session.adapter._rooms

    @pytest.mark.asyncio
    async def test_fresh_session_cleanup_times_out(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        adapter = ACPClientAdapter(CODEX)
        runtime = adapter._build_runtime(room_holder(adapter, "room-1"), 1)
        blocked_close = asyncio.Event()

        async def wait_to_close(_: str) -> None:
            await blocked_close.wait()

        runtime.close_session = AsyncMock(wraps=wait_to_close)
        monkeypatch.setattr(client_adapter, "SESSION_CLOSE_TIMEOUT_SECONDS", 0.01)

        with caplog.at_level(logging.WARNING):
            await adapter._close_session(
                runtime,
                "session-1",
                reason=client_adapter.SessionCloseReason.UNCONFIGURED,
            )

        runtime.close_session.assert_awaited_once_with("session-1")
        assert caplog.messages == [
            "Timed out closing ACP session session-1 (unconfigured) after 0.01 seconds"
        ]

    @pytest.mark.asyncio
    async def test_cancelled_fresh_session_does_not_wait_to_close(self) -> None:
        adapter = ACPClientAdapter(CODEX)
        room = room_holder(adapter, "room-1")
        runtime = adapter._build_runtime(room, 1)
        initialization_started = asyncio.Event()
        close_started = asyncio.Event()
        release_close = asyncio.Event()
        close_finished = asyncio.Event()
        runtime.create_session_response = AsyncMock(
            return_value=NewSessionResponse(session_id="session-1")
        )

        async def wait_to_close(_: str) -> None:
            close_started.set()
            await release_close.wait()
            close_finished.set()

        runtime.close_session = AsyncMock(wraps=wait_to_close)

        async def initialize() -> None:
            async with adapter._fresh_session(room, runtime, []):
                initialization_started.set()
                await asyncio.Event().wait()

        initializing = asyncio.create_task(initialize())
        await initialization_started.wait()
        initializing.cancel()

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(initializing, timeout=0.1)

        await close_started.wait()
        release_close.set()
        await close_finished.wait()

    @pytest.mark.asyncio
    async def test_cancelled_fresh_session_close_is_held_by_its_room(self) -> None:
        """The background close task is held by the room until it finishes, so
        the room's release can wait for it."""
        adapter = ACPClientAdapter(CODEX)
        room = room_holder(adapter, "room-1")
        runtime = adapter._build_runtime(room, 1)
        initialization_started = asyncio.Event()
        release_close = asyncio.Event()
        runtime.create_session_response = AsyncMock(
            return_value=NewSessionResponse(session_id="session-1")
        )

        async def wait_to_close(_: str) -> None:
            await release_close.wait()

        runtime.close_session = AsyncMock(wraps=wait_to_close)

        async def initialize() -> None:
            async with adapter._fresh_session(room, runtime, []):
                initialization_started.set()
                await asyncio.Event().wait()

        initializing = asyncio.create_task(initialize())
        await initialization_started.wait()
        initializing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(initializing, timeout=0.1)

        [closing] = room.session_closes

        release_close.set()
        await closing

        assert room.session_closes == set()
