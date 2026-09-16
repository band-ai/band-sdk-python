"""Unit tests for `band_mcp.server`.

Focused on `_health_check`, the piece of `run()` with non-trivial branching
that doesn't require actually starting FastMCP.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from band_mcp import server as server_mod


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------


def _resolver_with_rest(human_rest: object | None, agent_rest: object | None) -> object:
    """A resolver-shaped stand-in: _health_check only reads .human_rest/.agent_rest."""
    return SimpleNamespace(human_rest=human_rest, agent_rest=agent_rest)


async def test_health_check_checks_both_configured_surfaces():
    human_rest = SimpleNamespace(
        human_api_agents=SimpleNamespace(list_my_agents=AsyncMock(return_value=[]))
    )
    agent_rest = SimpleNamespace(
        agent_api_identity=SimpleNamespace(get_agent_me=AsyncMock(return_value={}))
    )

    result = await server_mod._health_check(_resolver_with_rest(human_rest, agent_rest))

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

    result = await server_mod._health_check(_resolver_with_rest(human_rest, agent_rest))

    assert result == "Failed | agent | agent denied"
    human_rest.human_api_agents.list_my_agents.assert_awaited_once()
    agent_rest.agent_api_identity.get_agent_me.assert_awaited_once()
