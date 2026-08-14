"""Router-level tests for ACPServer.

The rest of the server suite calls handlers directly with hand-written
keywords, which cannot catch drift between a handler's parameter names and
the ACP request model the SDK router unpacks into it (``func(**fields)``).
These tests dispatch by JSON-RPC method name through a real
``build_agent_router``, the way an editor reaches the server, so a handler
that no longer matches its upstream request model fails here.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from acp.agent.router import build_agent_router
from acp.exceptions import RequestError
from acp.meta import AGENT_METHODS

from band.integrations.acp.server import ACPServer, run_acp_server
from band.integrations.acp.server_adapter import BandACPServerAdapter

# Payloads keyed by ACP method, in the camelCase wire form an editor sends.
# Every method ACPServer implements is represented.
WIRE_REQUESTS: dict[str, dict[str, Any]] = {
    "initialize": {"protocolVersion": 1},
    "authenticate": {"methodId": "api_key"},
    "session/new": {"cwd": "/workspace", "mcpServers": []},
    "session/load": {"cwd": "/workspace", "sessionId": "session-1", "mcpServers": []},
    "session/list": {},
    "session/set_mode": {"sessionId": "session-1", "modeId": "code"},
    "session/set_config_option": {
        "sessionId": "session-1",
        "configId": "theme",
        "value": "dark",
    },
    "session/prompt": {
        "sessionId": "session-1",
        "prompt": [{"type": "text", "text": "hello"}],
    },
    "session/fork": {"sessionId": "session-1", "cwd": "/workspace"},
    "session/resume": {"sessionId": "session-1", "cwd": "/workspace"},
    "session/close": {"sessionId": "session-1"},
}

# Routes the ACP SDK marks unstable. ACPServer implements all of them and
# advertises fork/resume from initialize(), so run_acp_server() enables them.
UNSTABLE_METHODS = {"session/fork", "session/resume", "session/close"}


def make_server() -> tuple[ACPServer, BandACPServerAdapter]:
    """Build a server over an adapter with a stubbed REST client."""
    adapter = BandACPServerAdapter()
    adapter._rest = AsyncMock()
    adapter.verify_credentials = AsyncMock(return_value=True)
    adapter.create_session = AsyncMock(return_value="session-new")
    adapter.handle_prompt = AsyncMock()
    adapter.on_cleanup = AsyncMock()
    adapter._session_to_room["session-1"] = "room-1"
    adapter._room_to_session["room-1"] = "session-1"
    server = ACPServer(adapter)
    return server, adapter


@pytest.mark.parametrize("method", sorted(WIRE_REQUESTS))
@pytest.mark.asyncio
async def test_every_implemented_method_is_reachable(method: str) -> None:
    """Each method should dispatch into its handler without signature drift.

    A parameter renamed or reordered away from the upstream request model
    surfaces here as a TypeError; a route the server never registered
    surfaces as method_not_found.
    """
    server, _ = make_server()
    router = build_agent_router(server, use_unstable_protocol=True)

    await router(method, WIRE_REQUESTS[method], False)


@pytest.mark.parametrize("method", sorted(UNSTABLE_METHODS))
@pytest.mark.asyncio
async def test_unstable_routes_need_the_band_runner(method: str) -> None:
    """Unstable routes answer method_not_found without the runner's flag.

    This is what run_acp_server() exists to prevent: ACPServer implements
    and advertises these, but a plain acp.run_agent() leaves them dark.
    """
    server, _ = make_server()
    router = build_agent_router(server)

    with pytest.warns(UserWarning, match="unstable protocol"):
        with pytest.raises(RequestError):
            await router(method, WIRE_REQUESTS[method], False)


def test_wire_requests_cover_every_agent_method_the_server_implements() -> None:
    """The payload table should not silently fall behind the server.

    Without this, a newly implemented handler could be added with no
    routing coverage and the parametrized tests above would still pass.
    Membership is checked against ``ACPServer.__dict__`` rather than
    ``hasattr``: ACPServer subclasses the ``Agent`` protocol, so every
    protocol stub is inherited and ``hasattr`` would also be true for
    methods the server does not actually implement.
    """
    implemented = {
        AGENT_METHODS[name]
        for name in AGENT_METHODS
        if name != "session_cancel" and _handler_for(name) in ACPServer.__dict__
    }

    assert implemented == set(WIRE_REQUESTS)


def _handler_for(method_name: str) -> str:
    """Map an AGENT_METHODS key to the ACPServer handler it routes to."""
    overrides = {
        "session_new": "new_session",
        "session_load": "load_session",
        "session_list": "list_sessions",
        "session_set_mode": "set_session_mode",
        "session_set_config_option": "set_config_option",
        "session_prompt": "prompt",
        "session_fork": "fork_session",
        "session_resume": "resume_session",
        "session_close": "close_session",
    }
    return overrides.get(method_name, method_name)


@pytest.mark.asyncio
async def test_run_acp_server_enables_the_routes_the_server_advertises() -> None:
    """The runner should turn the unstable routes on, not leave it to callers."""
    server, _ = make_server()

    with patch(
        "band.integrations.acp.server.run_agent", new=AsyncMock()
    ) as mock_run_agent:
        await run_acp_server(server)

    assert mock_run_agent.await_args.kwargs["use_unstable_protocol"] is True


@pytest.mark.asyncio
async def test_cancel_notification_is_reachable() -> None:
    """session/cancel dispatches as a notification, not a request."""
    server, adapter = make_server()
    adapter.cancel_prompt = AsyncMock()
    router = build_agent_router(server)

    await router("session/cancel", {"sessionId": "session-1"}, True)

    adapter.cancel_prompt.assert_awaited_once_with("session-1")
