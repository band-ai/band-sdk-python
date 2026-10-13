"""Tests for GoogleADKAdapter.

Tests for shared adapter behavior (initialization defaults, custom kwargs,
history_converter, on_started agent_name/description, on_message callable,
cleanup safety) live in tests/framework_conformance/test_adapter_conformance.py.
This file contains Google ADK-specific behavior: system prompt rendering,
session management, tool bridging, custom tools, and error handling.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio
from google import genai
from google.genai import Client, types
from google.genai.errors import APIError
from pydantic import BaseModel, Field

from band.core.protocols import (
    GENERIC_PROVIDER_FAILURE_MESSAGE,
)
from band.core.types import ALL_CAPABILITIES, Capability, Emit, PlatformMessage
from band.runtime.tools import AgentTools, BandTool
from band.testing import (
    FakeAgentTools,
    failure_reports,
)
from tests.adapters.genaikit import (
    SCRIPTED_USAGE,
    ModelFailureCases,
    PlatformSchemaFakeTools,
    ScriptedAdapter,
    no_candidates,
    text_reply,
    tool_call,
)
from tests.adapters.usage_events import recorded_usage_payloads
from tests.framework_conformance.turnprobes import (
    CUSTOM_TOOL_DECLARATIONS,
    DispatchingFakeTools,
    turn_input,
)

pytest.importorskip("google.adk", reason="google-adk not installed")

# Imports below require google-adk; guarded by importorskip above.
_google_adk_mod = importlib.import_module("band.adapters.google_adk")
GoogleADKAdapter = _google_adk_mod.GoogleADKAdapter
GoogleADKAdapterConfig = _google_adk_mod.GoogleADKAdapterConfig
_get_tool_bridge_class = _google_adk_mod._get_tool_bridge_class
_BandToolBridge = _get_tool_bridge_class()
_sanitize_adk_agent_name = _google_adk_mod._sanitize_adk_agent_name
_google_llm = importlib.import_module("google.adk.models.google_llm")


LOCAL_API_KEY = "local-test-key"
EFFECT_ARGS = {"content": "Completed effect", "mentions": ["@alice"]}


class HTTPMode(StrEnum):
    MOCK = "mock"
    TCP = "tcp"
    PROXY = "proxy"


class RecoveryTools(DispatchingFakeTools, PlatformSchemaFakeTools):
    """Advertise platform schemas and execute the observable fake tool effects."""


class GoogleHTTPPeer:
    """Script provider HTTP responses while retaining the real ADK tool loop."""

    def __init__(self) -> None:
        self.responses: list[int | types.GenerateContentResponse | None] = []
        self.requests: list[httpx.Request] = []
        self.clients: list[Client] = []
        self.initial_delay = 0.001
        self.max_delay = 0.001
        self.retry_started = asyncio.Event()

    def respond(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self.responses.pop(0)
        assert response is not None, "Disconnects require the TCP peer"
        if isinstance(response, int):
            return httpx.Response(
                response,
                json={"error": {"code": response, "message": "Provider unavailable"}},
            )
        return httpx.Response(
            200, json=response.model_dump(mode="json", by_alias=True, exclude_none=True)
        )

    @property
    def generation_requests(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.requests]

    @property
    def clients_closed(self) -> bool:
        return bool(self.clients) and all(
            client._api_client._httpx_client.is_closed
            and client._api_client._async_httpx_client.is_closed
            for client in self.clients
        )

    async def serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            request_headers = httpx.Headers(
                line.split(b":", 1)
                for line in headers.split(b"\r\n")[1:]
                if b":" in line
            )
            content = await reader.readexactly(int(request_headers["content-length"]))
            request = httpx.Request(
                "POST",
                "http://localhost/generate",
                headers=request_headers,
                content=content,
            )
            if self.responses[0] is None:
                self.responses.pop(0)
                self.requests.append(request)
                return
            response = self.respond(request)
            writer.write(
                f"HTTP/1.1 {response.status_code} {response.reason_phrase}\r\n"
                "Content-Type: application/json\r\nConnection: close\r\n"
                f"Content-Length: {len(response.content)}\r\n\r\n".encode()
                + response.content
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()


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


class TestInitialization:
    """Tests for adapter initialization."""

    @pytest.mark.asyncio
    async def test_runner_agent_uses_configured_model(self, mock_tools):
        adapter = GoogleADKAdapter(GoogleADKAdapterConfig(model="gemini-2.5-pro"))
        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")

        runner = adapter._create_runner(mock_tools)

        assert runner.agent.model.model == "gemini-2.5-pro"
        await runner.close()

    @pytest.mark.asyncio
    async def test_custom_section_is_rendered_into_the_prompt(self):
        adapter = GoogleADKAdapter(GoogleADKAdapterConfig(custom_section="Be helpful."))
        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")

        assert "Be helpful." in adapter._system_prompt

    @pytest.mark.parametrize("field", ["max_history_messages", "max_transcript_chars"])
    def test_history_bounds_must_be_positive(self, field):
        with pytest.raises(
            ValueError, match=f"{field}\n  Input should be greater than 0"
        ):
            GoogleADKAdapterConfig(**{field: 0})

    @pytest.mark.parametrize(
        ("value", "match"),
        [
            (-1, "max_retries\n  Input should be greater than or equal to 0"),
            (11, "max_retries\n  Input should be less than or equal to 10"),
        ],
    )
    def test_max_retries_rejects_out_of_range_budgets(self, value: int, match: str):
        with pytest.raises(ValueError, match=match):
            GoogleADKAdapterConfig(max_retries=value)

    def test_execution_reporting_default(self):
        """Should default emit to everything the adapter supports."""
        adapter = GoogleADKAdapter()
        assert Emit.TOOL_CALLS in adapter.features.emit

    def test_memory_tools_default(self):
        """Should default memory tools to False."""
        adapter = GoogleADKAdapter()
        assert Capability.MEMORY not in adapter.features.capabilities

    def test_empty_custom_tools(self):
        """Should default to empty custom tools list."""
        adapter = GoogleADKAdapter()
        assert adapter._custom_tools == []

    def test_history_converter_set(self):
        """Should have history converter set by default."""
        adapter = GoogleADKAdapter()
        assert adapter.history_converter is not None


class TestADKAgentNameSanitization:
    """Tests for ADK-safe internal agent names."""

    def test_replaces_spaces_and_punctuation(self):
        """Display names with punctuation should become valid ADK identifiers."""
        assert _sanitize_adk_agent_name("Band Agent!") == "Band_Agent_"

    def test_prefixes_digit_leading_names(self):
        """ADK rejects identifiers that start with digits, even after punctuation replacement."""
        safe_name = _sanitize_adk_agent_name("24/7 Support")

        assert safe_name == "band_24_7_Support"
        assert safe_name.isidentifier()

    def test_avoids_reserved_user_name(self):
        """ADK reserves the exact agent name 'user' for end-user input."""
        assert _sanitize_adk_agent_name("user") == "band_user"

    def test_uses_default_for_empty_name(self):
        """Empty names should still produce the default ADK-safe identifier."""
        assert _sanitize_adk_agent_name("") == "band_agent"


class TestOnStarted:
    """Tests for on_started() method."""

    @pytest.mark.asyncio
    async def test_renders_system_prompt(self):
        """Should render system prompt from agent metadata."""
        adapter = GoogleADKAdapter()
        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")

        assert adapter._system_prompt != ""
        assert "TestBot" in adapter._system_prompt

    @pytest.mark.asyncio
    async def test_uses_custom_system_prompt_when_provided(self):
        """Should use custom system_prompt instead of rendered one."""
        adapter = GoogleADKAdapter(
            GoogleADKAdapterConfig(system_prompt="Custom prompt here.")
        )
        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")

        assert adapter._system_prompt == "Custom prompt here."

    @pytest.mark.asyncio
    async def test_sets_agent_name(self):
        """Should set agent_name on the adapter."""
        adapter = GoogleADKAdapter()
        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")

        assert adapter.agent_name == "TestBot"
        assert adapter.agent_description == "A test bot"


class TestOnMessage:
    """Tests for on_message() method."""

    @pytest.mark.asyncio
    async def test_creates_session_on_bootstrap(self, sample_message, mock_tools):
        """Should create a new session on first message."""
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()
            mock_runner.run_async = _empty_async_iter
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            assert "room-123" in adapter._room_sessions

    @pytest.mark.asyncio
    async def test_creates_new_session_on_each_message(
        self, sample_message, mock_tools
    ):
        """Should create a fresh session per message (runner is ephemeral)."""
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()
            mock_runner.run_async = _empty_async_iter
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            # First message (bootstrap)
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            session_id_1 = adapter._room_sessions["room-123"]

            # Second message (not bootstrap)
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-123",
            )

            session_id_2 = adapter._room_sessions["room-123"]
            assert session_id_1 != session_id_2

    @pytest.mark.asyncio
    async def test_injects_participants_message(self, sample_message, mock_tools):
        """Should include participants update in message."""
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")

        captured_messages = []

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()

            def capture_run(**kwargs):
                captured_messages.append(kwargs.get("new_message"))
                return _empty_async_iter()

            mock_runner.run_async = capture_run
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg="Alice joined the room",
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

        assert len(captured_messages) == 1
        content = captured_messages[0]
        text = content.parts[0].text
        assert "[System]: Alice joined the room" in text

    @pytest.mark.asyncio
    async def test_injects_history_on_bootstrap(self, sample_message, mock_tools):
        """Should inject history transcript on bootstrap."""
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")

        history = [
            {"role": "user", "content": "[Bob]: Previous message"},
            {"role": "model", "content": "Previous response"},
        ]

        captured_messages = []

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()

            def capture_run(**kwargs):
                captured_messages.append(kwargs.get("new_message"))
                return _empty_async_iter()

            mock_runner.run_async = capture_run
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=history,
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

        assert len(captured_messages) == 1
        text = captured_messages[0].parts[0].text
        assert "Previous conversation context" in text
        assert "[Bob]: Previous message" in text


class TestOnCleanup:
    """Tests for on_cleanup() method."""

    @pytest.mark.asyncio
    async def test_cleans_up_room_session(self):
        """Should remove room session on cleanup."""
        adapter = GoogleADKAdapter()
        adapter._room_sessions["room-123"] = "session-abc"

        await adapter.on_cleanup("room-123")

        assert "room-123" not in adapter._room_sessions

    @pytest.mark.asyncio
    async def test_cleanup_nonexistent_room(self):
        """Should not raise when cleaning up nonexistent room."""
        adapter = GoogleADKAdapter()
        await adapter.on_cleanup("nonexistent-room")

    @pytest.mark.asyncio
    async def test_cleanup_called_twice(self):
        """Should not raise when cleanup is called twice for the same room."""
        adapter = GoogleADKAdapter()
        adapter._room_history["room-123"] = [{"role": "user", "content": "hi"}]
        adapter._room_sessions["room-123"] = "session-abc"

        await adapter.on_cleanup("room-123")
        await adapter.on_cleanup("room-123")

        assert "room-123" not in adapter._room_history
        assert "room-123" not in adapter._room_sessions

    @pytest.mark.asyncio
    async def test_cleanup_one_room_while_another_active(self):
        """Should only clean up the specified room, leaving others untouched."""
        adapter = GoogleADKAdapter()
        adapter._room_history["room-A"] = [{"role": "user", "content": "A"}]
        adapter._room_sessions["room-A"] = "session-A"
        adapter._room_history["room-B"] = [{"role": "user", "content": "B"}]
        adapter._room_sessions["room-B"] = "session-B"

        await adapter.on_cleanup("room-A")

        assert "room-A" not in adapter._room_history
        assert "room-A" not in adapter._room_sessions
        assert adapter._room_history["room-B"] == [{"role": "user", "content": "B"}]
        assert adapter._room_sessions["room-B"] == "session-B"


class TestToolBridge:
    """Tests for _BandToolBridge."""

    def test_tool_name(self):
        """Should return the tool name."""
        bridge = _BandToolBridge(
            tool_name="band_send_message",
            tool_description="Send a message",
            parameters_schema={},
            tools=MagicMock(),
            custom_tools=[],
        )
        assert bridge.name == "band_send_message"

    def test_tool_description(self):
        """Should return the tool description."""
        bridge = _BandToolBridge(
            tool_name="test_tool",
            tool_description="A test tool",
            parameters_schema={},
            tools=MagicMock(),
            custom_tools=[],
        )
        assert bridge.description == "A test tool"

    def test_get_declaration(self):
        """Should build a FunctionDeclaration with stripped additionalProperties."""
        bridge = _BandToolBridge(
            tool_name="my_tool",
            tool_description="Does things",
            parameters_schema={
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "additionalProperties": False,
            },
            tools=MagicMock(),
            custom_tools=[],
        )

        decl = bridge._get_declaration()

        assert decl.name == "my_tool"
        assert decl.description == "Does things"
        # additionalProperties should be stripped for Gemini compatibility
        assert "additionalProperties" not in (decl.parameters or {})

    def test_declaration_eagerly_cached(self):
        """Declaration should be built eagerly in __init__ and cached."""
        bridge = _BandToolBridge(
            tool_name="cached_tool",
            tool_description="Cached",
            parameters_schema={"type": "object", "properties": {}},
            tools=MagicMock(),
            custom_tools=[],
        )

        # _cached_declaration should exist from __init__
        assert hasattr(bridge, "_cached_declaration")
        assert bridge._cached_declaration.name == "cached_tool"

        # _build_declaration should return the same cached object
        assert bridge._build_declaration() is bridge._cached_declaration

        # _get_declaration should delegate to _build_declaration
        assert bridge._get_declaration() is bridge._cached_declaration

    @pytest.mark.asyncio
    async def test_executes_platform_tool(self):
        """Should delegate to AgentToolsProtocol."""
        mock_tools = MagicMock()
        mock_tools.execute_tool_call = AsyncMock(return_value={"status": "sent"})

        bridge = _BandToolBridge(
            tool_name="band_send_message",
            tool_description="Send a message",
            parameters_schema={},
            tools=mock_tools,
            custom_tools=[],
        )

        result = await bridge.run_async(
            args={"content": "Hello", "mentions": ["@alice"]},
            tool_context=MagicMock(),
        )

        mock_tools.execute_tool_call.assert_called_once_with(
            "band_send_message", {"content": "Hello", "mentions": ["@alice"]}
        )
        assert result == '{"status": "sent"}'

    @pytest.mark.asyncio
    async def test_handles_tool_error(self):
        """Should return error string on tool failure."""
        mock_tools = MagicMock()
        mock_tools.execute_tool_call = AsyncMock(side_effect=Exception("Tool failed!"))

        bridge = _BandToolBridge(
            tool_name="failing_tool",
            tool_description="Fails",
            parameters_schema={},
            tools=mock_tools,
            custom_tools=[],
        )

        result = await bridge.run_async(args={}, tool_context=MagicMock())
        assert "Error" in result
        assert "Tool failed!" in result

    def test_builds_declaration_for_every_platform_tool(self):
        """Every platform tool's schema must build a ref-free Gemini declaration.

        Enum-typed params (e.g. band_list_memories'/band_store_memory's scope,
        system, type) are emitted by Pydantic as JSON-Schema ``$ref``/``$defs``.
        Gemini's ``parameters`` field is the restricted OpenAPI Schema subset and
        rejects those references, so the bridge must dereference them. This builds
        a bridge for *every* real platform tool (memory + contacts included) and
        asserts the cached declaration carries no leftover ``$ref``/``$defs`` — the
        gap that only surfaced at E2E when memory tools were enabled.
        """
        # Real platform schemas, link-free (schema building reads TOOL_DEFINITIONS,
        # not the REST client), with every optional tool category included.
        schemas = AgentTools("room-1", MagicMock()).get_openai_tool_schemas(
            capabilities=ALL_CAPABILITIES
        )
        names = {s["function"]["name"] for s in schemas}

        # Guard: the test is only meaningful if the ref-bearing tools are present,
        # so it can't silently pass if schema generation stops emitting them.
        assert {"band_list_memories", "band_store_memory"} <= names, (
            f"expected ref-bearing memory tools in schema set, got {sorted(names)}"
        )

        for schema in schemas:
            func = schema["function"]
            bridge = _BandToolBridge(
                tool_name=func["name"],
                tool_description=func.get("description", ""),
                parameters_schema=func.get("parameters", {}),
                tools=MagicMock(),
                custom_tools=[],
            )
            blob = bridge._get_declaration().model_dump_json()
            assert "$ref" not in blob and "$defs" not in blob, (
                f"{func['name']}: declaration leaked $ref/$defs (Gemini rejects them)"
            )


class TestDeclarationCandidateDetection:
    """Tests for _DECLARATION_CANDIDATES probing and smoke-test."""

    def test_no_candidates_raises_runtime_error(self):
        """Should raise RuntimeError when BaseTool has no known declaration method."""
        _get_tool_bridge_class.cache_clear()
        try:
            with patch.object(
                _google_adk_mod,
                "_DECLARATION_CANDIDATES",
                ("_nonexistent_method",),
            ):
                _get_tool_bridge_class.cache_clear()
                with pytest.raises(RuntimeError, match="no known declaration method"):
                    _get_tool_bridge_class()
        finally:
            _get_tool_bridge_class.cache_clear()
            # Re-populate cache with valid class
            _get_tool_bridge_class()

    def test_smoke_test_runs_at_class_creation(self):
        """Smoke-test should verify declaration mechanism end-to-end."""
        # The fact that _get_tool_bridge_class() returned successfully at
        # module level (line 26) proves the smoke-test passed.  Verify the
        # returned class has the expected declaration method.
        bridge = _BandToolBridge(
            tool_name="smoke",
            tool_description="smoke test",
            parameters_schema={},
            tools=MagicMock(),
            custom_tools=[],
        )
        decl = bridge._get_declaration()
        assert decl is not None
        assert decl.name == "smoke"


class TestBuildADKTools:
    """Tests for _build_adk_tools."""

    def test_builds_platform_tools(self, mock_tools):
        """Should create tool bridges from platform tool schemas."""
        adapter = GoogleADKAdapter()
        bridges = adapter._build_adk_tools(mock_tools)

        assert len(bridges) == 1
        assert bridges[0].name == "band_send_message"

    def test_includes_custom_tools(self, mock_tools):
        """Should include custom tools in the bridge list."""

        class EchoInput(BaseModel):
            """Echo back the message."""

            message: str = Field(description="Message to echo")

        async def echo(args: EchoInput) -> str:
            return f"Echo: {args.message}"

        adapter = GoogleADKAdapter(additional_tools=[(EchoInput, echo)])
        bridges = adapter._build_adk_tools(mock_tools)

        assert len(bridges) == 2
        tool_names = [b.name for b in bridges]
        assert "band_send_message" in tool_names
        assert "echo" in tool_names


class TestCustomTools:
    """Tests for custom tool support."""

    def test_accepts_additional_tools_parameter(self):
        """Adapter should accept list of (Model, func) tuples."""

        class EchoInput(BaseModel):
            """Echo back the message."""

            message: str = Field(description="Message to echo")

        async def echo(args: EchoInput) -> str:
            return f"Echo: {args.message}"

        adapter = GoogleADKAdapter(additional_tools=[(EchoInput, echo)])
        assert len(adapter._custom_tools) == 1

    @pytest.mark.asyncio
    async def test_custom_tool_execution_via_bridge(self):
        """Custom tool should be executed via bridge."""

        class EchoInput(BaseModel):
            """Echo back the message."""

            message: str = Field(description="Message to echo")

        async def echo(args: EchoInput) -> str:
            return f"Echo: {args.message}"

        mock_tools = MagicMock()
        mock_tools.execute_tool_call = AsyncMock()

        bridge = _BandToolBridge(
            tool_name="echo",
            tool_description="Echo back the message",
            parameters_schema={},
            tools=mock_tools,
            custom_tools=[(EchoInput, echo)],
        )

        result = await bridge.run_async(
            args={"message": "Hello"},
            tool_context=MagicMock(),
        )

        # Should NOT have called platform tool
        mock_tools.execute_tool_call.assert_not_called()
        assert "Echo: Hello" in result

    @pytest.mark.asyncio
    async def test_sync_custom_tool_execution(self):
        """Sync custom tool should be executed via bridge."""

        class CalcInput(BaseModel):
            """Calculate sum."""

            a: int = Field(description="First number")
            b: int = Field(description="Second number")

        def calculate(args: CalcInput) -> str:
            return f"Result: {args.a + args.b}"

        mock_tools = MagicMock()
        mock_tools.execute_tool_call = AsyncMock()

        # Tool name derived from model: CalcInput -> "calc"
        bridge = _BandToolBridge(
            tool_name="calc",
            tool_description="Calculate sum",
            parameters_schema={},
            tools=mock_tools,
            custom_tools=[(CalcInput, calculate)],
        )

        result = await bridge.run_async(
            args={"a": 3, "b": 4},
            tool_context=MagicMock(),
        )

        mock_tools.execute_tool_call.assert_not_called()
        assert "Result: 7" in result

    @pytest.mark.asyncio
    async def test_custom_tool_validation_error(self):
        """Should return formatted validation error for invalid arguments."""

        class StrictInput(BaseModel):
            """Requires a name field."""

            name: str = Field(description="Required name")

        async def strict_tool(args: StrictInput) -> str:
            return f"Hello, {args.name}"

        mock_tools = MagicMock()
        mock_tools.execute_tool_call = AsyncMock()

        bridge = _BandToolBridge(
            tool_name="strict",
            tool_description="Requires name",
            parameters_schema={},
            tools=mock_tools,
            custom_tools=[(StrictInput, strict_tool)],
        )

        # Pass empty args - missing required 'name' field
        result = await bridge.run_async(
            args={},
            tool_context=MagicMock(),
        )

        assert "Invalid arguments" in result
        assert "name" in result

    @pytest.mark.asyncio
    async def test_multiple_custom_tools_execution(self):
        """Multiple custom tools should be independently executable."""

        class EchoInput(BaseModel):
            """Echo back the message."""

            message: str = Field(description="Message to echo")

        class CalcInput(BaseModel):
            """Calculate sum."""

            a: int = Field(description="First number")
            b: int = Field(description="Second number")

        async def echo(args: EchoInput) -> str:
            return f"Echo: {args.message}"

        def calculate(args: CalcInput) -> str:
            return f"Sum: {args.a + args.b}"

        custom_tools = [(EchoInput, echo), (CalcInput, calculate)]
        mock_tools = MagicMock()
        mock_tools.execute_tool_call = AsyncMock()

        echo_bridge = _BandToolBridge(
            tool_name="echo",
            tool_description="Echo",
            parameters_schema={},
            tools=mock_tools,
            custom_tools=custom_tools,
        )
        # Tool name derived from model: CalcInput -> "calc"
        calc_bridge = _BandToolBridge(
            tool_name="calc",
            tool_description="Calc",
            parameters_schema={},
            tools=mock_tools,
            custom_tools=custom_tools,
        )

        echo_result = await echo_bridge.run_async(
            args={"message": "Hi"}, tool_context=MagicMock()
        )
        calc_result = await calc_bridge.run_async(
            args={"a": 2, "b": 3}, tool_context=MagicMock()
        )

        assert "Echo: Hi" in echo_result
        assert "Sum: 5" in calc_result
        mock_tools.execute_tool_call.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("declare", "complete"), CUSTOM_TOOL_DECLARATIONS)
    async def test_custom_tool_records_its_effect_on_the_room_turn(
        self, declare: Callable[..., Any], complete: bool
    ):
        class FileInput(BaseModel):
            """File the report."""

            note: str

        @declare
        async def file_report(args: FileInput) -> str:
            return "filed"

        tools = FakeAgentTools(room_id="room-123")
        adapter = GoogleADKAdapter(additional_tools=[(FileInput, file_report)])
        (bridge,) = [t for t in adapter._build_adk_tools(tools) if t.name == "file"]

        await bridge.run_async(args={"note": "go"}, tool_context=MagicMock())

        assert tools.turn.complete is complete


class TestContactsInjection:
    """Tests for contacts_msg injection."""

    @pytest.mark.asyncio
    async def test_injects_contacts_message(self, sample_message, mock_tools):
        """Should include contacts broadcast in message."""
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")

        captured_messages = []

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()

            def capture_run(**kwargs):
                captured_messages.append(kwargs.get("new_message"))
                return _empty_async_iter()

            mock_runner.run_async = capture_run
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg="Bob is now a contact",
                is_session_bootstrap=True,
                room_id="room-123",
            )

        assert len(captured_messages) == 1
        text = captured_messages[0].parts[0].text
        assert "[System]: Bob is now a contact" in text


class TestExecutionReporting:
    """Tests for _report_event execution reporting."""

    @pytest.mark.asyncio
    async def test_reports_function_calls(self, mock_tools):
        """Should report function calls as tool_call events."""
        adapter = GoogleADKAdapter(emit=Emit.TOOL_CALLS)

        mock_fc = MagicMock()
        mock_fc.name = "band_send_message"
        mock_fc.args = {"content": "Hello"}
        mock_fc.id = "fc-1"

        event = MagicMock()
        event.get_function_calls.return_value = [mock_fc]
        event.get_function_responses.return_value = []

        await adapter._report_event(event, mock_tools)

        mock_tools.send_event.assert_called_once()
        call_args = mock_tools.send_event.call_args
        assert call_args.kwargs["message_type"] == "tool_call"
        assert "band_send_message" in call_args.kwargs["content"]

    @pytest.mark.asyncio
    async def test_reports_function_responses(self, mock_tools):
        """Should report function responses as tool_result events."""
        adapter = GoogleADKAdapter(emit=Emit.TOOL_CALLS)

        mock_fr = MagicMock()
        mock_fr.name = "band_send_message"
        mock_fr.response = {"status": "sent"}
        mock_fr.id = "fc-1"

        event = MagicMock()
        event.get_function_calls.return_value = []
        event.get_function_responses.return_value = [mock_fr]

        await adapter._report_event(event, mock_tools)

        mock_tools.send_event.assert_called_once()
        call_args = mock_tools.send_event.call_args
        assert call_args.kwargs["message_type"] == "tool_result"
        assert "band_send_message" in call_args.kwargs["content"]

    @pytest.mark.asyncio
    async def test_reports_read_room_file_image_result_as_placeholder(self, mock_tools):
        """band_read_room_file's image result must not leak its base64 data
        into a tool_result event. run_async json.dumps a non-str tool
        result before returning it, so ADK's own __build_response_event
        wraps that json string as {"result": <that string>} (its spec
        requires a dict) -- str()ing that wrapper would otherwise embed the
        full base64 payload."""
        adapter = GoogleADKAdapter(emit=Emit.TOOL_CALLS)

        image_result = {
            "content": [
                {
                    "type": "image",
                    "data": "not-really-base64-but-huge-in-real-life",
                    "mimeType": "image/png",
                }
            ]
        }
        mock_fr = MagicMock()
        mock_fr.name = BandTool.READ_ROOM_FILE
        mock_fr.response = {"result": json.dumps(image_result)}
        mock_fr.id = "fc-1"

        event = MagicMock()
        event.get_function_calls.return_value = []
        event.get_function_responses.return_value = [mock_fr]

        await adapter._report_event(event, mock_tools)

        content = mock_tools.send_event.call_args.kwargs["content"]
        assert "not-really-base64-but-huge-in-real-life" not in content
        assert "<1 image content block(s)>" in content

    @pytest.mark.asyncio
    async def test_reports_send_room_file_args_content_as_placeholder(self, mock_tools):
        """band_send_room_file's raw file content must not leak into a
        tool_call event's reported ARGS."""
        adapter = GoogleADKAdapter(emit=Emit.TOOL_CALLS)

        mock_fc = MagicMock()
        mock_fc.name = BandTool.SEND_ROOM_FILE
        mock_fc.args = {"content": "the entire raw file body", "filename": "f.txt"}
        mock_fc.id = "fc-1"

        event = MagicMock()
        event.get_function_calls.return_value = [mock_fc]
        event.get_function_responses.return_value = []

        await adapter._report_event(event, mock_tools)

        content = mock_tools.send_event.call_args.kwargs["content"]
        assert "the entire raw file body" not in content
        assert "byte file content" in content

    @pytest.mark.asyncio
    async def test_skips_event_without_function_methods(self, mock_tools):
        """Should skip events that lack function call/response methods."""
        adapter = GoogleADKAdapter(emit=Emit.TOOL_CALLS)

        event = MagicMock(spec=[])  # No attributes

        await adapter._report_event(event, mock_tools)

        mock_tools.send_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_report_failure_gracefully(self, mock_tools):
        """Should not raise when event reporting fails."""
        adapter = GoogleADKAdapter(emit=Emit.TOOL_CALLS)
        mock_tools.send_event = AsyncMock(side_effect=Exception("Network error"))

        mock_fc = MagicMock()
        mock_fc.name = "test_tool"
        mock_fc.args = {}
        mock_fc.id = "fc-1"

        event = MagicMock()
        event.get_function_calls.return_value = [mock_fc]
        event.get_function_responses.return_value = []

        # Should not raise
        await adapter._report_event(event, mock_tools)


class TestErrorHandling:
    """Tests for error handling."""

    @pytest.mark.asyncio
    async def test_reports_error_on_runner_failure(self, sample_message, mock_tools):
        """Should report error when ADK runner fails."""
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()

            async def failing_run(**kwargs):
                raise RuntimeError("Runner Error")
                yield

            mock_runner.run_async = failing_run
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            with pytest.raises(Exception, match="Runner Error"):
                await adapter.on_message(
                    msg=sample_message,
                    tools=mock_tools,
                    history=[],
                    participants_msg=None,
                    contacts_msg=None,
                    is_session_bootstrap=True,
                    room_id="room-123",
                )

            mock_tools.send_failure.assert_called_once()
            failure = mock_tools.send_failure.call_args.args[0]
            assert failure.provider == "google_adk"
            assert failure.message == GENERIC_PROVIDER_FAILURE_MESSAGE
            # Runner should be closed even on error (via finally)
            mock_runner.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_reports_error_when_runner_construction_itself_fails(
        self, sample_message, mock_tools
    ):
        """A runner-construction failure must be reported, not escape uncaught."""
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")

        with (
            patch.object(
                adapter, "_create_runner", side_effect=RuntimeError("bad tool schema")
            ),
            pytest.raises(RuntimeError, match="bad tool schema"),
        ):
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

        mock_tools.send_failure.assert_called_once()
        failure = mock_tools.send_failure.call_args.args[0]
        assert failure.provider == "google_adk"
        assert failure.message == GENERIC_PROVIDER_FAILURE_MESSAGE


class TestModelFailuresReturnedAsData(ModelFailureCases):
    PROVIDER = "google_adk"

    @pytest.mark.asyncio
    async def test_no_candidates_fails_with_adks_own_code(self, scripted_adapter):
        await self.assert_turn_fails_with(
            scripted_adapter, [no_candidates()], "UNKNOWN_ERROR"
        )


class TestHistoryTranscript:
    """Tests for _format_history_transcript."""

    def test_formats_text_messages(self):
        """Should format text messages."""
        adapter = GoogleADKAdapter()
        history = [
            {"role": "user", "content": "[Alice]: Hello"},
            {"role": "user", "content": "[Bob]: Hi there"},
        ]

        transcript = adapter._format_history_transcript(history)

        assert "[Alice]: Hello" in transcript
        assert "[Bob]: Hi there" in transcript

    def test_formats_tool_calls(self):
        """Should format tool call blocks."""
        adapter = GoogleADKAdapter()
        history = [
            {
                "role": "model",
                "content": [
                    {
                        "type": "function_call",
                        "name": "band_send_message",
                        "args": {"content": "Hello"},
                    }
                ],
            }
        ]

        transcript = adapter._format_history_transcript(history)

        assert "[Tool Call]" in transcript
        assert "band_send_message" in transcript

    def test_formats_tool_results(self):
        """Should format tool result blocks."""
        adapter = GoogleADKAdapter()
        history = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "function_response",
                        "name": "band_send_message",
                        "output": '{"status": "sent"}',
                    }
                ],
            }
        ]

        transcript = adapter._format_history_transcript(history)

        assert "[Tool Result]" in transcript
        assert "band_send_message" in transcript

    def test_empty_history(self):
        """Should return empty string for empty history."""
        adapter = GoogleADKAdapter()
        assert adapter._format_history_transcript([]) == ""

    @pytest.mark.asyncio
    async def test_labels_own_agent_replies_with_agent_name(self):
        """Own-agent text (role='model', bare content) should be labeled with the agent's name.

        INT-509 regression: without a speaker label, rehydrated transcripts
        showed the agent's prior replies as anonymous lines and the LLM
        could not tell them apart from peer turns, leading it to re-answer
        questions it had already handled after a restart.

        ``SimpleAdapter.on_started`` propagates the agent name into the
        converter, which is the single source of truth for own-vs-peer
        attribution.  This test exercises that wiring end-to-end so a
        future refactor that drops the propagation step would fail here.
        """
        adapter = GoogleADKAdapter()
        await adapter.on_started("ResearchBot", "Test bot")
        history = [
            {"role": "user", "content": "[Alice]: What's the capital of France?"},
            {"role": "model", "content": "Paris."},
            {"role": "user", "content": "[Alice]: Remember pineapple"},
            {"role": "model", "content": "I'll remember 'pineapple'."},
        ]

        transcript = adapter._format_history_transcript(history)

        assert "[Alice]: What's the capital of France?" in transcript
        assert "[ResearchBot]: Paris." in transcript
        assert "[ResearchBot]: I'll remember 'pineapple'." in transcript


class TestHistoryAccumulation:
    """Tests for per-room history accumulation across messages."""

    @pytest.mark.asyncio
    async def test_non_bootstrap_uses_accumulated_history(
        self, sample_message, mock_tools
    ):
        """Non-bootstrap messages should still inject accumulated history."""
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")

        captured_messages = []

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()

            def capture_run(**kwargs):
                captured_messages.append(kwargs.get("new_message"))
                return _empty_async_iter()

            mock_runner.run_async = capture_run
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            # First message (bootstrap) seeds history
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[
                    {"role": "user", "content": "[Bob]: Previous message"},
                ],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            # Second message (non-bootstrap) should still have history
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],  # History param ignored on non-bootstrap
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-123",
            )

        # Second message should contain accumulated history
        assert len(captured_messages) == 2
        text = captured_messages[1].parts[0].text
        assert "Previous conversation context" in text
        # Should also have the first user message accumulated
        assert "Hello, agent!" in text

    @pytest.mark.asyncio
    async def test_non_bootstrap_no_history_injection_when_empty(
        self, sample_message, mock_tools
    ):
        """Non-bootstrap with no prior history should not inject transcript."""
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")

        captured_messages = []

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()

            def capture_run(**kwargs):
                captured_messages.append(kwargs.get("new_message"))
                return _empty_async_iter()

            mock_runner.run_async = capture_run
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            # Bootstrap with empty history
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            # Non-bootstrap - accumulated history only has the first msg
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-123",
            )

        # Second message should have context from first message
        text = captured_messages[1].parts[0].text
        assert "Previous conversation context" in text

    @pytest.mark.asyncio
    async def test_cleanup_removes_room_history(self):
        """on_cleanup should remove accumulated history."""
        adapter = GoogleADKAdapter()
        adapter._room_history["room-123"] = [
            {"role": "user", "content": "test"},
        ]
        adapter._room_sessions["room-123"] = "session-abc"

        await adapter.on_cleanup("room-123")

        assert "room-123" not in adapter._room_history
        assert "room-123" not in adapter._room_sessions

    @pytest.mark.asyncio
    async def test_sliding_window_limits_transcript(self, sample_message, mock_tools):
        """Should only inject the last max_history_messages in the transcript."""
        adapter = GoogleADKAdapter(GoogleADKAdapterConfig(max_history_messages=3))
        await adapter.on_started("TestBot", "Test bot")

        # Seed room history with more messages than the window
        adapter._room_history["room-123"] = [
            {"role": "user", "content": f"[User]: Message {i}"} for i in range(10)
        ]

        captured_messages = []

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()

            def capture_run(**kwargs):
                captured_messages.append(kwargs.get("new_message"))
                return _empty_async_iter()

            mock_runner.run_async = capture_run
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-123",
            )

        text = captured_messages[0].parts[0].text
        # Only the last 3 messages should be in the transcript
        assert "Message 7" in text
        assert "Message 8" in text
        assert "Message 9" in text
        # Older messages should be excluded
        assert "Message 0" not in text
        assert "Message 6" not in text

    @pytest.mark.asyncio
    async def test_history_trimmed_to_prevent_unbounded_growth(
        self, sample_message, mock_tools
    ):
        """Should trim accumulated history when it exceeds 2x max_history_messages."""
        adapter = GoogleADKAdapter(GoogleADKAdapterConfig(max_history_messages=3))
        await adapter.on_started("TestBot", "Test bot")

        # Seed room history just below the trim threshold (2 * 3 = 6)
        adapter._room_history["room-123"] = [
            {"role": "user", "content": f"[User]: Message {i}"} for i in range(6)
        ]

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()
            mock_runner.run_async = _empty_async_iter
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-123",
            )

        # 6 seeded + 1 new user message = 7 > threshold (6), triggers trim to 3
        history = adapter._room_history["room-123"]
        assert len(history) == 3
        # Should keep the most recent messages
        assert history[-1]["role"] == "user"
        assert history[-1]["content"] == "[Alice]: Hello, agent!"


class TestTranscriptTruncation:
    """Tests for character-based transcript truncation."""

    @pytest.mark.asyncio
    async def test_truncates_large_transcript(self, sample_message, mock_tools):
        """Should truncate transcript exceeding max_transcript_chars."""
        adapter = GoogleADKAdapter(GoogleADKAdapterConfig(max_transcript_chars=200))
        await adapter.on_started("TestBot", "Test bot")

        # Seed history with messages that produce a transcript > 200 chars
        adapter._room_history["room-123"] = [
            {"role": "user", "content": f"[User]: Message number {i} with padding"}
            for i in range(20)
        ]

        captured_messages = []

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()

            def capture_run(**kwargs):
                captured_messages.append(kwargs.get("new_message"))
                return _empty_async_iter()

            mock_runner.run_async = capture_run
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-123",
            )

        text = captured_messages[0].parts[0].text
        # The transcript portion (between the markers) should be <= 200 chars
        start = text.find("[Previous conversation context]\n")
        end = text.find("\n[End of previous context]")
        if start != -1 and end != -1:
            transcript_section = text[
                start + len("[Previous conversation context]\n") : end
            ]
            assert len(transcript_section) <= 200
        # Older messages should be dropped, recent ones kept
        assert "Message number 19" in text
        assert "Message number 0" not in text

    @pytest.mark.asyncio
    async def test_no_truncation_when_within_limit(self, sample_message, mock_tools):
        """Should not truncate transcript when within max_transcript_chars."""
        adapter = GoogleADKAdapter(GoogleADKAdapterConfig(max_transcript_chars=100_000))
        await adapter.on_started("TestBot", "Test bot")

        adapter._room_history["room-123"] = [
            {"role": "user", "content": "[User]: Short msg"},
        ]

        captured_messages = []

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()

            def capture_run(**kwargs):
                captured_messages.append(kwargs.get("new_message"))
                return _empty_async_iter()

            mock_runner.run_async = capture_run
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-123",
            )

        text = captured_messages[0].parts[0].text
        assert "[User]: Short msg" in text


class TestFinalResponseCapture:
    """Tests for capturing final text response from ADK events."""

    @pytest.mark.asyncio
    async def test_captures_final_response_in_history(self, sample_message, mock_tools):
        """Should capture final response text and add to room history."""
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")

        mock_event = MagicMock()
        mock_event.is_final_response.return_value = True
        mock_event.error_code = None
        mock_part = MagicMock()
        mock_part.text = "Here is my response"
        mock_event.content.parts = [mock_part]

        async def iter_with_response(**kwargs):
            yield mock_event

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()
            mock_runner.run_async = iter_with_response
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

        # History should contain both user message and model response
        history = adapter._room_history["room-123"]
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "model"
        assert history[1]["content"] == "Here is my response"

    def test_extract_event_text_with_content(self):
        """Should extract text from event content parts."""
        mock_event = MagicMock()
        mock_part = MagicMock()
        mock_part.text = "response text"
        mock_event.content.parts = [mock_part]

        result = GoogleADKAdapter._extract_event_text(mock_event)
        assert result == "response text"

    def test_extract_event_text_no_content(self):
        """Should return empty string when event has no content."""
        mock_event = MagicMock()
        mock_event.content = None

        result = GoogleADKAdapter._extract_event_text(mock_event)
        assert result == ""

    def test_extract_event_text_no_text_parts(self):
        """Should return empty string when parts have no text."""
        mock_event = MagicMock()
        mock_part = MagicMock()
        mock_part.text = None
        mock_event.content.parts = [mock_part]

        result = GoogleADKAdapter._extract_event_text(mock_event)
        assert result == ""


class TestConcurrentMessages:
    """Tests for concurrent on_message calls."""

    @pytest.mark.asyncio
    async def test_concurrent_messages_different_rooms(self, mock_tools):
        """Concurrent on_message calls on different rooms should not interfere."""
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")

        msg_a = PlatformMessage(
            id="msg-a",
            room_id="room-a",
            content="Hello from room A",
            sender_id="user-1",
            sender_type="User",
            sender_name="Alice",
            message_type="text",
            metadata={},
            created_at=datetime.now(UTC),
        )
        msg_b = PlatformMessage(
            id="msg-b",
            room_id="room-b",
            content="Hello from room B",
            sender_id="user-2",
            sender_type="User",
            sender_name="Bob",
            message_type="text",
            metadata={},
            created_at=datetime.now(UTC),
        )

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()
            mock_runner.run_async = _empty_async_iter
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            await asyncio.gather(
                adapter.on_message(
                    msg=msg_a,
                    tools=mock_tools,
                    history=[],
                    participants_msg=None,
                    contacts_msg=None,
                    is_session_bootstrap=True,
                    room_id="room-a",
                ),
                adapter.on_message(
                    msg=msg_b,
                    tools=mock_tools,
                    history=[],
                    participants_msg=None,
                    contacts_msg=None,
                    is_session_bootstrap=True,
                    room_id="room-b",
                ),
            )

        # Each room should have its own independent history
        assert "room-a" in adapter._room_history
        assert "room-b" in adapter._room_history
        assert len(adapter._room_history["room-a"]) == 1
        assert len(adapter._room_history["room-b"]) == 1
        assert "room A" in adapter._room_history["room-a"][0]["content"]
        assert "room B" in adapter._room_history["room-b"][0]["content"]

        # Each room should have its own session
        assert adapter._room_sessions["room-a"] != adapter._room_sessions["room-b"]


# Helper for creating empty async iterators in tests
async def _empty_async_iter(**kwargs):
    return
    yield  # Make it an async generator


@pytest.mark.parametrize("google_http", [HTTPMode.MOCK, HTTPMode.TCP], indirect=True)
class TestNativeHTTPRecovery:
    @pytest.mark.parametrize(
        ("responses", "max_retries", "expected_calls", "error_code"),
        [
            pytest.param(
                [
                    503,
                    tool_call(
                        BandTool.SEND_MESSAGE,
                        {"content": "Recovered", "mentions": ["@alice"]},
                    ),
                    text_reply("Done"),
                ],
                2,
                3,
                None,
                id="recover",
            ),
            pytest.param([503, 503, 503], 2, 3, 503, id="exhaust"),
            pytest.param([503], 0, 1, 503, id="disabled"),
            pytest.param([400], 2, 1, 400, id="nonretryable"),
        ],
    )
    @pytest.mark.asyncio
    async def test_generation_budget_and_failure_reporting(
        self,
        google_http: GoogleHTTPPeer,
        responses: list[int | types.GenerateContentResponse],
        max_retries: int,
        expected_calls: int,
        error_code: int | None,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        google_http.responses = responses.copy()
        adapter = GoogleADKAdapter(
            GoogleADKAdapterConfig(max_retries=max_retries), emit={Emit.USAGE}
        )
        await adapter.on_started("TestBot", "Test bot")
        tools = RecoveryTools()
        with caplog.at_level(logging.INFO, logger="google_genai._api_client"):
            if error_code is None:
                await adapter.on_event(turn_input(tools))
                assert failure_reports(tools) == []
                assert tools.chat == ["Recovered"]
                [usage] = recorded_usage_payloads(tools)
                assert (usage["input_tokens"], usage["output_tokens"]) == (
                    SCRIPTED_USAGE.prompt_token_count * 2,
                    SCRIPTED_USAGE.candidates_token_count * 2,
                )
            else:
                with pytest.raises(APIError) as raised:
                    await adapter.on_event(turn_input(tools))
                assert raised.value.code == error_code
                assert failure_reports(tools) == [
                    ("google_adk", GENERIC_PROVIDER_FAILURE_MESSAGE)
                ]
                assert tools.chat == []
                assert recorded_usage_payloads(tools) == []
        assert len(google_http.requests) == expected_calls
        assert google_http.clients_closed
        repeated = (
            google_http.generation_requests[:2]
            if error_code is None
            else google_http.generation_requests
        )
        assert repeated == [repeated[0]] * len(repeated)
        retry_logs = [
            r.getMessage()
            for r in caplog.records
            if r.name == "google_genai._api_client"
            and r.getMessage().startswith("Retrying ")
        ]
        assert len(retry_logs) == (1 if error_code is None else expected_calls - 1)
        for entry in retry_logs:
            assert "ServerError" in entry and "503" in entry
            assert LOCAL_API_KEY not in entry
            assert "vault code" not in entry and "Completed effect" not in entry

    @pytest.mark.parametrize("exhausted", [False, True], ids=["recover", "exhaust"])
    @pytest.mark.asyncio
    async def test_post_tool_recovery_never_replays_the_effect(
        self, google_http: GoogleHTTPPeer, exhausted: bool
    ) -> None:
        google_http.responses = [
            tool_call(
                BandTool.SEND_MESSAGE,
                EFFECT_ARGS,
            ),
            503,
            *([503, 503] if exhausted else [text_reply("Done")]),
        ]
        adapter = GoogleADKAdapter(emit={Emit.USAGE})
        await adapter.on_started("TestBot", "Test bot")
        tools = RecoveryTools()
        if exhausted:
            with pytest.raises(APIError):
                await adapter.on_event(turn_input(tools))
            assert failure_reports(tools) == [
                ("google_adk", GENERIC_PROVIDER_FAILURE_MESSAGE)
            ]
        else:
            await adapter.on_event(turn_input(tools))
            assert failure_reports(tools) == []
        assert tools.chat == ["Completed effect"]
        assert google_http.clients_closed
        assert tools.tool_calls == [
            {
                "tool_name": BandTool.SEND_MESSAGE,
                "arguments": EFFECT_ARGS,
            }
        ]
        [usage] = recorded_usage_payloads(tools)
        successful_responses = 1 if exhausted else 2
        assert (usage["input_tokens"], usage["output_tokens"]) == (
            SCRIPTED_USAGE.prompt_token_count * successful_responses,
            SCRIPTED_USAGE.candidates_token_count * successful_responses,
        )
        requests = google_http.generation_requests
        assert len(requests) == (4 if exhausted else 3)
        assert requests[1:] == [requests[1]] * (len(requests) - 1)
        assert any(
            "functionResponse" in part
            for content in requests[1]["contents"]
            for part in content["parts"]
        )

    @pytest.mark.asyncio
    async def test_cancellation_during_backoff_stops_generation(
        self, google_http: GoogleHTTPPeer
    ) -> None:
        google_http.responses = [
            tool_call(
                BandTool.SEND_MESSAGE,
                EFFECT_ARGS,
            ),
            503,
        ]
        google_http.initial_delay = 60
        google_http.max_delay = 60
        adapter = GoogleADKAdapter(emit={Emit.USAGE})
        await adapter.on_started("TestBot", "Test bot")
        tools = RecoveryTools()
        task = asyncio.create_task(adapter.on_event(turn_input(tools)))
        try:
            await asyncio.wait_for(google_http.retry_started.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert len(google_http.requests) == 2
        assert google_http.clients_closed
        assert tools.tool_calls == [
            {
                "tool_name": BandTool.SEND_MESSAGE,
                "arguments": EFFECT_ARGS,
            }
        ]
        assert failure_reports(tools) == []
        assert tools.chat == ["Completed effect"]
        # Cancellation suppresses best-effort usage emission by contract.
        assert recorded_usage_payloads(tools) == []


class TestWireBudget:
    @pytest.mark.parametrize("google_http", [HTTPMode.PROXY], indirect=True)
    @pytest.mark.asyncio
    async def test_generation_preserves_environment_proxy_routing(
        self, google_http: GoogleHTTPPeer
    ) -> None:
        google_http.responses = [
            tool_call(BandTool.SEND_MESSAGE, EFFECT_ARGS),
            text_reply("Done"),
        ]
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")
        tools = RecoveryTools()
        await adapter.on_event(turn_input(tools))
        assert tools.chat == ["Completed effect"]
        assert len(google_http.requests) == 2
        assert google_http.clients_closed

    @pytest.mark.parametrize("google_http", [HTTPMode.TCP], indirect=True)
    @pytest.mark.asyncio
    async def test_provider_clients_close_when_runner_cleanup_fails(
        self, google_http: GoogleHTTPPeer
    ) -> None:
        google_http.responses = [
            tool_call(BandTool.SEND_MESSAGE, EFFECT_ARGS),
            text_reply("Done"),
        ]
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")
        _, runner_class, _, _, _ = _google_adk_mod._require_adk()
        with (
            patch.object(
                runner_class,
                "close",
                new=AsyncMock(side_effect=RuntimeError("Tool cleanup failed")),
            ),
            pytest.raises(RuntimeError, match="Tool cleanup failed"),
        ):
            await adapter.on_event(turn_input(RecoveryTools()))
        assert google_http.clients_closed

    @pytest.mark.parametrize("google_http", [HTTPMode.TCP], indirect=True)
    @pytest.mark.parametrize("post_tool", [False, True], ids=["initial", "post-tool"])
    @pytest.mark.asyncio
    async def test_zero_budget_never_resends_a_disconnected_request(
        self, google_http: GoogleHTTPPeer, post_tool: bool
    ) -> None:
        google_http.responses = [
            *([tool_call(BandTool.SEND_MESSAGE, EFFECT_ARGS)] if post_tool else []),
            None,
            *([] if post_tool else [tool_call(BandTool.SEND_MESSAGE, EFFECT_ARGS)]),
            text_reply("Done"),
        ]
        adapter = GoogleADKAdapter(
            GoogleADKAdapterConfig(max_retries=0), emit={Emit.USAGE}
        )
        await adapter.on_started("TestBot", "Test bot")
        tools = RecoveryTools()
        with pytest.raises(httpx.RemoteProtocolError):
            await adapter.on_event(turn_input(tools))
        assert len(google_http.requests) == (2 if post_tool else 1)
        assert tools.chat == (["Completed effect"] if post_tool else [])
        assert failure_reports(tools) == [
            ("google_adk", GENERIC_PROVIDER_FAILURE_MESSAGE)
        ]
        assert len(recorded_usage_payloads(tools)) == (1 if post_tool else 0)
        assert google_http.clients_closed

    def test_negative_retry_budget_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_retries"):
            GoogleADKAdapterConfig(max_retries=-1)
