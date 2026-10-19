"""Replacing shared MCP preserves an accepted turn and each room's reply binding."""

from __future__ import annotations

import asyncio

import pytest

from band.core.protocols import TurnResultAlreadyReported
from tests.adapters.claude_sdk.fakecli import MCP_DISCONNECTED, Hold, UnavailableMcp
from tests.adapters.claude_sdk.helpers import OpenRoom


class TestActiveMcpReplacement:
    async def test_an_active_room_finishes_then_resumes_on_the_replacement(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room()
        other = room.beside("room-2")
        active = Hold()
        room.claude.script(
            [room.model_reply("accepted work finished"), active],
            [other.model_reply("other room served")],
            [room.model_reply("original room resumed")],
        )
        tools = room.fresh_tools()
        sending = asyncio.create_task(room.send("long task", tools=tools))
        try:
            async with active:
                await room.crash_band_server()
                await other.send("replace the backend")
                assert other.chat == ["other room served"]
                assert not sending.done()
            await sending
            resuming_tools = room.fresh_tools()
            await room.send("continue", tools=resuming_tools)
        finally:
            active.released.set()
            await asyncio.gather(sending, return_exceptions=True)

        first, second, resumed = room.session_band_ports
        assert first != second == resumed
        assert room.claude.resumed == [None, None, "sess-1"]
        assert room.chat == ["accepted work finished", "original room resumed"]
        assert tools.turn.complete and tools.turn.replied
        assert resuming_tools.turn.complete and resuming_tools.turn.replied
        assert other.chat == ["other room served"]
        assert room.failures == other.failures == []
        assert room.claude.sessions[0].alive is False

    async def test_an_unreplied_turn_using_the_dead_endpoint_fails_once_and_resumes(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room()
        other = room.beside("room-2")
        active = Hold()
        room.claude.script(
            [active, UnavailableMcp(room.model_reply("unreachable reply"))],
            [other.model_reply("replacement room served")],
            [room.model_reply("failed room resumed")],
        )
        failed_tools = room.fresh_tools()
        sending = asyncio.create_task(room.send("long task", tools=failed_tools))
        try:
            async with active:
                assert not failed_tools.turn.replied
                await room.crash_band_server()
                await other.send("replace the backend")
                assert not sending.done()
            with pytest.raises(TurnResultAlreadyReported, match=MCP_DISCONNECTED):
                await sending
            assert room.chat == []
            assert len(room.reported_failures) == 1
            await room.send("recover")
        finally:
            active.released.set()
            await asyncio.gather(sending, return_exceptions=True)

        first, replacement, resumed = room.session_band_ports
        assert first != replacement == resumed
        assert room.claude.resumed == [None, None, "sess-1"]
        assert room.chat == ["failed room resumed"]
        assert room.failures == [f"Claude SDK turn failed: {MCP_DISCONNECTED}"]
        assert other.chat == ["replacement room served"]
        assert other.failures == []
        assert room.claude.sessions[0].alive is False
