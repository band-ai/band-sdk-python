"""Router-level tests for ACPServer.

The rest of the server suite calls handlers directly with hand-written
keywords. The SDK router instead resolves handlers with ``getattr`` and
invokes them as ``func(**request_model_fields)``, so a handler whose
parameters no longer match its request model fails only when dispatched.
These tests dispatch by JSON-RPC method name through ``build_agent_router``.

Only the REST boundary is faked (``mock_rest_client``); the adapter runs its
real session bookkeeping, so assertions are on observable state.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from acp.agent.router import build_agent_router
from acp.exceptions import RequestError
from acp.interfaces import Agent
from acp.meta import AGENT_METHODS
from acp.schema import (
    ForkSessionResponse,
    InitializeResponse,
    ListSessionsResponse,
    NewSessionResponse,
    ResumeSessionResponse,
)

from band.integrations.acp.server import ACPServer, run_acp_server
from band.integrations.acp.server_adapter import BandACPServerAdapter
from tests.integrations.acp.conftest import has_pending_prompt, wait_for_pending_prompt

# Substituted with the live session id at dispatch time.
SESSION = "<session>"

# method -> (camelCase wire payload, expected router result).
#
# The expectation is a response class where the router returns the model, and
# a literal where it returns a serialized payload: the SDK applies
# normalize_result to session/load, session/close, session/set_mode,
# session/set_config_option and authenticate, whose responses carry no
# non-default fields and so serialize to {}.
#
# session/prompt is excluded: it blocks until the peer replies, so it has a
# dedicated test below.
REQUESTS: dict[str, tuple[dict[str, Any], Any]] = {
    "initialize": ({"protocolVersion": 1}, InitializeResponse),
    "authenticate": ({"methodId": "api_key"}, {}),
    "session/new": ({"cwd": "/workspace", "mcpServers": []}, NewSessionResponse),
    "session/load": (
        {"cwd": "/workspace", "sessionId": SESSION, "mcpServers": []},
        {},
    ),
    "session/list": ({}, ListSessionsResponse),
    "session/set_mode": ({"sessionId": SESSION, "modeId": "code"}, {}),
    "session/set_config_option": (
        {"sessionId": SESSION, "configId": "theme", "value": "dark"},
        {},
    ),
    "session/fork": (
        {"sessionId": SESSION, "cwd": "/workspace"},
        ForkSessionResponse,
    ),
    "session/resume": (
        {"sessionId": SESSION, "cwd": "/workspace"},
        ResumeSessionResponse,
    ),
    "session/close": ({"sessionId": SESSION}, {}),
}

BLOCKING_METHODS = {"session/prompt"}

# Routes the ACP SDK registers as unstable. ACPServer implements all three;
# run_acp_server() enables them via use_unstable_protocol.
UNSTABLE_METHODS = {"session/fork", "session/resume", "session/close"}

# AGENT_METHODS key -> ACPServer handler name, where they differ.
HANDLER_NAMES = {
    "session_new": "new_session",
    "session_load": "load_session",
    "session_list": "list_sessions",
    "session_set_mode": "set_session_mode",
    "session_set_config_option": "set_config_option",
    "session_prompt": "prompt",
    "session_fork": "fork_session",
    "session_resume": "resume_session",
    "session_close": "close_session",
    "session_cancel": "cancel",
}


def payload_for(method: str, session_id: str) -> dict[str, Any]:
    """Return the wire payload for a method, bound to a live session id."""
    return {
        key: session_id if value == SESSION else value
        for key, value in REQUESTS[method][0].items()
    }


@pytest.fixture
def adapter(mock_rest_client: MagicMock) -> BandACPServerAdapter:
    """Adapter with only the REST boundary faked."""
    adapter = BandACPServerAdapter()
    adapter._rest = mock_rest_client
    return adapter


@pytest.fixture
def server(adapter: BandACPServerAdapter) -> ACPServer:
    return ACPServer(adapter)


@pytest.fixture
def router(server: ACPServer) -> Any:
    return build_agent_router(cast(Agent, server), use_unstable_protocol=True)


@pytest.fixture
async def session_id(adapter: BandACPServerAdapter) -> str:
    """A session created through the adapter's real create_session()."""
    return await adapter.create_session(cwd="/workspace")


@pytest.fixture
def dispatched_prompt(
    router: Any, adapter: BandACPServerAdapter
) -> Callable[[str, str], AbstractAsyncContextManager[str]]:
    """Factory for driving a session/prompt dispatch as a background task.

    ``session/prompt`` blocks until the peer replies, so a test that wants to
    observe the pending turn dispatches it as a task and waits for it to
    reach the room. On exit, resolves the prompt and awaits the dispatch
    task regardless of how the test body already resolved it (a repeat
    ``cancel_prompt`` is a no-op), so a failing assertion never leaves a
    dangling task or an unresolved prompt for a later test to trip over.

    Yields the room id the prompt landed in.
    """

    @asynccontextmanager
    async def _dispatch(session_id: str, text: str) -> AsyncIterator[str]:
        task = asyncio.create_task(
            router(
                "session/prompt",
                {"sessionId": session_id, "prompt": [{"type": "text", "text": text}]},
                False,
            )
        )
        room_id = adapter.get_room_for_session(session_id)
        await wait_for_pending_prompt(adapter, room_id)
        try:
            yield room_id
        finally:
            await adapter.cancel_prompt(session_id)
            await task

    return _dispatch


@pytest.mark.parametrize("method", sorted(REQUESTS))
@pytest.mark.asyncio
async def test_dispatch_returns_the_declared_response(
    method: str, router: Any, session_id: str
) -> None:
    """Each method dispatches and returns its ACP response type.

    A parameter renamed or reordered against the request model raises
    TypeError here; an unregistered route raises method_not_found.
    """
    expected = REQUESTS[method][1]

    result = await router(method, payload_for(method, session_id), False)

    if isinstance(expected, type):
        assert isinstance(result, expected)
    else:
        assert result == expected


@pytest.mark.asyncio
async def test_new_session_creates_a_room_and_registers_the_session(
    router: Any, adapter: BandACPServerAdapter, mock_rest_client: MagicMock
) -> None:
    """session/new maps a new ACP session onto a freshly created Band room."""
    result = await router("session/new", {"cwd": "/workspace", "mcpServers": []}, False)

    mock_rest_client.agent_api_chats.create_agent_chat.assert_awaited_once()
    assert adapter.has_session(result.session_id)
    assert adapter.get_session_cwd(result.session_id) == "/workspace"


@pytest.mark.asyncio
async def test_close_session_removes_the_session(
    router: Any, adapter: BandACPServerAdapter, session_id: str
) -> None:
    """session/close drops the session from the adapter."""
    assert adapter.has_session(session_id)

    await router("session/close", {"sessionId": session_id}, False)

    assert not adapter.has_session(session_id)


@pytest.mark.asyncio
async def test_load_session_reports_a_miss_for_unknown_session(
    router: Any, adapter: BandACPServerAdapter
) -> None:
    """session/load returns an empty result and creates nothing."""
    result = await router(
        "session/load",
        {"cwd": "/workspace", "sessionId": "never-created", "mcpServers": []},
        False,
    )

    assert result == {}
    assert not adapter.has_session("never-created")


@pytest.mark.asyncio
async def test_fork_session_creates_a_second_session(
    router: Any, adapter: BandACPServerAdapter, session_id: str
) -> None:
    """session/fork adds a session without disturbing the original."""
    result = await router(
        "session/fork", {"sessionId": session_id, "cwd": "/workspace"}, False
    )

    assert result.session_id != session_id
    assert adapter.has_session(session_id)
    assert adapter.has_session(result.session_id)


@pytest.mark.asyncio
async def test_set_mode_is_applied_to_the_session(
    router: Any, adapter: BandACPServerAdapter, session_id: str
) -> None:
    """session/set_mode records the mode the editor selected."""
    await router("session/set_mode", {"sessionId": session_id, "modeId": "code"}, False)

    assert adapter.get_session_mode(session_id) == "code"


@pytest.mark.asyncio
async def test_prompt_posts_the_text_to_the_room(
    dispatched_prompt: Callable[[str, str], AbstractAsyncContextManager[str]],
    session_id: str,
    mock_rest_client: MagicMock,
) -> None:
    """session/prompt sends the prompt to the room before waiting for a reply."""
    async with dispatched_prompt(session_id, "hello"):
        sent = mock_rest_client.agent_api_messages.create_agent_chat_message
        sent.assert_awaited_once()
        assert "hello" in sent.await_args.kwargs["message"].content


@pytest.mark.asyncio
async def test_cancel_notification_releases_the_pending_prompt(
    router: Any,
    adapter: BandACPServerAdapter,
    dispatched_prompt: Callable[[str, str], AbstractAsyncContextManager[str]],
    session_id: str,
) -> None:
    """session/cancel dispatches as a notification and unblocks the prompt."""
    async with dispatched_prompt(session_id, "hi") as room_id:
        await router("session/cancel", {"sessionId": session_id}, True)

        assert not has_pending_prompt(adapter, room_id)


@pytest.mark.parametrize("method", sorted(UNSTABLE_METHODS))
@pytest.mark.asyncio
async def test_unstable_routes_need_use_unstable_protocol(
    method: str, server: ACPServer, session_id: str
) -> None:
    """Unstable routes return method_not_found when the flag is off.

    Fails if the SDK stabilizes these routes, at which point
    run_acp_server()'s flag is no longer required.
    """
    default_router = build_agent_router(cast(Agent, server))

    with (
        pytest.warns(UserWarning, match="unstable protocol"),
        pytest.raises(RequestError),
    ):
        await default_router(method, payload_for(method, session_id), False)


@pytest.mark.asyncio
async def test_run_acp_server_sets_use_unstable_protocol(server: ACPServer) -> None:
    """run_acp_server() passes use_unstable_protocol=True to run_agent()."""
    with patch(
        "band.integrations.acp.server.run_agent", new=AsyncMock()
    ) as mock_run_agent:
        await run_acp_server(server)

    assert mock_run_agent.await_args.kwargs["use_unstable_protocol"] is True


def test_requests_cover_every_handler_acp_server_defines() -> None:
    """REQUESTS covers every ACP method ACPServer handles.

    Without this, a new handler added with no entry would leave the
    dispatch tests above silently passing. Membership is tested against
    ``ACPServer.__dict__`` rather than ``hasattr`` so that inherited
    attributes cannot register as implemented handlers.
    """
    handled = {
        AGENT_METHODS[name]
        for name in AGENT_METHODS
        if HANDLER_NAMES.get(name, name) in ACPServer.__dict__
    }

    assert handled == set(REQUESTS) | BLOCKING_METHODS | {"session/cancel"}
