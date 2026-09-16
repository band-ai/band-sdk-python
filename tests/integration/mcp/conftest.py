"""Fixtures for live-API band-mcp integration tests.

These tests exercise the CLI door's real path end-to-end against a real Band
API: ``standalone_spec(config, resolver)`` builds an ``EngineSpec`` from a
resolved ``Config``, ``build_engine(spec)`` mounts it on a real ``FastMCP``,
and dispatch goes through ``mcp._tool_manager.call_tool`` -- the same
register -> validate -> dispatch -> HTTP path a real ``band-mcp`` process
takes, minus the transport.

Credentials are loaded from ``.env.test``. Every test is skipped unless
``BAND_AGENT_KEY`` is set; once the suite runs, ``BAND_USER_KEY`` and
``BAND_API_KEY_2`` are required too (see ``BandTestSettings`` below) --
a partial-credential environment would silently narrow which scopes and
topologies get tested, which is exactly the class of bug this suite exists
to catch.

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
from pydantic import AliasChoices, Field

from band.integrations.mcp.engine import build_engine
from band_mcp import shared
from band_mcp.config import Config, Scope, ToolGroup
from band_mcp.server import standalone_spec
from band_mcp.shared import StandaloneResolver, build_standalone_resolver
from thenvoi_testing.markers import skip_without_env
from thenvoi_testing.settings import BaseTestSettings

from tests.paths import ENV_TEST_FILE


class BandTestSettings(BaseTestSettings):
    """Settings for band-mcp integration tests, loaded from ``.env.test``.

    ``band_user_key``/``band_agent_key_2`` fall back to the sibling
    ``tests/conftest_integration.py`` suite's env var names
    (``BAND_API_KEY_USER``/``BAND_API_KEY_2``): both are real Band API keys
    for the same test account, just named differently by that suite's own
    convention -- an alias reuses the existing credential instead of
    requiring a second, duplicate ``.env.test`` entry.
    """

    band_user_key: str = Field(
        "", validation_alias=AliasChoices("BAND_USER_KEY", "BAND_API_KEY_USER")
    )
    band_agent_key: str = ""
    band_agent_key_2: str = Field(
        "", validation_alias=AliasChoices("BAND_AGENT_KEY_2", "BAND_API_KEY_2")
    )
    band_base_url: str = "https://app.band.ai"
    test_agent_id: str = ""
    test_agent_id_2: str = ""

    _env_file_path: Path = ENV_TEST_FILE


test_settings = BandTestSettings()


def get_user_key() -> str | None:
    return test_settings.band_user_key or None


def get_agent_key() -> str | None:
    return test_settings.band_agent_key or None


def get_agent_key_2() -> str | None:
    return test_settings.band_agent_key_2 or None


def get_base_url() -> str:
    return test_settings.band_base_url


def get_test_agent_id() -> str | None:
    return test_settings.test_agent_id or None


def get_test_agent_id_2() -> str | None:
    return test_settings.test_agent_id_2 or None


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


def _unwrap(payload: Any) -> Any:
    """Unwrap a ``{"data": ...}`` envelope; return payload unchanged if bare."""
    return payload.get("data") if isinstance(payload, dict) else payload


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
    """Resolve the primary agent's Config. Both credentials are required.

    A key missing here is a real `.env.test` setup gap, not something to
    silently work around by narrowing scope -- fail loudly instead.
    """
    user_key = get_user_key()
    agent_key = get_agent_key()
    if not user_key or not agent_key:
        raise RuntimeError(
            "tests/integration/mcp/ requires both BAND_AGENT_KEY and "
            "BAND_USER_KEY (or BAND_API_KEY_USER) set in .env.test."
        )

    return Config(
        user_key=user_key,
        agent_key=agent_key,
        scope=[Scope.AGENT, Scope.HUMAN],
        tools=[ToolGroup.CONTACTS, ToolGroup.MEMORY],
    )


@pytest.fixture(scope="session")
def second_agent_config() -> Config:
    """Resolve a second, genuinely distinct agent identity's Config.

    Backs multi-agent scenarios (one real agent adding/mentioning another),
    as opposed to ``live_config``'s single agent plus its human owner.
    """
    agent_key = get_agent_key_2()
    if not agent_key:
        raise RuntimeError(
            "Multi-agent tests/integration/mcp/ scenarios require "
            "BAND_AGENT_KEY_2 (or BAND_API_KEY_2) set in .env.test."
        )
    return Config(agent_key=agent_key, user_key=None, scope=[Scope.AGENT], tools=[])


def _build_harness(config: Config, monkeypatch: pytest.MonkeyPatch) -> LiveHarness:
    """Build a real engine (standalone_spec + build_engine) and return a driver."""
    monkeypatch.setattr(shared.settings, "band_base_url", get_base_url())

    resolver: StandaloneResolver = build_standalone_resolver(config)
    spec = standalone_spec(config, resolver)
    mcp = build_engine(spec)

    return LiveHarness(mcp, list(config.scope))


@pytest.fixture
def harness(live_config: Config, monkeypatch: pytest.MonkeyPatch) -> LiveHarness:
    """Primary agent's driver (agent + human scope)."""
    return _build_harness(live_config, monkeypatch)


@pytest.fixture
def second_agent_harness(
    second_agent_config: Config, monkeypatch: pytest.MonkeyPatch
) -> LiveHarness:
    """Second, genuinely distinct agent identity's driver (agent scope only)."""
    return _build_harness(second_agent_config, monkeypatch)


@pytest.fixture
async def agent_room(harness: LiveHarness):
    """Create a throwaway agent chat room, yield its id.

    No teardown: the Band REST API has no room-delete endpoint, so every live
    run of a test using this fixture permanently leaks the room it creates
    (same known platform limitation noted in ``tests/integration/test_agent_contacts.py``).
    These tests require real API access and are skipped in CI.
    """
    created = await harness.call("band_create_chatroom")
    room_id = _extract_id(created)
    assert room_id, f"band_create_chatroom returned no id: {created!r}"
    yield room_id


async def add_room_owner(harness: LiveHarness, room_id: str) -> str:
    """Add the room-owning human as a participant; return their id to @mention.

    A freshly created agent room has no other participant, and self-mention
    is disallowed by design -- the owner is always resolvable via
    ``band_lookup_peers`` (the ``type: "User"`` entry). A *known* second
    identity (e.g. a second test agent) doesn't need this lookup at all --
    call ``band_add_participant`` with its id directly.
    """
    peers = _unwrap(
        await harness.call("band_lookup_peers", chat_id=room_id, page=1, page_size=100)
    )
    owner_id = next(p for p in peers if p["type"] == "User")["id"]
    await harness.call("band_add_participant", chat_id=room_id, identifier=owner_id)
    return owner_id
