"""Tests for GeminiAdapter."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from google.genai import types
from google.genai.errors import ServerError
from pydantic import BaseModel, Field, ValidationError

from band.adapters.gemini import GeminiAdapter, GeminiAdapterConfig
from band.core.exceptions import ProviderRunError
from band.core.protocols import (
    GENERIC_PROVIDER_FAILURE_MESSAGE,
    TurnResultAlreadyReported,
)
from band.core.types import Emit, PlatformMessage, ToolEventKey
from band.runtime.tools import BandTool
from band.testing import (
    MISSING_REPLY_FAILURE,
    FakeAgentTools,
    failure_reports,
    reported_failures,
)
from tests.adapters.genaikit import (
    SCRIPTED_USAGE,
    PlatformSchemaFakeTools,
    prompt_blocked,
    stopped,
    text_reply,
    tool_call,
)
from tests.adapters.usage_events import recorded_usage_payloads
from tests.framework_conformance.turnprobes import (
    CUSTOM_TOOL_DECLARATIONS,
    turn_input,
)


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
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def mock_tools() -> MagicMock:
    """Create mock AgentToolsProtocol (MagicMock base, AsyncMock methods)."""
    tools = MagicMock()
    tools.get_openai_tool_schemas = MagicMock(return_value=[])
    tools.send_message = AsyncMock(return_value={"status": "sent"})
    tools.send_event = AsyncMock(return_value={"status": "sent"})
    tools.send_failure = AsyncMock(return_value={"status": "sent"})
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


@pytest.fixture
def scripted_adapter() -> Callable[..., Awaitable[GeminiAdapter]]:
    """Build a started adapter whose client returns ``responses`` in order."""

    async def build(
        *responses: types.GenerateContentResponse, **features: Any
    ) -> GeminiAdapter:
        adapter = GeminiAdapter(**features)
        await adapter.on_started("TestBot", "Test bot")
        adapter.client = MagicMock()
        adapter.client.aio.models.generate_content = AsyncMock(
            side_effect=list(responses)
        )
        return adapter

    return build


class TestConfig:
    @pytest.mark.parametrize(
        ("setting", "value"),
        [
            ("max_output_tokens", 0),
            ("max_tool_rounds", 0),
            ("max_retries", -1),
            ("retry_base_delay_s", -0.5),
            ("max_history_messages", 0),
        ],
    )
    def test_rejects_out_of_range_settings(self, setting: str, value: float) -> None:
        with pytest.raises(ValueError, match=setting):
            GeminiAdapterConfig.model_validate({setting: value})

    def test_provider_key_authenticates_the_client(self) -> None:
        adapter = GeminiAdapter(GeminiAdapterConfig(provider_key="AIza-test-key"))

        with patch("band.adapters.gemini.genai.Client") as client_cls:
            adapter._ensure_client()

        client_cls.assert_called_once_with(api_key="AIza-test-key")

    @pytest.mark.asyncio
    async def test_requests_use_configured_model_and_sampling(self) -> None:
        adapter = GeminiAdapter(
            GeminiAdapterConfig(
                model="gemini-test-model", max_output_tokens=321, temperature=0.2
            )
        )
        generate = AsyncMock(return_value=_response_with_text("ok"))
        adapter.client = MagicMock()
        adapter.client.aio.models.generate_content = generate

        await adapter._call_gemini(contents=[], tools=[])

        request = generate.call_args.kwargs
        assert request["model"] == "gemini-test-model"
        assert request["config"].max_output_tokens == 321
        assert request["config"].temperature == 0.2


class TestOnStarted:
    @pytest.mark.asyncio
    async def test_renders_system_prompt(self):
        adapter = GeminiAdapter()
        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")
        assert adapter._system_prompt != ""
        assert "TestBot" in adapter._system_prompt

    @pytest.mark.asyncio
    async def test_uses_custom_system_prompt_when_provided(self):
        adapter = GeminiAdapter(
            GeminiAdapterConfig(
                system_prompt="Custom prompt here.", custom_section="Be terse."
            )
        )
        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")
        assert adapter._system_prompt == "Custom prompt here."


class TestOnMessage:
    @pytest.mark.asyncio
    async def test_initializes_history_on_bootstrap(self, sample_message, mock_tools):
        adapter = GeminiAdapter()
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
            emit=Emit.TOOL_CALLS,
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
            emit=Emit.TOOL_CALLS,
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
        adapter = GeminiAdapter()
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


class TestErrorReporting:
    @pytest.mark.asyncio
    async def test_reports_generic_failure(self, sample_message, mock_tools):
        adapter = GeminiAdapter()
        await adapter.on_started("TestBot", "Test bot")

        with (
            patch.object(
                adapter, "_call_gemini", AsyncMock(side_effect=Exception("boom"))
            ),
            pytest.raises(Exception, match="boom"),
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
        assert failure.provider == "gemini"
        assert failure.message == GENERIC_PROVIDER_FAILURE_MESSAGE
        assert failure.code is None
        assert failure.detail is None

    @pytest.mark.asyncio
    async def test_preserves_server_error_status_and_message(
        self, sample_message, mock_tools
    ):
        """ServerError's status/message are real provider data -- preserve
        them as code/detail rather than falling back to the generic shape."""
        adapter = GeminiAdapter()
        await adapter.on_started("TestBot", "Test bot")
        error = ServerError(
            503, {"error": {"status": "UNAVAILABLE", "message": "overloaded"}}, None
        )

        with (
            patch.object(adapter, "_call_gemini", AsyncMock(side_effect=error)),
            pytest.raises(ServerError),
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

        failure = mock_tools.send_failure.call_args.args[0]
        assert failure.provider == "gemini"
        assert failure.code == "UNAVAILABLE"
        assert failure.detail == "overloaded"

    @pytest.mark.asyncio
    async def test_non_string_server_error_status_is_stringified(
        self, sample_message, mock_tools
    ):
        """A malformed error body's non-string ``status`` field must not
        crash failure reporting -- band_sdk_core's AgentFailure requires
        code: str | None, but ServerError.status is an unconstrained
        Optional[str] at runtime (parsed straight off the response JSON)."""
        adapter = GeminiAdapter()
        await adapter.on_started("TestBot", "Test bot")
        error = ServerError(503, {"status": 503, "message": "backend overloaded"}, None)

        with (
            patch.object(adapter, "_call_gemini", AsyncMock(side_effect=error)),
            pytest.raises(ServerError),
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
        assert failure.provider == "gemini"
        assert failure.code == "503"
        assert failure.detail == "backend overloaded"


class TestModelFailuresReturnedAsData:
    """A blocked prompt or a safety stop arrives as a normal response; the
    adapter must still fail the turn, even after the turn did real work."""

    @pytest.mark.parametrize(
        ("script", "code"),
        [
            pytest.param([stopped(types.FinishReason.SAFETY)], "SAFETY", id="safety"),
            pytest.param(
                [prompt_blocked(types.BlockedReason.PROHIBITED_CONTENT)],
                "PROHIBITED_CONTENT",
                id="prompt-blocked",
            ),
            pytest.param(
                [
                    tool_call(BandTool.CREATE_CHATROOM, {}),
                    stopped(types.FinishReason.SAFETY),
                ],
                "SAFETY",
                id="safety-after-real-work",
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_reports_the_failure_code_without_the_provider_text(
        self, scripted_adapter, script, code
    ):
        adapter = await scripted_adapter(*script)
        tools = PlatformSchemaFakeTools()

        with pytest.raises(ProviderRunError):
            await adapter.on_event(turn_input(tools))

        assert reported_failures(tools) == [
            {
                "provider": "gemini",
                "code": code,
                "message": GENERIC_PROVIDER_FAILURE_MESSAGE,
                "detail": None,
            }
        ]

    @pytest.mark.asyncio
    async def test_empty_normal_finish_is_only_a_missing_reply(self, scripted_adapter):
        adapter = await scripted_adapter(stopped(types.FinishReason.STOP))
        tools = PlatformSchemaFakeTools()

        with pytest.raises(TurnResultAlreadyReported):
            await adapter.on_event(turn_input(tools))

        assert failure_reports(tools) == [MISSING_REPLY_FAILURE]

    @pytest.mark.asyncio
    async def test_reply_cut_off_at_max_tokens_completes(self, scripted_adapter):
        adapter = await scripted_adapter(
            tool_call(BandTool.SEND_MESSAGE, {"content": "Hi", "mentions": ["Alice"]}),
            text_reply("Anything else", finish=types.FinishReason.MAX_TOKENS),
        )
        tools = PlatformSchemaFakeTools()

        await adapter.on_event(turn_input(tools))

        assert reported_failures(tools) == []

    @pytest.mark.asyncio
    async def test_failed_run_still_emits_its_usage(self, scripted_adapter):
        adapter = await scripted_adapter(
            stopped(types.FinishReason.SAFETY), emit={Emit.USAGE}
        )
        tools = PlatformSchemaFakeTools()

        with pytest.raises(ProviderRunError):
            await adapter.on_event(turn_input(tools))

        [usage] = recorded_usage_payloads(tools)
        assert usage["input_tokens"] == SCRIPTED_USAGE.prompt_token_count


class TestRetries:
    @pytest.mark.asyncio
    async def test_retries_transient_server_errors(self):
        adapter = GeminiAdapter(
            GeminiAdapterConfig(max_retries=1, retry_base_delay_s=0)
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


class TestBuildGeminiTools:
    """Gemini rejects numeric bounds and additionalProperties on tool params, so
    the adapter must sanitize Band schemas before building declarations."""

    def test_declarations_drop_numeric_bounds_and_additional_properties(
        self, mock_tools
    ):
        mock_tools.get_openai_tool_schemas = MagicMock(
            return_value=[
                {
                    "type": "function",
                    "function": {
                        "name": "band_lookup_peers",
                        "description": "lookup peers",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "page_size": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "maximum": 100,
                                },
                            },
                            "additionalProperties": False,
                        },
                    },
                }
            ]
        )
        adapter = GeminiAdapter()

        tools = adapter._build_gemini_tools(mock_tools)

        decl = tools[0].function_declarations[0]
        schema = decl.parameters_json_schema
        assert "additionalProperties" not in schema
        page_size = schema["properties"]["page_size"]
        assert "minimum" not in page_size
        assert "maximum" not in page_size


class TestCustomTools:
    @pytest.mark.asyncio
    async def test_executes_custom_tool(self, mock_tools):
        class EchoInput(BaseModel):
            text: str = Field(...)

        async def echo_tool(inp: EchoInput) -> str:
            return inp.text

        adapter = GeminiAdapter(
            additional_tools=[(EchoInput, echo_tool)],
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

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("declare", "complete"), CUSTOM_TOOL_DECLARATIONS)
    async def test_custom_tool_records_its_effect_on_the_room_turn(
        self, sample_message, declare: Callable[..., Any], complete: bool
    ):
        class FileInput(BaseModel):
            """File the report."""

            note: str

        @declare
        async def file_report(inp: FileInput) -> str:
            return "filed"

        tools = FakeAgentTools(room_id="room-123")
        adapter = GeminiAdapter(additional_tools=[(FileInput, file_report)])
        await adapter.on_started("TestBot", "Test bot")

        with patch.object(
            adapter,
            "_call_gemini",
            AsyncMock(
                side_effect=[
                    _response_with_function_call("file", {"note": "go"}, "call_1"),
                    _response_with_text(""),
                ]
            ),
        ):
            await adapter.on_message(
                msg=sample_message,
                tools=tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

        assert tools.turn.complete is complete


class TestReadRoomFileImagePassthrough:
    @pytest.mark.asyncio
    async def test_image_result_passes_through_as_inline_data(self, mock_tools):
        mock_tools.execute_tool_call = AsyncMock(
            return_value={
                "content": [
                    {"type": "image", "data": "ZmFrZQ==", "mimeType": "image/png"}
                ]
            }
        )
        adapter = GeminiAdapter()
        function_calls = [
            types.FunctionCall(
                name="band_read_room_file", args={"file_id": "f1"}, id="c1"
            )
        ]

        parts = await adapter._process_function_calls(function_calls, mock_tools)

        function_response = parts[0].function_response
        assert function_response is not None
        assert function_response.parts is not None
        assert len(function_response.parts) == 1
        inline_data = function_response.parts[0].inline_data
        assert inline_data is not None
        assert inline_data.mime_type == "image/png"
        assert inline_data.data == b"fake"

    @pytest.mark.asyncio
    async def test_non_image_result_stays_text(self, mock_tools):
        mock_tools.execute_tool_call = AsyncMock(
            return_value={"name": "notes.txt", "content_type": "text/plain"}
        )
        adapter = GeminiAdapter()
        function_calls = [
            types.FunctionCall(
                name="band_read_room_file", args={"file_id": "f1"}, id="c1"
            )
        ]

        parts = await adapter._process_function_calls(function_calls, mock_tools)

        function_response = parts[0].function_response
        assert function_response is not None
        assert function_response.parts is None
        assert function_response.response == {
            "output": '{"name": "notes.txt", "content_type": "text/plain"}'
        }


class TestToolEventRedaction:
    @pytest.mark.asyncio
    async def test_read_room_file_image_result_reports_placeholder_not_raw_base64(
        self, mock_tools
    ):
        """The tool_result event for an image band_read_room_file call must
        report a bounded placeholder, not the raw base64 payload -- the LLM-
        facing inline_data content block (asserted in
        TestReadRoomFileImagePassthrough above) is a separate path from what
        gets reported to the platform-visible event."""

        mock_tools.execute_tool_call = AsyncMock(
            return_value={
                "content": [
                    {"type": "image", "data": "ZmFrZQ==", "mimeType": "image/png"}
                ]
            }
        )
        adapter = GeminiAdapter(emit=Emit.TOOL_CALLS)
        function_calls = [
            types.FunctionCall(
                name="band_read_room_file", args={"file_id": "f1"}, id="c1"
            )
        ]

        await adapter._process_function_calls(function_calls, mock_tools)

        result_event = mock_tools.send_event.call_args_list[-1]
        reported = json.loads(result_event.kwargs["content"])
        assert reported[ToolEventKey.OUTPUT] == "<1 image content block(s)>"
        assert "ZmFrZQ==" not in result_event.kwargs["content"]

    @pytest.mark.asyncio
    async def test_send_room_file_reports_content_placeholder_not_raw_bytes(
        self, mock_tools
    ):
        """The tool_call event for band_send_room_file must report a bounded
        placeholder for `args`, not the raw file text -- real file bytes
        (up to ~1MB) have no business in a platform-visible log event."""

        mock_tools.execute_tool_call = AsyncMock(return_value={"status": "success"})
        adapter = GeminiAdapter(emit=Emit.TOOL_CALLS)
        raw_content = "the quick brown fox" * 100
        function_calls = [
            types.FunctionCall(
                name="band_send_room_file",
                args={"content": raw_content, "filename": "notes.txt"},
                id="c1",
            )
        ]

        await adapter._process_function_calls(function_calls, mock_tools)

        call_event = mock_tools.send_event.call_args_list[0]
        reported = json.loads(call_event.kwargs["content"])
        assert reported[ToolEventKey.ARGS]["content"] == (
            f"<{len(raw_content.encode('utf-8'))} byte file content>"
        )
        assert raw_content not in call_event.kwargs["content"]


class TestOnCleanup:
    @pytest.mark.asyncio
    async def test_removes_room_history(self):
        adapter = GeminiAdapter()
        adapter._message_history["room-1"] = [
            types.Content(role="user", parts=[types.Part.from_text(text="hi")])
        ]
        await adapter.on_cleanup("room-1")
        assert "room-1" not in adapter._message_history

    @pytest.mark.asyncio
    async def test_cleanup_twice_is_idempotent(self):
        adapter = GeminiAdapter()
        adapter._message_history["room-1"] = []
        await adapter.on_cleanup("room-1")
        await adapter.on_cleanup("room-1")  # Should not raise
        assert "room-1" not in adapter._message_history

    @pytest.mark.asyncio
    async def test_cleanup_unknown_room_is_noop(self):
        adapter = GeminiAdapter()
        await adapter.on_cleanup("nonexistent-room")  # Should not raise
        assert "nonexistent-room" not in adapter._message_history

    def test_trim_history_caps_at_max(self):
        adapter = GeminiAdapter(GeminiAdapterConfig(max_history_messages=5))
        adapter._message_history["room-1"] = [
            types.Content(role="user", parts=[types.Part.from_text(text=f"msg-{i}")])
            for i in range(10)
        ]
        adapter._trim_history("room-1")
        assert len(adapter._message_history["room-1"]) == 5
        # Keeps the most recent messages
        assert adapter._message_history["room-1"][0].parts[0].text == "msg-5"

    def test_trim_history_noop_when_under_limit(self):
        adapter = GeminiAdapter(GeminiAdapterConfig(max_history_messages=50))
        adapter._message_history["room-1"] = [
            types.Content(role="user", parts=[types.Part.from_text(text="hi")])
        ]
        adapter._trim_history("room-1")
        assert len(adapter._message_history["room-1"]) == 1

    def test_trim_history_drops_leading_model_entry(self):
        adapter = GeminiAdapter(GeminiAdapterConfig(max_history_messages=3))
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
        adapter = GeminiAdapter(GeminiAdapterConfig(max_history_messages=3))
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
        adapter = GeminiAdapter()
        await adapter.on_started("TestBot", "Test bot")
        await adapter.on_cleanup("room-never-used")  # Should not raise


class TestValidationErrorHandling:
    @pytest.mark.asyncio
    async def test_validation_error_returns_friendly_message(self, mock_tools):
        adapter = GeminiAdapter()

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
        adapter = GeminiAdapter(GeminiAdapterConfig(max_tool_rounds=2))
        await adapter.on_started("TestBot", "Test bot")

        # Always return a function call so the loop never terminates naturally
        with (
            patch.object(
                adapter,
                "_call_gemini",
                AsyncMock(
                    return_value=_response_with_function_call(
                        "band_lookup_peers", {"page": "1"}, "call_1"
                    )
                ),
            ),
            pytest.raises(RuntimeError, match="Exceeded max tool rounds"),
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

        # Exceeding max tool rounds is a reportable provider failure.
        mock_tools.send_failure.assert_called_once()
        failure = mock_tools.send_failure.call_args.args[0]
        assert failure.provider == "gemini"
        assert "Exceeded max tool rounds" in failure.message


class TestHttpxRetries:
    @pytest.mark.asyncio
    async def test_retries_on_timeout_exception(self):
        adapter = GeminiAdapter(
            GeminiAdapterConfig(max_retries=1, retry_base_delay_s=0)
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
            GeminiAdapterConfig(max_retries=1, retry_base_delay_s=0)
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
            GeminiAdapterConfig(max_retries=1, retry_base_delay_s=0)
        )
        adapter._system_prompt = "system"
        adapter.client = MagicMock()
        adapter.client.aio.models.generate_content = AsyncMock(
            side_effect=httpx.TimeoutException("timeout")
        )

        with (
            patch.object(
                adapter.client.aio.models,  # type: ignore[union-attr]
                "generate_content",
                adapter.client.aio.models.generate_content,
            ),
            pytest.raises(httpx.TimeoutException),
        ):
            await adapter._call_gemini(
                contents=[
                    types.Content(role="user", parts=[types.Part.from_text(text="x")])
                ],
                tools=[],
            )


class TestParticipantsContactsInjection:
    @pytest.mark.asyncio
    async def test_participants_msg_injected_into_history(
        self, sample_message, mock_tools
    ):
        adapter = GeminiAdapter()
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
        adapter = GeminiAdapter()
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
        adapter = GeminiAdapter()
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
        adapter = GeminiAdapter()
        response = MagicMock()
        response.candidates = []
        response.function_calls = []

        content = adapter._extract_candidate_content(response)
        assert content is None

    def test_extract_candidate_content_returns_none_when_no_candidates(self):
        adapter = GeminiAdapter()
        response = MagicMock()
        response.candidates = None
        response.function_calls = []

        content = adapter._extract_candidate_content(response)
        assert content is None
