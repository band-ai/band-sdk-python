"""Resources and fixtures for Google ADK adapter behavior."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio
from google import genai
from google.genai import Client, types

from band.core.types import PlatformMessage
from tests.adapters.genaikit import (
    ScriptedAdapter,
)
from tests.adapters.googleadk.bridge import GoogleADKAdapter, _google_llm
from tests.adapters.googleadk.httppeer import LOCAL_API_KEY, GoogleHTTPPeer, HTTPMode


@pytest_asyncio.fixture(loop_scope="function")
async def google_http(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[GoogleHTTPPeer]:
    peer = GoogleHTTPPeer()
    mode = HTTPMode(getattr(request, "param", HTTPMode.MOCK))
    server = (
        await asyncio.start_server(peer.serve, "127.0.0.1", 0)
        if mode != HTTPMode.MOCK
        else None
    )
    base_url = (
        f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}" if server else None
    )
    if mode == HTTPMode.PROXY:
        assert base_url is not None
        monkeypatch.setenv("HTTP_PROXY", base_url)
        monkeypatch.setenv("NO_PROXY", "")
        base_url = "http://provider.invalid"
    elif server is not None:
        monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")

    def create_client(*, http_options: types.HttpOptions) -> Client:
        retry = http_options.retry_options
        if retry is not None:
            retry = retry.model_copy(
                update={
                    "initial_delay": peer.initial_delay,
                    "max_delay": peer.max_delay,
                    "jitter": 0.001,
                }
            )
        options: dict[str, Any] = {"retry_options": retry}
        if base_url is not None:
            options["base_url"] = base_url
        else:
            options["httpx_async_client"] = None
            options["async_client_args"] = {
                "transport": httpx.MockTransport(peer.respond)
            }
        client = Client(
            api_key=LOCAL_API_KEY,
            http_options=http_options.model_copy(update=options),
        )
        # Signal from native backoff, after the retry decision and before sleeping.
        native_before_sleep = client._api_client._async_retry.before_sleep

        def before_sleep(state: Any) -> None:
            peer.retry_started.set()
            if native_before_sleep is not None:
                native_before_sleep(state)

        client._api_client._async_retry.before_sleep = before_sleep
        peer.clients.append(client)
        return client

    with (
        patch.object(genai, "Client", create_client),
        patch.object(_google_llm, "Client", create_client),
    ):
        try:
            yield peer
        finally:
            for client in peer.clients:
                try:
                    await client.aio.aclose()
                finally:
                    await client._api_client._async_httpx_client.aclose()
                    client.close()
            if server is not None:
                server.close()
                await server.wait_closed()


@pytest.fixture
def scripted_adapter(google_http: GoogleHTTPPeer) -> ScriptedAdapter:
    async def build(*responses: types.GenerateContentResponse, **features: Any) -> Any:
        google_http.responses = list(responses)
        adapter = GoogleADKAdapter(**features)
        await adapter.on_started("TestBot", "Test bot")
        return adapter

    return build


@pytest.fixture
def sample_message():
    """Create a sample platform message."""
    return PlatformMessage(
        id="msg-123",
        room_id="room-123",
        content="Hello, agent!",
        sender_id="user-456",
        sender_type="User",
        sender_name="Alice",
        message_type="text",
        metadata={},
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def mock_tools():
    """Create mock AgentToolsProtocol (MagicMock base, AsyncMock methods)."""
    tools = MagicMock()
    tools.get_tool_schemas = MagicMock(return_value=[])
    tools.get_openai_tool_schemas = MagicMock(
        return_value=[
            {
                "type": "function",
                "function": {
                    "name": "band_send_message",
                    "description": "Send a message",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "mentions": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["content", "mentions"],
                    },
                },
            }
        ]
    )
    tools.send_message = AsyncMock(return_value={"status": "sent"})
    tools.send_event = AsyncMock(return_value={"status": "sent"})
    tools.send_failure = AsyncMock(return_value={"status": "sent"})
    tools.execute_tool_call = AsyncMock(return_value={"status": "success"})
    return tools
