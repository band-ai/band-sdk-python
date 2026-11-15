"""Resources and fixtures for Google ADK adapter behavior."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import google.auth
import httpx
import pytest
import pytest_asyncio
from google import genai
from google.auth.exceptions import DefaultCredentialsError
from google.genai import Client, types
from google.oauth2.credentials import Credentials

from band.core.types import PlatformMessage
from tests.adapters.genaikit import (
    ScriptedAdapter,
)
from tests.adapters.googleadk.bridge import (
    GoogleADKAdapter,
    _google_adk_mod,
    _google_llm,
)
from tests.adapters.googleadk.clientkit import AUTH_FAILURE, GEMINI_MODEL
from tests.adapters.googleadk.httppeer import LOCAL_API_KEY, GoogleHTTPPeer, HTTPMode


@pytest.fixture
def google_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_GENAI_USE_VERTEXAI",
        "GOOGLE_GENAI_USE_ENTERPRISE",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_GEMINI_BASE_URL",
        "GOOGLE_VERTEX_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", LOCAL_API_KEY)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "offline")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.setattr(
        google.auth,
        "default",
        lambda **kwargs: (Credentials(token="offline-token"), "offline"),
    )


@pytest.fixture
def backend_env(
    request: pytest.FixtureRequest,
    google_environment: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if request.param is not None:
        monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", request.param)


@pytest.fixture
def header_shape(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Native ADK has no injection seam for its version-specific descriptor.
    monkeypatch.setattr(_google_llm.Gemini, "_tracking_headers", request.param)


@pytest_asyncio.fixture
async def native_model(
    request: pytest.FixtureRequest, google_environment: None
) -> AsyncIterator[Any]:
    _, _, _, model_class, _ = _google_adk_mod._require_adk()
    model = model_class(model=getattr(request, "param", GEMINI_MODEL))
    try:
        yield model
    finally:
        await model.aclose()


@pytest.fixture
def missing_google_credentials(
    google_environment: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("GOOGLE_API_KEY", "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION"):
        monkeypatch.delenv(name)
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "false")

    def unavailable(**kwargs: Any) -> Any:
        raise DefaultCredentialsError(AUTH_FAILURE)

    monkeypatch.setattr(google.auth, "default", unavailable)


@pytest.fixture
def closed_google_clients(monkeypatch: pytest.MonkeyPatch) -> list[httpx.AsyncClient]:
    clients: list[httpx.AsyncClient] = []
    real_close = httpx.AsyncClient.aclose

    async def close(client: httpx.AsyncClient) -> None:
        await real_close(client)
        clients.append(client)

    monkeypatch.setattr(httpx.AsyncClient, "aclose", close)
    return clients


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

    def create_client(
        *, http_options: types.HttpOptions, vertexai: bool | None = None
    ) -> Client:
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
            vertexai=vertexai,
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

    with patch.object(genai, "Client", create_client):
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
