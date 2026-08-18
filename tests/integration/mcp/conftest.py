"""Fixtures for live-API band-mcp integration tests.

These tests exercise the CLI door's real path end-to-end against a real Band
API: ``standalone_spec(config, resolver)`` builds an ``EngineSpec`` from a
resolved ``Config``, ``build_engine(spec)`` mounts it on a real ``FastMCP``,
and dispatch goes through ``mcp._tool_manager.call_tool`` -- the same
register -> validate -> dispatch -> HTTP path a real ``band-mcp`` process
takes, minus the transport.

Credentials are loaded from ``.env.test``. Every test is skipped unless
``BAND_AGENT_KEY`` is set.

Run:
    uv run --all-packages pytest tests/integration/mcp/ -v -s --no-cov

Skip (unit only):
    uv run --all-packages pytest tests/ --ignore=tests/integration/
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP

from band.integrations.mcp.engine import build_engine
from band_mcp import shared
from band_mcp.config import Config, Scope, ToolGroup
from band_mcp.server import standalone_spec
from band_mcp.shared import StandaloneResolver, build_standalone_resolver
from thenvoi_testing.markers import skip_without_env
from thenvoi_testing.settings import BaseTestSettings

from tests.paths import ENV_TEST_FILE


class BandTestSettings(BaseTestSettings):
    """Settings for band-mcp integration tests, loaded from ``.env.test``."""

    band_user_key: str = ""
    band_agent_key: str = ""
    band_base_url: str = "https://app.band.ai"
    test_agent_id: str = ""

    _env_file_path: Path = ENV_TEST_FILE


test_settings = BandTestSettings()


def get_user_key() -> str | None:
    return test_settings.band_user_key or None


def get_agent_key() -> str | None:
    return test_settings.band_agent_key or None


def get_base_url() -> str:
    return test_settings.band_base_url


def get_test_agent_id() -> str | None:
    return test_settings.test_agent_id or None


# Skip marker for the whole live suite.
requires_api = skip_without_env("BAND_AGENT_KEY")


def _extract_id(payload: Any) -> str | None:
    """Pull an id out of a tool response.

    Most tools wrap results as ``{"id": ...}`` or ``{"data": {"id": ...}}``,
    but ``band_create_chatroom``'s underlying SDK method returns the room id
    as a bare ``str`` (``AgentTools.create_chatroom() -> str``), which
    ``_serialize()``/``LiveHarness.call()`` round-trips through JSON as a
    plain string, not a dict -- so that shape is the id itself.
    """
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        if "id" in payload:
            return payload["id"]
        data = payload.get("data")
        if isinstance(data, dict):
            return data.get("id")
    return None


class LiveHarness:
    """Drives the standalone engine end-to-end against a live API.

    ``call(name, **args)`` validates and dispatches a tool exactly as the MCP
    server would, returning the parsed JSON payload (or the raw string when the
    result is not JSON).
    """

    def __init__(self, mcp: FastMCP, scope: list[str]) -> None:
        self._mcp = mcp
        self.scope = scope

    async def names(self) -> set[str]:
        return {t.name for t in await self._mcp.list_tools()}

    async def call_raw(self, name: str, **args: Any) -> str:
        result = await self._mcp._tool_manager.call_tool(name, args)
        # FastMCP returns the handler's string return wrapped in content; the
        # engine's registrations return a JSON string via ``_serialize``.
        if isinstance(result, str):
            return result
        if isinstance(result, (list, tuple)) and result:
            first = result[0]
            return getattr(first, "text", str(first))
        return getattr(result, "text", str(result))

    async def call(self, name: str, **args: Any) -> Any:
        raw = await self.call_raw(name, **args)
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw


@pytest.fixture(scope="session")
def live_config() -> Config:
    """Resolve a Config from whichever live credentials `.env.test` sets."""
    user_key = get_user_key()
    agent_key = get_agent_key()
    if not user_key and not agent_key:
        pytest.skip("Neither BAND_USER_KEY nor BAND_AGENT_KEY is set")

    scope: list[Scope] = []
    if agent_key:
        scope.append(Scope.AGENT)
    if user_key:
        scope.append(Scope.HUMAN)

    return Config(
        user_key=user_key,
        agent_key=agent_key,
        scope=scope,
        tools=[ToolGroup.CONTACTS, ToolGroup.MEMORY],
    )


@pytest.fixture
def harness(live_config: Config, monkeypatch: pytest.MonkeyPatch) -> LiveHarness:
    """Build a real engine (standalone_spec + build_engine) and return a driver."""
    monkeypatch.setattr(shared.settings, "band_base_url", get_base_url())

    resolver: StandaloneResolver = build_standalone_resolver(live_config)
    spec = standalone_spec(live_config, resolver)
    mcp = build_engine(spec)

    return LiveHarness(mcp, list(live_config.scope))


@pytest.fixture
async def agent_room(harness: LiveHarness):
    """Create a throwaway agent chat room, yield its id (agent scope only)."""
    if "agent" not in harness.scope:
        pytest.skip("agent scope not served by this key")

    created = await harness.call("band_create_chatroom")
    room_id = _extract_id(created)
    if not room_id:
        pytest.skip(f"could not create agent chat room: {created!r}")
    yield room_id
