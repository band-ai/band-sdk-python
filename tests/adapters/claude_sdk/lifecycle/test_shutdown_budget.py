"""Adapter shutdown closes rooms before the Band MCP server, all within one
reporting budget, and leaves anything unfinished for a retry."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapterConfig
from band.core.exceptions import CleanupIncompleteError
from tests.adapters.claude_sdk.fakecli import Hold
from tests.adapters.claude_sdk.helpers import REPORTING_BUDGET, ClaudeRoom

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]


class TestShutdownBudget:
    async def test_shutdown_keeps_mcp_for_open_rooms(
        self, claude_room: OpenRoom
    ) -> None:
        """A CLI that is slow to close makes shutdown report incomplete cleanup;
        the Band MCP server it dials stays up until a retry confirms the room
        closed, and nothing is closed twice."""
        room = await claude_room(ClaudeSDKAdapterConfig(retention=REPORTING_BUDGET))
        room.claude.script([room.model_reply("hi")])
        await room.send("hi")
        backend = room.adapter._mcp.current
        assert backend is not None
        closing = room.claude.closing = Hold()

        with pytest.raises(CleanupIncompleteError):
            await room.adapter.cleanup_all()
        [cli] = room.claude.sessions
        still_serving = (room.adapter._mcp.current is backend, cli.close_calls)
        closing.released.set()
        await room.adapter.cleanup_all()

        assert still_serving == (True, 1)
        assert (backend.is_running, cli.alive) == (False, False)

    @pytest.mark.looptime
    async def test_shutdown_steps_share_one_budget(
        self, claude_room: OpenRoom, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Closing the room and then the Band MCP server each fit the budget on
        their own but not together, so the budget runs out during the server's
        close, which a retry then finishes."""
        room = await claude_room(ClaudeSDKAdapterConfig(retention=REPORTING_BUDGET))
        room.claude.script([room.model_reply("hi")])
        await room.send("hi")
        backend = room.adapter._mcp.current
        assert backend is not None
        step_s = REPORTING_BUDGET.transition_timeout_s * 0.7
        closing = room.claude.closing = Hold()
        mcp = room.adapter._mcp
        close_mcp = mcp.close

        # The server's real shutdown is quick; only its delay is staged here.
        async def slow_mcp_close(*, final: bool) -> None:
            await asyncio.sleep(step_s)
            await close_mcp(final=final)

        with monkeypatch.context() as patch:
            patch.setattr(mcp, "close", slow_mcp_close)
            shutdown = asyncio.create_task(room.adapter.cleanup_all())
            async with closing:
                await asyncio.sleep(step_s)
            with pytest.raises(CleanupIncompleteError, match="MCP"):
                await shutdown
            [cli] = room.claude.sessions
            at_budget = (cli.alive, backend.is_running)
            await room.adapter.cleanup_all()

        assert at_budget == (False, True)
        assert backend.is_running is False
