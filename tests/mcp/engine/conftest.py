"""Build real MCP engines for protocol checks."""

from __future__ import annotations

import pytest

from band.integrations.mcp.engine import (
    EngineSpec,
    build_engine,
    build_tool_registration,
    extend_with_chat_id,
)
from band.runtime.tools import TOOL_DEFINITIONS
from band.testing.fake_tools import FakeAgentTools
from tests.mcp.engine.peers import _agent_resolver


@pytest.fixture
async def agent_session_factory():
    """Yields a builder from a room-scoped FakeAgentTools to a connected
    ClientSession over a real (uniform-wrap, embedded-shaped) engine."""

    async def _build(fake: FakeAgentTools, *, definitions=None):
        resolver = _agent_resolver(fake)
        defs = definitions or [
            TOOL_DEFINITIONS[name]
            for name in (
                "band_send_message",
                "band_get_participants",
                "band_lookup_peers",
                "band_create_chatroom",
            )
        ]
        registrations = [
            build_tool_registration(
                definition,
                extend_with_chat_id(definition.input_model, None),
                resolver=resolver,
                strip_chat_id=True,
            )
            for definition in defs
        ]
        spec = EngineSpec(name="test-embedded", tools=tuple(registrations))
        return build_engine(spec)

    return _build
