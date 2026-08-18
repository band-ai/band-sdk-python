"""Unit tests for `band_mcp.shared`.

INT-1096: replaces the old AppContext/lifespan-based tests (build_app_context,
get_human_tools, get_agent_tools, get_agent_tools_lock, discard_agent_tools --
all deleted with the AppContext design). Covers the same invariants against
the real `StandaloneResolver` instead: human singleton dispatch, per-room
`AgentTools` caching for the server lifespan, LRU eviction, lock-stripe
serialization, the room-less None-key/"" sentinel, and the send_message
pre-flight participant refresh + discard-on-failure (divergence-matrix rows
9, 11, 24). One old test dropped outright, not ported: SDK-import-failure
handling (row 21) -- band-sdk is this same package now, so AgentTools/
HumanTools import unconditionally; there is no failure mode left to test.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from band_mcp import shared as shared_mod
from band_mcp.config import Config
from band_mcp.shared import (
    AGENT_TOOLS_CACHE_MAX_SIZE,
    AGENT_TOOLS_LOCK_STRIPES,
    StandaloneResolver,
    build_standalone_resolver,
)
from band.runtime.tools import ToolDefinition, SendMessageInput, GetParticipantsInput


def _definition(
    name: str, method_name: str, *, surface: str = "agent"
) -> ToolDefinition:
    model = SendMessageInput if method_name == "send_message" else GetParticipantsInput
    return ToolDefinition(
        name=name, input_model=model, method_name=method_name, surface=surface
    )


# ---------------------------------------------------------------------------
# build_standalone_resolver: scope-gated client construction
# ---------------------------------------------------------------------------


def test_build_standalone_resolver_constructs_only_served_scope_clients(monkeypatch):
    constructed: list[str] = []

    class FakeRestClient:
        def __init__(self, api_key: str, base_url: str):
            self.api_key = api_key
            self.base_url = base_url
            constructed.append(api_key)

    monkeypatch.setattr(shared_mod, "AsyncRestClient", FakeRestClient)

    resolver = build_standalone_resolver(
        Config(scope=["agent"], user_key="band_u_unused", agent_key="band_a_used")
    )

    assert resolver.human_rest is None
    assert resolver.agent_rest is not None
    assert constructed == ["band_a_used"]


def test_build_standalone_resolver_constructs_human_tools_singleton(monkeypatch):
    class FakeRestClient:
        def __init__(self, api_key: str, base_url: str):
            self.api_key = api_key

    monkeypatch.setattr(shared_mod, "AsyncRestClient", FakeRestClient)

    resolver = build_standalone_resolver(Config(scope=["human"], user_key="band_u_1"))

    assert resolver.human_rest is not None
    assert resolver.agent_rest is None


# ---------------------------------------------------------------------------
# Human surface dispatch
# ---------------------------------------------------------------------------


async def test_invoke_human_dispatches_to_singleton():
    human_tools = MagicMock()
    human_tools.get_my_profile = AsyncMock(return_value={"id": "u1"})
    resolver = StandaloneResolver(human_tools=human_tools)

    result = await resolver.invoke(
        _definition("band_get_my_profile", "get_my_profile", surface="human"), None, {}
    )

    assert result == {"id": "u1"}
    human_tools.get_my_profile.assert_awaited_once_with()


async def test_invoke_human_raises_and_warns_when_unavailable(caplog):
    resolver = StandaloneResolver(human_tools=None)

    with caplog.at_level(logging.WARNING, logger="band_mcp.shared"):
        with pytest.raises(RuntimeError, match="human tools not available"):
            await resolver.invoke(
                _definition("band_get_my_profile", "get_my_profile", surface="human"),
                None,
                {},
            )
    assert any("human tools not available" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Agent surface: per-room caching
# ---------------------------------------------------------------------------


def test_get_agent_tools_caches_per_room(monkeypatch):
    constructed: list[str | None] = []

    class FakeAgentTools:
        def __init__(self, room_id: str, rest: object):
            self.room_id = room_id
            constructed.append(room_id)

    monkeypatch.setattr(shared_mod, "AgentTools", FakeAgentTools)
    resolver = StandaloneResolver(agent_rest=MagicMock())

    first = resolver._get_or_create_agent_tools("room_A")
    second = resolver._get_or_create_agent_tools("room_A")

    assert first is second
    assert constructed == ["room_A"]


def test_get_agent_tools_returns_distinct_instance_per_room(monkeypatch):
    class FakeAgentTools:
        def __init__(self, room_id: str, rest: object):
            self.room_id = room_id

    monkeypatch.setattr(shared_mod, "AgentTools", FakeAgentTools)
    resolver = StandaloneResolver(agent_rest=MagicMock())

    a = resolver._get_or_create_agent_tools("room_A")
    b = resolver._get_or_create_agent_tools("room_B")

    assert a is not b
    assert a.room_id == "room_A"
    assert b.room_id == "room_B"


def test_get_agent_tools_locks_use_fixed_stripes():
    resolver = StandaloneResolver(agent_rest=MagicMock())

    a1 = resolver._agent_tools_lock("room_A")
    a2 = resolver._agent_tools_lock("room_A")
    roomless = resolver._agent_tools_lock(None)

    assert a1 is a2
    assert a1 in resolver._agent_tools_locks
    assert roomless in resolver._agent_tools_locks
    assert len(resolver._agent_tools_locks) == AGENT_TOOLS_LOCK_STRIPES


def test_get_agent_tools_cache_evicts_oldest_room(monkeypatch):
    class FakeAgentTools:
        def __init__(self, room_id: str, rest: object):
            self.room_id = room_id

    monkeypatch.setattr(shared_mod, "AgentTools", FakeAgentTools)
    resolver = StandaloneResolver(agent_rest=MagicMock())

    for i in range(AGENT_TOOLS_CACHE_MAX_SIZE):
        resolver._get_or_create_agent_tools(f"room_{i}")

    first = resolver._get_or_create_agent_tools("room_0")
    assert first is resolver._get_or_create_agent_tools("room_0")
    assert len(resolver._agent_tools_cache) == AGENT_TOOLS_CACHE_MAX_SIZE

    resolver._get_or_create_agent_tools("room_overflow")

    assert len(resolver._agent_tools_cache) == AGENT_TOOLS_CACHE_MAX_SIZE
    assert "room_0" in resolver._agent_tools_cache
    assert "room_1" not in resolver._agent_tools_cache
    assert "room_overflow" in resolver._agent_tools_cache


def test_get_agent_tools_accepts_none_cache_key_with_sdk_room_sentinel(monkeypatch):
    seen_room_ids: list[str] = []

    class FakeAgentTools:
        def __init__(self, room_id: str, rest: object):
            self.room_id = room_id
            seen_room_ids.append(room_id)

    monkeypatch.setattr(shared_mod, "AgentTools", FakeAgentTools)
    resolver = StandaloneResolver(agent_rest=MagicMock())

    result = resolver._get_or_create_agent_tools(None)

    assert result.room_id == ""
    assert seen_room_ids == [""]
    assert resolver._agent_tools_cache == {None: result}


def test_discard_agent_tools_only_drops_current_instance(monkeypatch):
    class FakeAgentTools:
        def __init__(self, room_id: str, rest: object):
            self.room_id = room_id

    monkeypatch.setattr(shared_mod, "AgentTools", FakeAgentTools)
    resolver = StandaloneResolver(agent_rest=MagicMock())

    original = resolver._get_or_create_agent_tools("room_A")
    replacement = object()

    resolver._discard_agent_tools("room_A", replacement)
    assert resolver._agent_tools_cache["room_A"] is original

    resolver._discard_agent_tools("room_A", original)
    assert "room_A" not in resolver._agent_tools_cache


async def test_invoke_agent_raises_without_agent_credential():
    resolver = StandaloneResolver(agent_rest=None)

    with pytest.raises(RuntimeError, match="agent tools not available"):
        await resolver.invoke(
            _definition("band_get_participants", "get_participants"), "room_A", {}
        )


# ---------------------------------------------------------------------------
# send_message: pre-flight participant refresh + discard-on-failure (row 9)
# ---------------------------------------------------------------------------


async def test_invoke_send_message_refreshes_participants_first(monkeypatch):
    fake_agent_tools = MagicMock()
    fake_agent_tools.get_participants = AsyncMock(return_value=[])
    fake_agent_tools.send_message = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(
        shared_mod, "AgentTools", MagicMock(return_value=fake_agent_tools)
    )
    resolver = StandaloneResolver(agent_rest=MagicMock())

    result = await resolver.invoke(
        _definition("band_send_message", "send_message"),
        "room_A",
        {"content": "hi", "mentions": ["@x"]},
    )

    fake_agent_tools.get_participants.assert_awaited_once_with()
    fake_agent_tools.send_message.assert_awaited_once_with(
        content="hi", mentions=["@x"]
    )
    assert result == {"ok": True}


async def test_invoke_send_message_discards_cache_entry_on_refresh_failure(monkeypatch):
    fake_agent_tools = MagicMock()
    fake_agent_tools.get_participants = AsyncMock(side_effect=PermissionError("denied"))
    fake_agent_tools.send_message = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(
        shared_mod, "AgentTools", MagicMock(return_value=fake_agent_tools)
    )
    resolver = StandaloneResolver(agent_rest=MagicMock())

    with pytest.raises(PermissionError, match="denied"):
        await resolver.invoke(
            _definition("band_send_message", "send_message"),
            "room_A",
            {"content": "hi", "mentions": ["@x"]},
        )

    fake_agent_tools.send_message.assert_not_called()
    assert "room_A" not in resolver._agent_tools_cache


async def test_invoke_send_message_error_enriched_with_available_handles():
    resolver = StandaloneResolver(agent_rest=MagicMock())
    fake_agent_tools = MagicMock()
    fake_agent_tools.get_participants = AsyncMock(return_value=[])
    fake_agent_tools.participants = [
        {"id": "user-1", "name": "Alice", "handle": "@alice"},
        {"id": "self", "name": "Self", "handle": "@self"},
    ]
    fake_agent_tools.agent_id = "self"
    fake_agent_tools.send_message = AsyncMock(
        side_effect=ValueError("At least one mention is required")
    )
    resolver._agent_tools_cache["room_A"] = fake_agent_tools

    with pytest.raises(ValueError) as exc_info:
        await resolver.invoke(
            _definition("band_send_message", "send_message"),
            "room_A",
            {"content": "hi", "mentions": []},
        )

    message = str(exc_info.value)
    assert "At least one mention is required" in message
    assert "@alice" in message
    assert "@self" not in message
