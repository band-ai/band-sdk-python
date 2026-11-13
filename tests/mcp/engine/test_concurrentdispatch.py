"""MCP concurrentdispatch behavior."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from band_mcp import shared as shared_mod
from band_mcp.config import Config
from band_mcp.server import standalone_spec
from band_mcp.shared import AGENT_TOOLS_CACHE_MAX_SIZE, StandaloneResolver

from band.integrations.mcp.engine import (
    build_engine,
)
from band.testing.fake_tools import FakeAgentTools
from tests.mcp.engine.peers import BOB_PEER
from tests.mcp.engine.protocol import _direct_call


class TestConcurrentDispatch:
    async def test_concurrent_dispatch_through_one_engine(self, monkeypatch) -> None:
        """Real dispatch through StandaloneResolver's full stack -- identity
        resolution, per-room caching, lock striping -- via the engine's own
        ``_tool_manager.call_tool``, not the resolver's internal method
        directly (mirrors test_shared.py's
        test_resolve_agent_id_concurrent_cold_start_issues_one_rest_call).
        ``shared_mod.AgentTools`` is patched to hand back a room-scoped
        FakeAgentTools instead of one backed by real REST calls, so the REST
        boundary stays fake while every dispatch/caching layer above it runs
        for real."""
        constructed: list[str] = []

        class RoomAgentTools(FakeAgentTools):
            def __init__(self, room_id: str, rest: object, agent_id: str | None = None):
                super().__init__(room_id=room_id, peers=[BOB_PEER])
                constructed.append(room_id)

        monkeypatch.setattr(shared_mod, "AgentTools", RoomAgentTools)

        identity = MagicMock()
        identity.data.id = "self-agent-id"

        async def slow_get_agent_me() -> MagicMock:
            await asyncio.sleep(0)
            return identity

        rest = MagicMock()
        rest.agent_api_identity.get_agent_me = AsyncMock(side_effect=slow_get_agent_me)
        resolver = StandaloneResolver(agent_rest=rest)
        mcp = build_engine(standalone_spec(Config(scope=["agent"], tools=[]), resolver))

        async def add_bob(room_id: str) -> None:
            await _direct_call(
                mcp, "band_add_participant", chat_id=room_id, identifier="@bob"
            )

        async def get_participants(room_id: str) -> list[dict[str, Any]]:
            return await _direct_call(mcp, "band_get_participants", chat_id=room_id)

        # room_A gets two concurrent cold hits (a mutation and a read); room_B
        # and room_C get one cold hit each -- a mix of repeated and distinct
        # rooms all cold-starting at once.
        await asyncio.gather(
            add_bob("room_A"),
            get_participants("room_A"),
            get_participants("room_B"),
            get_participants("room_C"),
        )

        # The agent's own identity is resolver-global (_resolve_agent_id's own
        # docstring: "resolved once, cached for the resolver's lifetime") --
        # resolved once regardless of how many distinct rooms cold-started
        # concurrently, let alone how many calls landed on each.
        assert rest.agent_api_identity.get_agent_me.await_count == 1
        # Each room's AgentTools construction is deduped by its lock stripe --
        # one instance per distinct room, not one per call that named it.
        assert sorted(constructed) == ["room_A", "room_B", "room_C"]
        assert len(resolver._agent_tools_cache) == 3

        room_a_participants = await get_participants("room_A")
        room_b_participants = await get_participants("room_B")
        assert any(p["handle"] == "bob" for p in room_a_participants)
        assert room_b_participants == []  # no leakage from room_A's mutation

        # Cold-starting past the LRU cap still evicts down to the configured max.
        for i in range(AGENT_TOOLS_CACHE_MAX_SIZE):
            await get_participants(f"room_overflow_{i}")
        assert len(resolver._agent_tools_cache) == AGENT_TOOLS_CACHE_MAX_SIZE
