"""The adapter's teardown is one durable sequence that keeps the Band MCP
server until every Claude process it may serve is confirmed closed."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from tests.adapters.claude_sdk.fakecli import FakeClaude, Hold
from tests.adapters.claude_sdk.helpers import ClaudeRoom

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]


def hold_stop(room: ClaudeRoom) -> tuple[Hold, list[str]]:
    backend = room.adapter._mcp.current
    assert backend is not None
    stop = backend.stop
    hold, stops = Hold(), []

    async def held() -> None:
        hold.reached.set()
        await hold.released.wait()
        await stop()
        stops.append("stopped")

    object.__setattr__(backend, "stop", held)
    return hold, stops


class TestTeardownCustody:
    async def test_a_cancelled_teardown_caller_does_not_abandon_the_band_server(
        self, claude_room: OpenRoom, claude: FakeClaude
    ) -> None:
        room = await claude_room()
        claude.script([room.model_reply("Hi.")])
        await room.send("hi")
        hold, stops = hold_stop(room)

        caller = asyncio.create_task(room.adapter.cleanup_all())
        try:
            await hold.reached.wait()
            caller.cancel()
            await asyncio.gather(caller, return_exceptions=True)
        finally:
            hold.released.set()
        await room.adapter.cleanup_all()

        assert stops == ["stopped"]
        assert room.adapter._mcp._closing is None
        assert not claude.sessions[0].alive

    async def test_a_session_that_will_not_close_keeps_the_band_server_until_retry(
        self, claude_room: OpenRoom, claude: FakeClaude
    ) -> None:
        room = await claude_room()
        claude.script([room.model_reply("Hi.")])
        await room.send("hi")
        backend = room.adapter._mcp.current
        claude.refuse_close = True

        with pytest.raises(ExceptionGroup) as caught:
            await room.adapter.cleanup_all()
        assert caught.value.subgroup(RuntimeError) is not None
        # The CLI may still dial it: the Band server keeps running.
        assert room.adapter._mcp.current is backend
        assert backend is not None and backend.is_running

        claude.refuse_close = False
        await room.adapter.cleanup_all()
        assert room.adapter._mcp.current is None
        assert not backend.is_running

    async def test_a_failed_band_stop_is_retried_before_a_restart(
        self, claude_room: OpenRoom, claude: FakeClaude
    ) -> None:
        room = await claude_room()
        backend = room.adapter._mcp.current
        assert backend is not None
        stop = backend.stop
        attempts: list[Any] = []

        async def failing_once() -> None:
            attempts.append(None)
            if len(attempts) == 1:
                raise RuntimeError("stop failed")
            await stop()

        object.__setattr__(backend, "stop", failing_once)
        with pytest.raises(RuntimeError, match="stop failed"):
            await room.adapter.cleanup_all()
        assert room.adapter._mcp._closing is backend

        await room.adapter.on_started("Test Agent", "restarted")

        assert len(attempts) == 2
        assert room.adapter._mcp._closing is None
        assert not backend.is_running
        claude.script([room.model_reply("Back.")])
        await room.send("hi")
        assert room.chat == ["Back."]
