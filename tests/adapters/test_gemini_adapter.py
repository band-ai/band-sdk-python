"""Tests for GeminiAdapter."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from google.genai import types
from google.genai.errors import ServerError
from pydantic import BaseModel, Field

from band.adapters.gemini import GeminiAdapter
from band.core.types import PlatformMessage


@pytest.fixture
def sample_message() -> PlatformMessage:
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
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def mock_tools() -> MagicMock:
    """Create mock AgentToolsProtocol (MagicMock base, AsyncMock methods)."""
    tools = MagicMock()
    tools.get_openai_tool_schemas = MagicMock(return_value=[])
    tools.send_message = AsyncMock(return_value={"status": "sent"})
    tools.send_event = AsyncMock(return_value={"status": "sent"})
    tools.execute_tool_call = AsyncMock(return_value={"status": "success"})
    return tools


def _response_with_text(text: str) -> MagicMock:
    response = MagicMock()
    response.function_calls = []
    response.candidates = [
        MagicMock(
            content=types.Content(role="model", parts=[types.Part.from_text(text=text)])
        )
    ]
    return response


def _response_with_function_call(
    name: str, args: dict[str, str], call_id: str
) -> MagicMock:
    response = MagicMock()
    response.function_calls = [types.FunctionCall(name=name, args=args, id=call_id)]
    response.candidates = [
        MagicMock(
            content=types.Content(
                role="model",
                parts=[types.Part.from_function_call(name=name, args=args)],
            )
        )
    ]
    return response


class TestOnStarted:
    @pytest.mark.asyncio
    async def test_renders_system_prompt(self):
        adapter = GeminiAdapter(provider_key="test-key")
        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")
        assert adapter._system_prompt != ""
        assert "TestBot" in adapter._system_prompt

    @pytest.mark.asyncio
    async def test_uses_custom_system_prompt_when_provided(self):
        adapter = GeminiAdapter(
            system_prompt="Custom prompt here.",
            provider_key="test-key",
        )
        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")
        assert adapter._system_prompt == "Custom prompt here."


class TestOnMessage:
    @pytest.mark.asyncio
    async def test_initializes_history_on_bootstrap(self, sample_message, mock_tools):
        adapter = GeminiAdapter(provider_key="test-key")
        await adapter.on_started("TestBot", "Test bot")

        with patch.object(
            adapter, "_call_gemini", AsyncMock(return_value=_response_with_text("ok"))
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
            assert "room-123" in adapter._message_history
            assert len(adapter._message_history["room-123"]) >= 2

    @pytest.mark.asyncio
    async def test_executes_tool_loop(self, sample_message, mock_tools):
        adapter = GeminiAdapter(
            enable_execution_reporting=True,
            provider_key="test-key",
        )
        await adapter.on_started("TestBot", "Test bot")

        with patch.object(
            adapter,
            "_call_gemini",
            AsyncMock(
                side_effect=[
                    _response_with_function_call(
                        "band_lookup_peers", {"page": "1"}, "call_1"
                    ),
                    _response_with_text("done"),
                ]
            ),
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

        mock_tools.execute_tool_call.assert_called_once_with(
            "band_lookup_peers", {"page": "1"}
        )
        # tool_call + tool_result reporting
        assert mock_tools.send_event.call_count == 2

    @pytest.mark.asyncio
    async def test_send_event_failure_does_not_crash_tool_execution(
        self, sample_message, mock_tools
    ):
        adapter = GeminiAdapter(
            enable_execution_reporting=True,
            provider_key="test-key",
        )
        await adapter.on_started("TestBot", "Test bot")
        mock_tools.send_event.side_effect = Exception("403 Forbidden")

        with patch.object(
            adapter,
            "_call_gemini",
            AsyncMock(
                side_effect=[
                    _response_with_function_call(
                        "band_lookup_peers", {"page": "1"}, "call_1"
                    ),
                    _response_with_text("done"),
                ]
            ),
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

        mock_tools.execute_tool_call.assert_called_once()

    def test_extract_candidate_content_preserves_function_call_id_in_fallback(self):
        adapter = GeminiAdapter(provider_key="test-key")
        response = MagicMock()
        response.candidates = [MagicMock(content=None)]
        response.function_calls = [
            types.FunctionCall(name="band_lookup_peers", args={"page": "1"}, id="c1")
        ]

        content = adapter._extract_candidate_content(response)

        assert content is not None
        assert content.role == "model"
        assert len(content.parts) == 1
        function_call = content.parts[0].function_call
        assert function_call is not None
        assert function_call.id == "c1"
        assert function_call.name == "band_lookup_peers"
        assert function_call.args == {"page": "1"}


class TestRetries:
    @pytest.mark.asyncio
    async def test_retries_transient_server_errors(self):
        adapter = GeminiAdapter(
            max_retries=1,
            retry_base_delay_s=0,
            provider_key="test-key",
        )
        adapter._system_prompt = "system"
        adapter.client = MagicMock()
        adapter.client.aio.models.generate_content = AsyncMock(
            side_effect=[
                ServerError(500, {"error": "temporary"}, None),
                _response_with_text("ok"),
            ]
        )

        with patch.object(
            adapter.client.aio.models,  # type: ignore[union-attr]
            "generate_content",
            adapter.client.aio.models.generate_content,
        ) as mocked:
            response = await adapter._call_gemini(
                contents=[
                    types.Content(role="user", parts=[types.Part.from_text(text="x")])
                ],
                tools=[],
            )

        assert mocked.call_count == 2
        assert response.candidates[0].content.parts[0].text == "ok"


class TestCustomTools:
    @pytest.mark.asyncio
    async def test_executes_custom_tool(self, mock_tools):
        class EchoInput(BaseModel):
            text: str = Field(...)

        async def echo_tool(inp: EchoInput) -> str:
            return inp.text

        adapter = GeminiAdapter(
            additional_tools=[(EchoInput, echo_tool)],
            provider_key="test-key",
        )
        function_calls = [
            types.FunctionCall(name="echo", args={"text": "hello"}, id="c1")
        ]

        parts = await adapter._process_function_calls(function_calls, mock_tools)

        assert len(parts) == 1
        function_response = parts[0].function_response
        assert function_response is not None
        assert function_response.response == {"output": "hello"}
        mock_tools.execute_tool_call.assert_not_called()


class TestOnCleanup:
    @pytest.mark.asyncio
    async def test_removes_room_history(self):
        adapter = GeminiAdapter(provider_key="test-key")
        adapter._message_history["room-1"] = [
            types.Content(role="user", parts=[types.Part.from_text(text="hi")])
        ]
        await adapter.on_cleanup("room-1")
        assert "room-1" not in adapter._message_history

    @pytest.mark.asyncio
    async def test_cleanup_twice_is_idempotent(self):
        adapter = GeminiAdapter(provider_key="test-key")
        adapter._message_history["room-1"] = []
        await adapter.on_cleanup("room-1")
        await adapter.on_cleanup("room-1")  # Should not raise
        assert "room-1" not in adapter._message_history

    @pytest.mark.asyncio
    async def test_cleanup_unknown_room_is_noop(self):
        adapter = GeminiAdapter(provider_key="test-key")
        await adapter.on_cleanup("nonexistent-room")  # Should not raise
        assert "nonexistent-room" not in adapter._message_history

    def test_trim_history_caps_at_max(self):
        adapter = GeminiAdapter(provider_key="test-key", max_history_messages=5)
        adapter._message_history["room-1"] = [
            types.Content(role="user", parts=[types.Part.from_text(text=f"msg-{i}")])
            for i in range(10)
        ]
        adapter._trim_history("room-1")
        assert len(adapter._message_history["room-1"]) == 5
        # Keeps the most recent messages
        assert adapter._message_history["room-1"][0].parts[0].text == "msg-5"

    def test_trim_history_noop_when_under_limit(self):
        adapter = GeminiAdapter(provider_key="test-key", max_history_messages=50)
        adapter._message_history["room-1"] = [
            types.Content(role="user", parts=[types.Part.from_text(text="hi")])
        ]
        adapter._trim_history("room-1")
        assert len(adapter._message_history["room-1"]) == 1

    def test_trim_history_drops_leading_model_entry(self):
        adapter = GeminiAdapter(provider_key="test-key", max_history_messages=3)
        adapter._message_history["room-1"] = [
            types.Content(role="user", parts=[types.Part.from_text(text="msg-0")]),
            types.Content(role="model", parts=[types.Part.from_text(text="reply-0")]),
            types.Content(role="user", parts=[types.Part.from_text(text="msg-1")]),
            types.Content(role="model", parts=[types.Part.from_text(text="reply-1")]),
        ]

        adapter._trim_history("room-1")

        trimmed = adapter._message_history["room-1"]
        assert len(trimmed) == 2
        assert trimmed[0].role == "user"
        assert trimmed[0].parts[0].text == "msg-1"
        assert trimmed[1].role == "model"
        assert trimmed[1].parts[0].text == "reply-1"

    def test_trim_history_strips_orphaned_leading_tool_response_parts(self):
        adapter = GeminiAdapter(provider_key="test-key", max_history_messages=3)
        adapter._message_history["room-1"] = [
            types.Content(role="user", parts=[types.Part.from_text(text="msg-0")]),
            types.Content(
                role="model",
                parts=[
                    types.Part.from_function_call(
                        name="band_send_message",
                        args={"content": "hello"},
                    )
                ],
            ),
            types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            id="tc_1",
                            name="band_send_message",
                            response={"output": {"status": "sent"}},
                        )
                    ),
                    types.Part.from_text(text="[Alice]: follow-up"),
                ],
            ),
            types.Content(role="model", parts=[types.Part.from_text(text="reply-1")]),
        ]

        adapter._trim_history("room-1")

        trimmed = adapter._message_history["room-1"]
        assert len(trimmed) == 2
        assert trimmed[0].role == "user"
        assert len(trimmed[0].parts) == 1
        assert trimmed[0].parts[0].function_response is None
        assert trimmed[0].parts[0].text == "[Alice]: follow-up"
        assert trimmed[1].role == "model"
        assert trimmed[1].parts[0].text == "reply-1"

    @pytest.mark.asyncio
    async def test_cleanup_before_any_messages(self):
        adapter = GeminiAdapter(provider_key="test-key")
        await adapter.on_started("TestBot", "Test bot")
        await adapter.on_cleanup("room-never-used")  # Should not raise


class TestValidationErrorHandling:
    @pytest.mark.asyncio
    async def test_validation_error_returns_friendly_message(self, mock_tools):
        from pydantic import ValidationError

        adapter = GeminiAdapter(provider_key="test-key")

        mock_tools.execute_tool_call = AsyncMock(
            side_effect=ValidationError.from_exception_data(
                title="SendMessageInput",
                line_errors=[
                    {
                        "type": "missing",
                        "loc": ("content",),
                        "msg": "Field required",
                        "input": {},
                    }
                ],
            )
        )

        function_calls = [
            types.FunctionCall(name="band_send_message", args={}, id="call_1")
        ]
        parts = await adapter._process_function_calls(function_calls, mock_tools)

        assert len(parts) == 1
        resp = parts[0].function_response
        assert resp is not None
        assert "Invalid arguments for band_send_message" in resp.response["error"]
        assert "content" in resp.response["error"]


class TestMaxToolRounds:
    @pytest.mark.asyncio
    async def test_raises_runtime_error_when_max_rounds_exceeded(
        self, sample_message, mock_tools
    ):
        adapter = GeminiAdapter(
            max_tool_rounds=2,
            provider_key="test-key",
        )
        await adapter.on_started("TestBot", "Test bot")

        # Always return a function call so the loop never terminates naturally
        with patch.object(
            adapter,
            "_call_gemini",
            AsyncMock(
                return_value=_response_with_function_call(
                    "band_lookup_peers", {"page": "1"}, "call_1"
                )
            ),
        ):
            with pytest.raises(RuntimeError, match="Exceeded max tool rounds"):
                await adapter.on_message(
                    msg=sample_message,
                    tools=mock_tools,
                    history=[],
                    participants_msg=None,
                    contacts_msg=None,
                    is_session_bootstrap=True,
                    room_id="room-123",
                )


class TestHttpxRetries:
    @pytest.mark.asyncio
    async def test_retries_on_timeout_exception(self):
        adapter = GeminiAdapter(
            max_retries=1,
            retry_base_delay_s=0,
            provider_key="test-key",
        )
        adapter._system_prompt = "system"
        adapter.client = MagicMock()
        adapter.client.aio.models.generate_content = AsyncMock(
            side_effect=[
                httpx.TimeoutException("timeout"),
                _response_with_text("ok"),
            ]
        )

        with patch.object(
            adapter.client.aio.models,  # type: ignore[union-attr]
            "generate_content",
            adapter.client.aio.models.generate_content,
        ) as mocked:
            response = await adapter._call_gemini(
                contents=[
                    types.Content(role="user", parts=[types.Part.from_text(text="x")])
                ],
                tools=[],
            )

        assert mocked.call_count == 2
        assert response.candidates[0].content.parts[0].text == "ok"

    @pytest.mark.asyncio
    async def test_retries_on_transport_error(self):
        adapter = GeminiAdapter(
            max_retries=1,
            retry_base_delay_s=0,
            provider_key="test-key",
        )
        adapter._system_prompt = "system"
        adapter.client = MagicMock()
        adapter.client.aio.models.generate_content = AsyncMock(
            side_effect=[
                httpx.TransportError("connection reset"),
                _response_with_text("ok"),
            ]
        )

        with patch.object(
            adapter.client.aio.models,  # type: ignore[union-attr]
            "generate_content",
            adapter.client.aio.models.generate_content,
        ) as mocked:
            response = await adapter._call_gemini(
                contents=[
                    types.Content(role="user", parts=[types.Part.from_text(text="x")])
                ],
                tools=[],
            )

        assert mocked.call_count == 2
        assert response.candidates[0].content.parts[0].text == "ok"

    @pytest.mark.asyncio
    async def test_raises_after_exhausting_retries(self):
        adapter = GeminiAdapter(
            max_retries=1,
            retry_base_delay_s=0,
            provider_key="test-key",
        )
        adapter._system_prompt = "system"
        adapter.client = MagicMock()
        adapter.client.aio.models.generate_content = AsyncMock(
            side_effect=httpx.TimeoutException("timeout")
        )

        with patch.object(
            adapter.client.aio.models,  # type: ignore[union-attr]
            "generate_content",
            adapter.client.aio.models.generate_content,
        ):
            with pytest.raises(httpx.TimeoutException):
                await adapter._call_gemini(
                    contents=[
                        types.Content(
                            role="user", parts=[types.Part.from_text(text="x")]
                        )
                    ],
                    tools=[],
                )


class TestParticipantsContactsInjection:
    @pytest.mark.asyncio
    async def test_participants_msg_injected_into_history(
        self, sample_message, mock_tools
    ):
        adapter = GeminiAdapter(provider_key="test-key")
        await adapter.on_started("TestBot", "Test bot")

        with patch.object(
            adapter, "_call_gemini", AsyncMock(return_value=_response_with_text("ok"))
        ):
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg="Alice, Bob are in the room",
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

        history = adapter._message_history["room-123"]
        # First entry should be participants system message
        assert "[System]: Alice, Bob are in the room" in history[0].parts[0].text

    @pytest.mark.asyncio
    async def test_contacts_msg_injected_into_history(self, sample_message, mock_tools):
        adapter = GeminiAdapter(provider_key="test-key")
        await adapter.on_started("TestBot", "Test bot")

        with patch.object(
            adapter, "_call_gemini", AsyncMock(return_value=_response_with_text("ok"))
        ):
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg="Charlie is now a contact",
                is_session_bootstrap=True,
                room_id="room-123",
            )

        history = adapter._message_history["room-123"]
        assert "[System]: Charlie is now a contact" in history[0].parts[0].text

    @pytest.mark.asyncio
    async def test_both_participants_and_contacts_injected(
        self, sample_message, mock_tools
    ):
        adapter = GeminiAdapter(provider_key="test-key")
        await adapter.on_started("TestBot", "Test bot")

        with patch.object(
            adapter, "_call_gemini", AsyncMock(return_value=_response_with_text("ok"))
        ):
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg="Alice, Bob",
                contacts_msg="Charlie added",
                is_session_bootstrap=True,
                room_id="room-123",
            )

        history = adapter._message_history["room-123"]
        # All user-side content merged into a single Content entry
        user_entry = history[0]
        assert len(user_entry.parts) == 3
        assert "[System]: Alice, Bob" in user_entry.parts[0].text
        assert "[System]: Charlie added" in user_entry.parts[1].text
        assert "Alice" in user_entry.parts[2].text  # user message


class TestEmptyCandidates:
    def test_extract_candidate_content_returns_none_for_empty_response(self):
        adapter = GeminiAdapter(provider_key="test-key")
        response = MagicMock()
        response.candidates = []
        response.function_calls = []

        content = adapter._extract_candidate_content(response)
        assert content is None

    def test_extract_candidate_content_returns_none_when_no_candidates(self):
        adapter = GeminiAdapter(provider_key="test-key")
        response = MagicMock()
        response.candidates = None
        response.function_calls = []

        content = adapter._extract_candidate_content(response)
        assert content is None
