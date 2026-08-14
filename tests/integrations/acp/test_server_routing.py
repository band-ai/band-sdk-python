"""Router-level tests for ACPServer.

The rest of the server suite calls handlers directly with hand-written
keywords. The SDK router instead unpacks an ACP request model into the
handler (``func(**fields)``), so a handler whose parameters no longer match
its request model fails only when dispatched. These tests dispatch by
JSON-RPC method name through ``build_agent_router``.
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

# Routes the ACP SDK registers as unstable. ACPServer implements all three;
# run_acp_server() enables them via use_unstable_protocol.
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
    """Each method dispatches into its handler.

    A parameter renamed or reordered against the request model raises
    TypeError; an unregistered route raises method_not_found.
    """
    server, _ = make_server()
    router = build_agent_router(server, use_unstable_protocol=True)

    await router(method, WIRE_REQUESTS[method], False)


@pytest.mark.parametrize("method", sorted(UNSTABLE_METHODS))
@pytest.mark.asyncio
async def test_unstable_routes_need_the_band_runner(method: str) -> None:
    """Unstable routes return method_not_found without use_unstable_protocol.

    Fails if the SDK stabilizes these routes, at which point
    run_acp_server()'s flag is no longer required.
    """
    server, _ = make_server()
    router = build_agent_router(server)

    with pytest.warns(UserWarning, match="unstable protocol"):
        with pytest.raises(RequestError):
            await router(method, WIRE_REQUESTS[method], False)


def test_wire_requests_cover_every_agent_method_the_server_implements() -> None:
    """WIRE_REQUESTS covers every handler ACPServer defines.

    Without this, a new handler added with no entry in WIRE_REQUESTS would
    leave the tests above passing. Membership is tested against
    ``ACPServer.__dict__`` rather than ``hasattr``: ACPServer subclasses the
    ``Agent`` protocol, so ``hasattr`` is also true for inherited stubs the
    server does not define.
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
async def test_run_acp_server_sets_use_unstable_protocol() -> None:
    """run_acp_server() passes use_unstable_protocol=True to run_agent()."""
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
