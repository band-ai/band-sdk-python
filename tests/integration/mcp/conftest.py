"""Fixtures for live-API band-mcp integration tests (post-INT-352 architecture).

These tests exercise the SDK-driven registrar end-to-end against a real Band
API. Unlike the in-process ``test_forwarding.py`` suite (which mocks the SDK
tools), these build a real ``AppContext`` — real ``AsyncRestClient`` plus real
``band-sdk`` ``HumanTools`` / ``AgentTools`` — register the tools on a
``FastMCP`` instance, and dispatch through ``mcp._tool_manager.call_tool`` so
the full register -> validate -> dispatch -> HTTP path is covered.

Credentials are loaded from ``.env.test``. Every test is skipped unless
``BAND_API_KEY`` is set.

Run:
    uv run --all-packages pytest tests/integration/mcp/ -v -s --no-cov

Skip (unit only):
    uv run --all-packages pytest tests/ --ignore=tests/integration/
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP

from band_mcp import shared
from band_mcp.config import Config, _legacy_key_capabilities
from band_mcp.shared import build_app_context
from band_mcp.tools.registrar import register_tools
from thenvoi_testing.markers import skip_without_env
from thenvoi_testing.settings import BaseTestSettings

from tests.paths import ENV_TEST_FILE


class BandTestSettings(BaseTestSettings):
    """Settings for band-mcp integration tests, loaded from ``.env.test``."""

    band_api_key: str = ""
    band_base_url: str = "https://app.band.ai"
    test_agent_id: str = ""

    _env_file_path: Path = ENV_TEST_FILE


test_settings = BandTestSettings()


def get_api_key() -> str | None:
    return test_settings.band_api_key or None


def get_base_url() -> str:
    return test_settings.band_base_url


def get_test_agent_id() -> str | None:
    return test_settings.test_agent_id or None


# Skip marker for the whole live suite.
requires_api = skip_without_env("BAND_API_KEY")


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
    """Drives the SDK registrar end-to-end against a live API.

    ``call(name, **args)`` validates and dispatches a tool exactly as the MCP
    server would, returning the parsed JSON payload (or the raw string when the
    result is not JSON).
    """

    def __init__(self, mcp: FastMCP, app_context: Any, scope: list[str]) -> None:
        self._mcp = mcp
        self._ctx = SimpleNamespace(
            request_context=SimpleNamespace(lifespan_context=app_context)
        )
        self.scope = scope
        self.app_context = app_context

    async def names(self) -> set[str]:
        return {t.name for t in await self._mcp.list_tools()}

    async def call_raw(self, name: str, **args: Any) -> str:
        result = await self._mcp._tool_manager.call_tool(name, args, context=self._ctx)
        # FastMCP returns the handler's string return wrapped in content; the
        # registrar handlers return a JSON string via ``_serialize``.
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
    """Resolve a Config from ``BAND_API_KEY``, scoped to the key's capabilities.

    Mirrors the server's pure-legacy path: the legacy key's prefix decides
    which scopes are served.
    """
    key = get_api_key()
    if not key:
        pytest.skip("BAND_API_KEY not set")

    can_human, can_agent = _legacy_key_capabilities(key)
    scope: list[Any] = []
    if can_agent:
        scope.append("agent")
    if can_human:
        scope.append("human")
    if not scope:
        pytest.skip(f"BAND_API_KEY prefix serves no known scope: {key[:8]}...")

    return Config(scope=scope, tools=["contacts", "memory"], legacy_key=key)


@pytest.fixture
def harness(live_config: Config, monkeypatch: pytest.MonkeyPatch) -> LiveHarness:
    """Build a live ``AppContext`` + registered ``FastMCP`` and return a driver."""
    # build_app_context reads the global settings for the base URL; the
    # per-scope credentials come from ``live_config`` (whose legacy_key is
    # resolved per scope). Passing the config — not None — is what triggers
    # construction of the HumanTools singleton.
    monkeypatch.setattr(shared.settings, "band_api_key", get_api_key())
    monkeypatch.setattr(shared.settings, "band_base_url", get_base_url())

    app_context = build_app_context(live_config)

    mcp = FastMCP(name="integration")
    register_tools(mcp, live_config)

    return LiveHarness(mcp, app_context, list(live_config.scope))


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
