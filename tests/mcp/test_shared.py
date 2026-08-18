"""Unit tests for `band_mcp.shared`.

Covers acceptance criterion #11 from INT-350: `get_human_tools` returns a
singleton and `get_agent_tools` caches per room for the server lifespan.
INT-352 hardened SDK import to fail-hard (ConfigError) rather than fail-soft —
tests below reflect that.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from band_mcp import shared as shared_mod
from band_mcp.config import ConfigError
from band_mcp.shared import (
    AGENT_TOOLS_CACHE_MAX_SIZE,
    AGENT_TOOLS_LOCK_STRIPES,
    AppContext,
    build_app_context,
    discard_agent_tools,
    get_agent_tools,
    get_agent_tools_lock,
    get_human_tools,
)


def _make_ctx(app_context: AppContext) -> object:
    """Build a minimal ctx object matching AppContextType for the helpers."""
    request_context = SimpleNamespace(lifespan_context=app_context)
    return SimpleNamespace(request_context=request_context)


# ---------------------------------------------------------------------------
# build_app_context: legacy fallback
# ---------------------------------------------------------------------------


def test_build_app_context_legacy_user_key_builds_only_human_client(monkeypatch):
    constructed: list[str] = []

    class FakeRestClient:
        def __init__(self, api_key: str, base_url: str):
            self.api_key = api_key
            self.base_url = base_url
            constructed.append(api_key)

    monkeypatch.setattr(shared_mod.settings, "band_api_key", "thnv_u_abc")
    monkeypatch.setattr(shared_mod, "AsyncRestClient", FakeRestClient)

    app_ctx = build_app_context(None)

    assert app_ctx.human_rest is not None
    assert app_ctx.agent_rest is None
    assert constructed == ["thnv_u_abc"]


def test_build_app_context_legacy_agent_key_builds_only_agent_client(monkeypatch):
    constructed: list[str] = []

    class FakeRestClient:
        def __init__(self, api_key: str, base_url: str):
            self.api_key = api_key
            self.base_url = base_url
            constructed.append(api_key)

    monkeypatch.setattr(shared_mod.settings, "band_api_key", "thnv_a_abc")
    monkeypatch.setattr(shared_mod, "AsyncRestClient", FakeRestClient)

    app_ctx = build_app_context(None)

    assert app_ctx.human_rest is None
    assert app_ctx.agent_rest is not None
    assert constructed == ["thnv_a_abc"]


def test_build_app_context_constructs_only_served_scope_clients(monkeypatch):
    constructed: list[str] = []

    class FakeRestClient:
        def __init__(self, api_key: str, base_url: str):
            self.api_key = api_key
            self.base_url = base_url
            constructed.append(api_key)

    monkeypatch.setattr(shared_mod, "AsyncRestClient", FakeRestClient)

    app_ctx = build_app_context(
        shared_mod.Config(
            scope=["agent"], user_key="thnv_u_unused", agent_key="thnv_a_used"
        )
    )

    assert app_ctx.human_rest is None
    assert app_ctx.agent_rest is not None
    assert constructed == ["thnv_a_used"]


# ---------------------------------------------------------------------------
# get_human_tools: startup-constructed singleton
# ---------------------------------------------------------------------------


def test_get_human_tools_returns_singleton_across_calls():
    sentinel = object()
    app_ctx = AppContext(human_tools=sentinel)
    ctx = _make_ctx(app_ctx)

    first = get_human_tools(ctx)
    second = get_human_tools(ctx)
    assert first is sentinel
    assert second is sentinel
    assert first is second


def test_get_human_tools_returns_none_and_warns_when_unavailable(caplog):
    app_ctx = AppContext(human_tools=None)
    ctx = _make_ctx(app_ctx)

    with caplog.at_level(logging.WARNING, logger="band_mcp.shared"):
        result = get_human_tools(ctx)
    assert result is None
    assert any("HumanTools not available" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# get_agent_tools: per-room cache
# ---------------------------------------------------------------------------


def test_get_agent_tools_caches_per_room(monkeypatch):
    fake_agent_rest = MagicMock()
    app_ctx = AppContext(agent_rest=fake_agent_rest)
    ctx = _make_ctx(app_ctx)

    constructed: list[str | None] = []

    class FakeAgentTools:
        def __init__(self, room_id: str | None, rest: object):
            self.room_id = room_id
            self.rest = rest
            constructed.append(room_id)

    monkeypatch.setattr(shared_mod, "_try_import_agent_tools", lambda: FakeAgentTools)

    first = get_agent_tools(ctx, "room_A")
    second = get_agent_tools(ctx, "room_A")
    assert first is second
    assert constructed == ["room_A"]


def test_get_agent_tools_returns_distinct_instance_per_room(monkeypatch):
    fake_agent_rest = MagicMock()
    app_ctx = AppContext(agent_rest=fake_agent_rest)
    ctx = _make_ctx(app_ctx)

    class FakeAgentTools:
        def __init__(self, room_id: str | None, rest: object):
            self.room_id = room_id

    monkeypatch.setattr(shared_mod, "_try_import_agent_tools", lambda: FakeAgentTools)

    a = get_agent_tools(ctx, "room_A")
    b = get_agent_tools(ctx, "room_B")
    assert a is not b
    assert a.room_id == "room_A"
    assert b.room_id == "room_B"


def test_get_agent_tools_locks_use_fixed_stripes():
    app_ctx = AppContext(agent_rest=MagicMock())
    ctx = _make_ctx(app_ctx)

    a1 = get_agent_tools_lock(ctx, "room_A")
    a2 = get_agent_tools_lock(ctx, "room_A")
    roomless = get_agent_tools_lock(ctx, None)

    assert a1 is a2
    assert a1 in app_ctx._agent_tools_locks
    assert roomless in app_ctx._agent_tools_locks
    assert len(app_ctx._agent_tools_locks) == AGENT_TOOLS_LOCK_STRIPES


def test_get_agent_tools_cache_evicts_oldest_room(monkeypatch):
    fake_agent_rest = MagicMock()
    app_ctx = AppContext(agent_rest=fake_agent_rest)
    ctx = _make_ctx(app_ctx)

    class FakeAgentTools:
        def __init__(self, room_id: str | None, rest: object):
            self.room_id = room_id

    monkeypatch.setattr(shared_mod, "_try_import_agent_tools", lambda: FakeAgentTools)

    for i in range(AGENT_TOOLS_CACHE_MAX_SIZE):
        get_agent_tools(ctx, f"room_{i}")

    first = get_agent_tools(ctx, "room_0")
    assert first is get_agent_tools(ctx, "room_0")
    assert len(app_ctx._agent_tools_cache) == AGENT_TOOLS_CACHE_MAX_SIZE

    get_agent_tools(ctx, "room_overflow")

    assert len(app_ctx._agent_tools_cache) == AGENT_TOOLS_CACHE_MAX_SIZE
    assert "room_0" in app_ctx._agent_tools_cache
    assert "room_1" not in app_ctx._agent_tools_cache
    assert "room_overflow" in app_ctx._agent_tools_cache


def test_get_agent_tools_accepts_none_cache_key_with_sdk_room_sentinel(monkeypatch):
    fake_agent_rest = MagicMock()
    app_ctx = AppContext(agent_rest=fake_agent_rest)
    ctx = _make_ctx(app_ctx)

    class FakeAgentTools:
        def __init__(self, room_id: str, rest: object):
            self.room_id = room_id

    monkeypatch.setattr(shared_mod, "_try_import_agent_tools", lambda: FakeAgentTools)

    result = get_agent_tools(ctx, None, sdk_room_id="")

    assert result.room_id == ""
    assert app_ctx._agent_tools_cache == {None: result}


def test_discard_agent_tools_only_drops_current_instance(monkeypatch):
    fake_agent_rest = MagicMock()
    app_ctx = AppContext(agent_rest=fake_agent_rest)
    ctx = _make_ctx(app_ctx)

    class FakeAgentTools:
        def __init__(self, room_id: str | None, rest: object):
            self.room_id = room_id

    monkeypatch.setattr(shared_mod, "_try_import_agent_tools", lambda: FakeAgentTools)

    original = get_agent_tools(ctx, "room_A")
    replacement = object()

    discard_agent_tools(ctx, "room_A", replacement)
    assert app_ctx._agent_tools_cache["room_A"] is original

    discard_agent_tools(ctx, "room_A", original)
    assert "room_A" not in app_ctx._agent_tools_cache


def test_get_agent_tools_returns_none_without_agent_credential(caplog):
    app_ctx = AppContext(agent_rest=None)
    ctx = _make_ctx(app_ctx)

    with caplog.at_level(logging.WARNING, logger="band_mcp.shared"):
        result = get_agent_tools(ctx, "room_A")
    assert result is None
    assert any("no agent credential configured" in r.message for r in caplog.records)


def test_get_agent_tools_raises_when_sdk_import_fails(monkeypatch):
    fake_agent_rest = MagicMock()
    app_ctx = AppContext(agent_rest=fake_agent_rest)
    ctx = _make_ctx(app_ctx)

    # INT-352 change: a missing SDK is a configuration error, not a silent
    # degradation. `_try_import_agent_tools` now raises ConfigError on failure;
    # get_agent_tools propagates so the operator sees an actionable message.
    def _raise() -> object:
        raise ConfigError("band-sdk >= 0.2.11 is required")

    monkeypatch.setattr(shared_mod, "_try_import_agent_tools", _raise)

    with pytest.raises(ConfigError, match="band-sdk"):
        get_agent_tools(ctx, "room_A")
    # Nothing should be cached when construction fails.
    assert app_ctx._agent_tools_cache == {}
