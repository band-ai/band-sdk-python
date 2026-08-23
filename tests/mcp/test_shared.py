"""Unit tests for `band_mcp.shared`.

Covers `StandaloneResolver`: human singleton dispatch, per-room `AgentTools`
caching for the server lifespan, LRU eviction, lock-stripe serialization,
the room-less None-key/"" sentinel, and the send_message pre-flight
participant refresh + discard-on-failure. AgentTools/HumanTools import
unconditionally (band-sdk is this same package), so there is no
SDK-import-failure mode to test.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from band_mcp import shared as shared_mod
from band_mcp.config import Config
from band_mcp.shared import (
    AGENT_TOOLS_CACHE_MAX_SIZE,
    StandaloneResolver,
    build_standalone_resolver,
)
from band.core.exceptions import BandToolError
from band.runtime.tools import ToolDefinition, SendMessageInput, GetParticipantsInput
from band.testing.fake_tools import FakeAgentTools
from tests.mcp.conftest import FakeHumanTools


def _definition(
    name: str, method_name: str, *, surface: str = "agent"
) -> ToolDefinition:
    model = SendMessageInput if method_name == "send_message" else GetParticipantsInput
    return ToolDefinition(
        name=name, input_model=model, method_name=method_name, surface=surface
    )


def _fake_agent_rest(agent_id: str = "self-agent-id") -> MagicMock:
    """A `MagicMock` agent_rest whose identity lookup resolves to `agent_id`."""
    rest = MagicMock()
    identity = MagicMock()
    identity.data.id = agent_id
    rest.agent_api_identity.get_agent_me = AsyncMock(return_value=identity)
    return rest


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
    human_tools = FakeHumanTools(profile={"id": "u1"})
    resolver = StandaloneResolver(human_tools=human_tools)

    result = await resolver.invoke(
        _definition("band_get_my_profile", "get_my_profile", surface="human"), None, {}
    )

    assert result == {"id": "u1"}


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


@pytest.fixture
def fake_agent_tools(monkeypatch) -> list[str | None]:
    """Patches shared_mod.AgentTools with a bare fake; returns the room_ids
    passed to each construction, in order (empty if a test never checks it)."""
    constructed: list[str | None] = []

    class FakeAgentTools:
        def __init__(self, room_id: str, rest: object, agent_id: str | None = None):
            self.room_id = room_id
            self.agent_id = agent_id
            constructed.append(room_id)

    monkeypatch.setattr(shared_mod, "AgentTools", FakeAgentTools)
    return constructed


async def test_get_agent_tools_caches_per_room(fake_agent_tools):
    resolver = StandaloneResolver(agent_rest=_fake_agent_rest())

    first = await resolver._get_or_create_agent_tools("room_A", "band_get_participants")
    second = await resolver._get_or_create_agent_tools(
        "room_A", "band_get_participants"
    )

    assert first is second
    assert fake_agent_tools == ["room_A"]


async def test_get_agent_tools_returns_distinct_instance_per_room(fake_agent_tools):
    resolver = StandaloneResolver(agent_rest=_fake_agent_rest())

    a = await resolver._get_or_create_agent_tools("room_A", "band_get_participants")
    b = await resolver._get_or_create_agent_tools("room_B", "band_get_participants")

    assert a is not b
    assert a.room_id == "room_A"
    assert b.room_id == "room_B"


async def test_get_agent_tools_passes_resolved_agent_id(fake_agent_tools):
    resolver = StandaloneResolver(agent_rest=_fake_agent_rest(agent_id="self-agent-id"))

    tools = await resolver._get_or_create_agent_tools("room_A", "band_get_participants")

    assert tools.agent_id == "self-agent-id"


async def test_resolve_agent_id_concurrent_cold_start_issues_one_rest_call(
    fake_agent_tools,
):
    """Two rooms hashing to different lock stripes both cold-starting at once
    must not each issue their own `get_agent_me` call -- `_resolve_agent_id`'s
    own docstring promises "resolved once, cached for the resolver's
    lifetime", which only a dedicated lock (independent of the per-chat_id
    stripe locks the callers hold) can guarantee under real concurrency."""
    rest = _fake_agent_rest()

    async def slow_get_agent_me():
        await asyncio.sleep(0)
        identity = MagicMock()
        identity.data.id = "self-agent-id"
        return identity

    rest.agent_api_identity.get_agent_me = AsyncMock(side_effect=slow_get_agent_me)
    resolver = StandaloneResolver(agent_rest=rest)

    await asyncio.gather(
        resolver._get_or_create_agent_tools("room_A", "band_get_participants"),
        resolver._get_or_create_agent_tools("room_B", "band_get_participants"),
    )

    assert rest.agent_api_identity.get_agent_me.await_count == 1


def test_get_agent_tools_locks_use_fixed_stripes():
    resolver = StandaloneResolver(agent_rest=_fake_agent_rest())

    a1 = resolver._agent_tools_lock("room_A")
    a2 = resolver._agent_tools_lock("room_A")
    roomless = resolver._agent_tools_lock(None)

    assert a1 is a2
    assert a1 in resolver._agent_tools_locks
    assert roomless in resolver._agent_tools_locks
    assert len(resolver._agent_tools_locks) == AGENT_TOOLS_CACHE_MAX_SIZE


async def test_get_agent_tools_cache_evicts_oldest_room(fake_agent_tools):
    resolver = StandaloneResolver(agent_rest=_fake_agent_rest())

    for i in range(AGENT_TOOLS_CACHE_MAX_SIZE):
        await resolver._get_or_create_agent_tools(f"room_{i}", "band_get_participants")

    first = await resolver._get_or_create_agent_tools("room_0", "band_get_participants")
    assert first is await resolver._get_or_create_agent_tools(
        "room_0", "band_get_participants"
    )
    assert len(resolver._agent_tools_cache) == AGENT_TOOLS_CACHE_MAX_SIZE

    await resolver._get_or_create_agent_tools("room_overflow", "band_get_participants")

    assert len(resolver._agent_tools_cache) == AGENT_TOOLS_CACHE_MAX_SIZE
    assert "room_0" in resolver._agent_tools_cache
    assert "room_1" not in resolver._agent_tools_cache
    assert "room_overflow" in resolver._agent_tools_cache


class SlowSendAgentTools(FakeAgentTools):
    """A send that blocks mid-dispatch until released, so a test can force a
    concurrent cache-miss insert to land while this call is still in flight."""

    def __init__(
        self, *args: Any, ready: asyncio.Event, release: asyncio.Event, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self._ready = ready
        self._release = release

    async def send_message(
        self, content: str, mentions: list[str] | list[dict[str, str]] | None = None
    ) -> dict[str, Any]:
        self._ready.set()
        await self._release.wait()
        return await super().send_message(content, mentions=mentions)


async def test_invoke_agent_survives_its_own_cache_entry_evicted_mid_flight(
    fake_agent_tools,
):
    """Review finding: ``popitem(last=False)`` doesn't hold the evicted
    room's own stripe lock, so a room's cached ``AgentTools`` can be evicted
    while a call for that same room is still in flight elsewhere. Impact is
    bounded -- the in-flight call already holds a direct reference to its
    own instance, unaffected by the dict eviction -- but the race itself is
    real, so pin down that it stays bounded rather than assuming it."""
    resolver = StandaloneResolver(agent_rest=_fake_agent_rest())

    ready = asyncio.Event()
    release = asyncio.Event()
    room_a_tools = SlowSendAgentTools(room_id="room_A", ready=ready, release=release)
    resolver._agent_tools_cache["room_A"] = room_a_tools

    send_task = asyncio.create_task(
        resolver.invoke(
            _definition("band_send_message", "send_message"),
            "room_A",
            {"content": "hi", "mentions": ["@x"]},
        )
    )
    # room_A's own cache lookup already ran (and move_to_end'd it) on the way
    # to this blocking point, so it's the *freshest* entry here -- filling
    # every other slot afterwards is what ages it back into the LRU spot.
    await ready.wait()  # room_A's send is mid-dispatch, its stripe lock held

    for i in range(AGENT_TOOLS_CACHE_MAX_SIZE):
        await resolver._get_or_create_agent_tools(f"room_{i}", "band_get_participants")

    # The last insert above overflowed the cache and evicted the LRU entry --
    # room_A's, via the un-locked _get_or_create_agent_tools path -- even
    # though room_A's own call above hasn't returned yet.
    assert "room_A" not in resolver._agent_tools_cache

    release.set()
    result = await send_task

    room_a_tools.assert_message_sent(content="hi", mentions=["@x"], count=1)
    assert result == room_a_tools.messages_sent[0]

    release.set()
    result = await send_task

    room_a_tools.assert_message_sent(content="hi", mentions=["@x"], count=1)
    assert result == room_a_tools.messages_sent[0]


async def test_get_agent_tools_accepts_none_cache_key_with_sdk_room_sentinel(
    fake_agent_tools,
):
    resolver = StandaloneResolver(agent_rest=_fake_agent_rest())

    result = await resolver._get_or_create_agent_tools(None, "band_get_participants")

    assert result.room_id == ""
    assert fake_agent_tools == [""]
    assert resolver._agent_tools_cache == {None: result}


async def test_discard_agent_tools_only_drops_current_instance(fake_agent_tools):
    resolver = StandaloneResolver(agent_rest=_fake_agent_rest())

    original = await resolver._get_or_create_agent_tools(
        "room_A", "band_get_participants"
    )
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


class OrderTrackingAgentTools(FakeAgentTools):
    """Records call order so a test can prove the pre-flight participant
    refresh genuinely runs before ``send_message``, not just that both
    happened somewhere."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.call_order: list[str] = []

    async def get_participants(self) -> list[dict[str, Any]]:
        self.call_order.append("get_participants")
        return await super().get_participants()

    async def send_message(
        self, content: str, mentions: list[str] | list[dict[str, str]] | None = None
    ) -> dict[str, Any]:
        self.call_order.append("send_message")
        return await super().send_message(content, mentions=mentions)


class FailingParticipantsAgentTools(FakeAgentTools):
    """Simulates a live refresh failure (e.g. a REST error) ahead of send."""

    async def get_participants(self) -> list[dict[str, Any]]:
        raise PermissionError("denied")


class BareBandToolErrorAgentTools(FakeAgentTools):
    """Send fails with a bare ``BandToolError`` carrying no hint yet, so the
    test exercises engine.py's own ``enrich_send_message_error`` appending
    one for real, rather than one the fake already built in."""

    def __init__(self, *args: Any, agent_id: str | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.agent_id = agent_id

    async def send_message(
        self, content: str, mentions: list[str] | list[dict[str, str]] | None = None
    ) -> dict[str, Any]:
        raise BandToolError("At least one mention is required")


async def test_invoke_send_message_refreshes_participants_first():
    fake_agent_tools = OrderTrackingAgentTools(room_id="room_A")
    resolver = StandaloneResolver(agent_rest=None)
    resolver._agent_tools_cache["room_A"] = fake_agent_tools

    result = await resolver.invoke(
        _definition("band_send_message", "send_message"),
        "room_A",
        {"content": "hi", "mentions": ["@x"]},
    )

    assert fake_agent_tools.call_order == ["get_participants", "send_message"]
    fake_agent_tools.assert_message_sent(content="hi", mentions=["@x"], count=1)
    assert result == fake_agent_tools.messages_sent[0]


async def test_invoke_send_message_discards_cache_entry_on_refresh_failure():
    fake_agent_tools = FailingParticipantsAgentTools(room_id="room_A")
    resolver = StandaloneResolver(agent_rest=None)
    resolver._agent_tools_cache["room_A"] = fake_agent_tools

    with pytest.raises(PermissionError, match="denied"):
        await resolver.invoke(
            _definition("band_send_message", "send_message"),
            "room_A",
            {"content": "hi", "mentions": ["@x"]},
        )

    fake_agent_tools.assert_no_messages_sent()
    assert "room_A" not in resolver._agent_tools_cache


async def test_invoke_send_message_error_enriched_with_available_handles():
    fake_agent_tools = BareBandToolErrorAgentTools(
        room_id="room_A",
        participants=[
            {"id": "user-1", "name": "Alice", "handle": "@alice"},
            {"id": "self", "name": "Self", "handle": "@self"},
        ],
        agent_id="self",
    )
    resolver = StandaloneResolver(agent_rest=None)
    resolver._agent_tools_cache["room_A"] = fake_agent_tools

    with pytest.raises(BandToolError) as exc_info:
        await resolver.invoke(
            _definition("band_send_message", "send_message"),
            "room_A",
            {"content": "hi", "mentions": []},
        )

    message = str(exc_info.value)
    assert "At least one mention is required" in message
    assert "@alice" in message
    assert "@self" not in message
