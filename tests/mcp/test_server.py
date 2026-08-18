"""Unit tests for `band_mcp.server`.

Focused on the pieces of `run()` that do non-trivial branching without
actually starting FastMCP: the pure-legacy escape-hatch detection and its
scope write-back (C2/I3 from INT-350 PR review).
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from band_mcp import server as server_mod
from band_mcp.config import Config


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------


def _ctx_for_app(app_ctx: object) -> object:
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context=app_ctx))


async def test_health_check_checks_both_configured_surfaces():
    human_rest = SimpleNamespace(
        human_api_agents=SimpleNamespace(list_my_agents=AsyncMock(return_value=[]))
    )
    agent_rest = SimpleNamespace(
        agent_api_identity=SimpleNamespace(get_agent_me=AsyncMock(return_value={}))
    )
    app_ctx = SimpleNamespace(human_rest=human_rest, agent_rest=agent_rest)

    result = await server_mod.health_check(_ctx_for_app(app_ctx))

    assert result.startswith("OK | human,agent | ")
    human_rest.human_api_agents.list_my_agents.assert_awaited_once()
    agent_rest.agent_api_identity.get_agent_me.assert_awaited_once()


async def test_health_check_reports_agent_failure_even_when_human_succeeds():
    human_rest = SimpleNamespace(
        human_api_agents=SimpleNamespace(list_my_agents=AsyncMock(return_value=[]))
    )
    agent_rest = SimpleNamespace(
        agent_api_identity=SimpleNamespace(
            get_agent_me=AsyncMock(side_effect=RuntimeError("agent denied"))
        )
    )
    app_ctx = SimpleNamespace(human_rest=human_rest, agent_rest=agent_rest)

    result = await server_mod.health_check(_ctx_for_app(app_ctx))

    assert result == "Failed | agent | agent denied"
    human_rest.human_api_agents.list_my_agents.assert_awaited_once()
    agent_rest.agent_api_identity.get_agent_me.assert_awaited_once()


# ---------------------------------------------------------------------------
# _is_pure_legacy_invocation
# ---------------------------------------------------------------------------


def _make_args(**overrides: object) -> argparse.Namespace:
    """Build an argparse.Namespace matching server.parse_args() defaults."""
    defaults: dict[str, object] = {
        "user_key": None,
        "agent_key": None,
        "room_id": None,
        "scope": None,
        "tools": None,
        "transport": None,
        "host": None,
        "port": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_is_pure_legacy_invocation_true_when_only_legacy_key(monkeypatch):
    monkeypatch.delenv("BAND_USER_KEY", raising=False)
    monkeypatch.delenv("BAND_AGENT_KEY", raising=False)
    monkeypatch.delenv("BAND_MCP_SCOPE", raising=False)
    monkeypatch.delenv("BAND_MCP_TOOLS", raising=False)
    monkeypatch.delenv("BAND_MCP_ROOM_ID", raising=False)

    config = Config(legacy_key="thnv_u_abc", scope=[])
    args = _make_args()
    assert server_mod._is_pure_legacy_invocation(args, config) is True


def test_is_pure_legacy_invocation_false_when_cli_scope_set(monkeypatch):
    for name in (
        "BAND_USER_KEY",
        "BAND_AGENT_KEY",
        "BAND_MCP_SCOPE",
        "BAND_MCP_TOOLS",
        "BAND_MCP_ROOM_ID",
    ):
        monkeypatch.delenv(name, raising=False)

    config = Config(legacy_key="thnv_u_abc", scope=[])
    args = _make_args(scope=["agent"])
    assert server_mod._is_pure_legacy_invocation(args, config) is False


def test_is_pure_legacy_invocation_false_when_new_env_set(monkeypatch):
    for name in (
        "BAND_USER_KEY",
        "BAND_AGENT_KEY",
        "BAND_MCP_SCOPE",
        "BAND_MCP_TOOLS",
        "BAND_MCP_ROOM_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BAND_USER_KEY", "thnv_u_explicit")

    config = Config(legacy_key="thnv_abc", scope=[])
    args = _make_args()
    assert server_mod._is_pure_legacy_invocation(args, config) is False


def test_is_pure_legacy_invocation_false_when_no_legacy_key():
    config = Config(legacy_key=None, scope=[])
    args = _make_args()
    assert server_mod._is_pure_legacy_invocation(args, config) is False


def test_malformed_legacy_key_does_not_bypass_validation(monkeypatch):
    for name in (
        "BAND_USER_KEY",
        "BAND_AGENT_KEY",
        "BAND_MCP_SCOPE",
        "BAND_MCP_TOOLS",
        "BAND_MCP_ROOM_ID",
    ):
        monkeypatch.delenv(name, raising=False)

    config = Config(legacy_key="not_a_band_key", scope=[])
    args = _make_args()
    legacy_human, legacy_agent = server_mod._legacy_key_capabilities(config.legacy_key)

    assert server_mod._is_pure_legacy_invocation(args, config) is True
    assert (legacy_human or legacy_agent) is False


# ---------------------------------------------------------------------------
# Escape-hatch scope write-back (C2 / I3)
#
# These tests exercise the `validate(config)` failure path inside `run()` by
# driving the relevant branch directly rather than invoking `run()` — `run()`
# ends with `mcp.run()` which would block on stdio. The logic under test is
# small enough to reconstruct inline: if `_is_pure_legacy_invocation` is true,
# the legacy key's prefix determines `config.scope`.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "legacy_key,expected_scope",
    [
        ("thnv_u_timestamp_random", ["human"]),
        ("thnv_a_timestamp_random", ["agent"]),
        ("thnv_timestamp_random", ["agent", "human"]),
    ],
)
def test_escape_hatch_writes_scope_from_legacy_key(
    monkeypatch, legacy_key, expected_scope
):
    """When the escape hatch fires, config.scope is rewritten to match what
    the legacy key can actually serve.

    Applies whether or not validate() raised — an all-capable `thnv_*` key
    passes validate with default scope ["agent"] but still needs write-back so
    the surface loaded matches what AppContext.scope advertises downstream.
    """
    for name in (
        "BAND_USER_KEY",
        "BAND_AGENT_KEY",
        "BAND_MCP_SCOPE",
        "BAND_MCP_TOOLS",
        "BAND_MCP_ROOM_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BAND_API_KEY", legacy_key)

    from band_mcp.config import (
        ConfigError,
        _legacy_key_capabilities,
        resolve_config,
        validate,
    )

    args = _make_args()
    cli = {
        "user_key": args.user_key,
        "agent_key": args.agent_key,
        "room_id": args.room_id,
        "scope": args.scope,
        "tools": args.tools,
    }
    # Replay the relevant branch of run(): resolve, try validate, apply
    # scope write-back on every pure-legacy invocation.
    import os

    config = resolve_config(cli=cli, env=os.environ)

    try:
        validate(config)
    except ConfigError:
        pass  # pure-legacy invocation keeps booting

    assert server_mod._is_pure_legacy_invocation(args, config) is True
    legacy_human, legacy_agent = _legacy_key_capabilities(config.legacy_key)
    scope_writeback: list[str] = []
    if legacy_agent:
        scope_writeback.append("agent")
    if legacy_human:
        scope_writeback.append("human")
    config = replace(config, scope=scope_writeback)

    assert config.scope == expected_scope


def test_escape_hatch_user_legacy_key_maps_to_human_only(monkeypatch):
    """Specific C2 scenario from the review: `BAND_API_KEY=thnv_u_*` must
    log / register as `['human']`, not `['agent']`.
    """
    for name in (
        "BAND_USER_KEY",
        "BAND_AGENT_KEY",
        "BAND_MCP_SCOPE",
        "BAND_MCP_TOOLS",
        "BAND_MCP_ROOM_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BAND_API_KEY", "thnv_u_xyz")

    from band_mcp.config import _legacy_key_capabilities

    legacy_human, legacy_agent = _legacy_key_capabilities("thnv_u_xyz")
    assert legacy_human is True
    assert legacy_agent is False
