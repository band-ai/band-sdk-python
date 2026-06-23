"""Dispatch helpers for Tier-1 baseline conformance rows."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast
import pytest
from pydantic import BaseModel

from band.core.protocols import AgentToolsProtocol
from band.core.types import AgentInput, HistoryProvider, PlatformMessage
from tests.baseline_l1_fixtures import (
    L1_CUSTOM_TOOL_NAME,
    LogKeywordInput,
    make_l1_langgraph_structured_tool,
    make_l1_pydantic_ai_tool,
)
from tests.framework_conformance.injection_registry import (
    INJECTION_BINDINGS,
    ObservationPath,
    bindings_by_adapter,
    tier1_dependency_blocked_reason,
)
from tests.framework_conformance.platform_fixtures import ROOM_ID, USER_ID
from tests.framework_conformance.request_capture import (
    ConformanceSchemaRecorder,
    tier1_sentinel_provider_env,
)

HONEST_DISPATCH_ADAPTER_IDS = tuple(
    binding.adapter for binding in INJECTION_BINDINGS if binding.is_honest()
)
_BINDINGS_BY_ADAPTER = bindings_by_adapter()


def _assert_dispatch_dependencies(adapter_id: str) -> None:
    binding = _BINDINGS_BY_ADAPTER[adapter_id]
    blocked_reason = tier1_dependency_blocked_reason(binding)
    if blocked_reason is not None:
        # Missing optional framework dep (e.g. crewai in the default dev lane):
        # skip, not fail — the adapter is covered in its own lane.
        pytest.skip(blocked_reason)


@dataclass(frozen=True, kw_only=True)
class DispatchResult:
    adapter_id: str
    tool_name: str
    arguments: dict[str, Any]
    tool_calls: list[dict[str, Any]]
    messages_sent: list[dict[str, Any]]
    participants_added: list[dict[str, Any]]
    participants_removed: list[dict[str, Any]]
    context_calls: list[dict[str, Any]]


@dataclass(frozen=True, kw_only=True)
class CustomDispatchResult:
    adapter_id: str
    tool_name: str
    arguments: dict[str, Any]
    calls: list[LogKeywordInput]
    tool_calls: list[dict[str, Any]]


def make_dispatch_agent_input(tools: ConformanceSchemaRecorder) -> AgentInput:
    msg = PlatformMessage(
        id="dispatch-message",
        room_id=ROOM_ID,
        content="Please execute the scripted tool decision.",
        sender_id=USER_ID,
        sender_type="User",
        sender_name="Darvell",
        message_type="text",
        metadata=None,
        created_at=datetime.now(timezone.utc),
    )
    return AgentInput(
        msg=msg,
        tools=tools,
        history=HistoryProvider(raw=[]),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id=ROOM_ID,
    )


def _participant_id_for(identifier: str) -> str:
    try:
        return str(uuid.UUID(identifier))
    except ValueError:
        return f"p-{identifier}"


def _assert_expected_execute_tool_call(result: DispatchResult) -> None:
    expected_call = {"tool_name": result.tool_name, "arguments": result.arguments}
    binding = _BINDINGS_BY_ADAPTER[result.adapter_id]
    if ObservationPath.EXECUTE_TOOL_CALL in binding.observation_paths:
        assert result.tool_calls == [expected_call]
    else:
        assert result.tool_calls == []


def assert_dispatch_result(result: DispatchResult) -> None:
    if result.tool_name == "band_send_message":
        assert result.messages_sent == [
            {
                "id": "msg-0",
                "content": result.arguments["content"],
                "mentions": result.arguments["mentions"],
            }
        ]
        _assert_expected_execute_tool_call(result)
        assert result.participants_added == []
        assert result.participants_removed == []
        assert result.context_calls == []
        return
    if result.tool_name == "band_add_participant":
        identifier = result.arguments["identifier"]
        assert result.participants_added == [
            {
                "id": _participant_id_for(identifier),
                "name": identifier,
                "role": result.arguments["role"],
                "handle": identifier,
            }
        ]
        _assert_expected_execute_tool_call(result)
        assert result.messages_sent == []
        assert result.participants_removed == []
        assert result.context_calls == []
        return
    if result.tool_name == "band_remove_participant":
        assert result.participants_removed == [
            {
                "id": f"p-{result.arguments['identifier']}",
                "name": result.arguments["identifier"],
            }
        ]
        _assert_expected_execute_tool_call(result)
        assert result.messages_sent == []
        assert result.participants_added == []
        assert result.context_calls == []
        return
    if result.tool_name in {"band_get_participants", "band_lookup_peers"}:
        assert result.tool_calls == [
            {"tool_name": result.tool_name, "arguments": result.arguments}
        ]
        assert result.messages_sent == []
        assert result.participants_added == []
        assert result.participants_removed == []
        assert result.context_calls == []
        return
    raise AssertionError(f"Unhandled dispatch tool {result.tool_name}")


async def dispatch_tool(
    adapter_id: str,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    tools: ConformanceSchemaRecorder,
) -> DispatchResult:
    _assert_dispatch_dependencies(adapter_id)
    if adapter_id == "anthropic":
        await _dispatch_anthropic(tool_name, arguments, tools)
    elif adapter_id == "gemini":
        await _dispatch_gemini(tool_name, arguments, tools)
    elif adapter_id == "google_adk":
        await _dispatch_google_adk(tool_name, arguments, tools)
    elif adapter_id == "langgraph":
        await _dispatch_langgraph(tool_name, arguments, tools)
    elif adapter_id == "pydantic_ai":
        await _dispatch_pydantic_ai(tool_name, arguments, tools)
    elif adapter_id == "codex":
        await _dispatch_codex(tool_name, arguments, tools)
    else:
        raise AssertionError(f"No honest dispatch helper registered for {adapter_id}")

    return DispatchResult(
        adapter_id=adapter_id,
        tool_name=tool_name,
        arguments=arguments,
        tool_calls=list(tools.tool_calls),
        messages_sent=list(tools.messages_sent),
        participants_added=list(tools.participants_added),
        participants_removed=list(tools.participants_removed),
        context_calls=list(tools.context_calls),
    )


async def dispatch_l1_custom_tool(
    adapter_id: str,
    *,
    message: str,
    tools: ConformanceSchemaRecorder,
) -> CustomDispatchResult:
    _assert_dispatch_dependencies(adapter_id)
    calls: list[LogKeywordInput] = []
    tool_name = L1_CUSTOM_TOOL_NAME
    arguments = {"message": message}

    async def handler(args: LogKeywordInput) -> dict[str, str]:
        calls.append(args)
        return {"keyword": "FLIBBERTIGIBBET"}

    custom_tool = (LogKeywordInput, handler)

    if adapter_id == "anthropic":
        await _dispatch_l1_custom_anthropic(tool_name, arguments, custom_tool, tools)
    elif adapter_id == "gemini":
        await _dispatch_l1_custom_gemini(tool_name, arguments, custom_tool, tools)
    elif adapter_id == "google_adk":
        await _dispatch_l1_custom_google_adk(tool_name, arguments, custom_tool, tools)
    elif adapter_id == "langgraph":
        await _dispatch_l1_custom_langgraph(tool_name, arguments, calls, tools)
    elif adapter_id == "pydantic_ai":
        await _dispatch_l1_custom_pydantic_ai(tool_name, arguments, calls, tools)
    elif adapter_id == "codex":
        await _dispatch_l1_custom_codex(tool_name, arguments, custom_tool, tools)
    else:
        raise AssertionError(
            f"No honest custom dispatch helper registered for {adapter_id}"
        )

    return CustomDispatchResult(
        adapter_id=adapter_id,
        tool_name=tool_name,
        arguments=arguments,
        calls=list(calls),
        tool_calls=list(tools.tool_calls),
    )


async def _dispatch_anthropic(
    tool_name: str,
    arguments: dict[str, Any],
    tools: ConformanceSchemaRecorder,
) -> None:
    from anthropic.types import TextBlock, ToolUseBlock
    from band.adapters.anthropic import AnthropicAdapter

    class _Adapter(AnthropicAdapter):
        def __init__(self) -> None:
            super().__init__(provider_key="test-provider-key")
            self._responses = [
                type(
                    "_Response",
                    (),
                    {
                        "stop_reason": "tool_use",
                        "content": [
                            ToolUseBlock(
                                id="tool-use-1",
                                name=tool_name,
                                input=arguments,
                                type="tool_use",
                            )
                        ],
                    },
                )(),
                type(
                    "_Response",
                    (),
                    {
                        "stop_reason": "end_turn",
                        "content": [TextBlock(text="done", type="text")],
                    },
                )(),
            ]

        async def _call_anthropic(
            self,
            messages: list[dict[str, Any]],
            tools: list[Any],
        ) -> Any:
            del messages, tools
            return self._responses.pop(0)

    adapter = _Adapter()
    await adapter.on_started("Test Agent", "A conformance test agent")
    await adapter.on_event(make_dispatch_agent_input(tools))


async def _dispatch_gemini(
    tool_name: str,
    arguments: dict[str, Any],
    tools: ConformanceSchemaRecorder,
) -> None:
    pytest.importorskip("google.genai", reason="gemini extra not installed")
    from google.genai import types
    from band.adapters.gemini import GeminiAdapter

    class _Adapter(GeminiAdapter):
        def __init__(self) -> None:
            super().__init__(model="gemini-2.5-flash", provider_key="unused")
            self._responses = [
                types.GenerateContentResponse(
                    candidates=[
                        types.Candidate(
                            content=types.Content(
                                role="model",
                                parts=[
                                    types.Part(
                                        function_call=types.FunctionCall(
                                            name=tool_name,
                                            args=arguments,
                                            id="fc-1",
                                        )
                                    )
                                ],
                            )
                        )
                    ]
                ),
                types.GenerateContentResponse(
                    candidates=[
                        types.Candidate(
                            content=types.Content(
                                role="model", parts=[types.Part(text="done")]
                            )
                        )
                    ]
                ),
            ]

        async def _call_gemini(self, contents: Any, tools: list[Any]) -> Any:
            del contents, tools
            return self._responses.pop(0)

    adapter = _Adapter()
    await adapter.on_started("Test Agent", "A conformance test agent")
    await adapter.on_event(make_dispatch_agent_input(tools))


async def _dispatch_google_adk(
    tool_name: str,
    arguments: dict[str, Any],
    tools: ConformanceSchemaRecorder,
) -> None:
    pytest.importorskip("google.adk", reason="google-adk extra not installed")
    from google.adk import Agent as ADKAgent
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.adk.runners import InMemoryRunner
    from google.genai import types
    from band.adapters.google_adk import GoogleADKAdapter, _sanitize_adk_agent_name

    class _ScriptedBaseLlm(BaseLlm):
        @property
        def _llm_type(self) -> str:
            return "scripted-dispatch-fake"

        async def generate_content_async(
            self,
            llm_request: Any,
            stream: bool = False,
        ) -> Any:
            del llm_request, stream
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(
                                name=tool_name,
                                args=arguments,
                            )
                        )
                    ],
                )
            )
            yield LlmResponse(
                content=types.Content(role="model", parts=[types.Part(text="done")])
            )

    adapter = GoogleADKAdapter(model="gemini-2.5-flash")

    def _create_runner(adk_tools: AgentToolsProtocol) -> InMemoryRunner:
        adk_agent = ADKAgent(
            name=_sanitize_adk_agent_name(adapter.agent_name),
            model=_ScriptedBaseLlm(model="scripted"),
            instruction=adapter._system_prompt,
            tools=adapter._build_adk_tools(adk_tools),
        )
        return InMemoryRunner(agent=adk_agent, app_name="band")

    adapter._create_runner = _create_runner  # type: ignore[method-assign]
    await adapter.on_started("Test Agent", "A conformance test agent")
    await adapter.on_event(make_dispatch_agent_input(tools))


async def _drive_langgraph_script(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    tools: ConformanceSchemaRecorder,
    additional_tools: list[Any] | None = None,
) -> None:
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langgraph.checkpoint.memory import InMemorySaver
    from band.adapters.langgraph import LangGraphAdapter

    decisions = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": tool_name,
                    "args": arguments,
                    "id": "call-conformance",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(content="done"),
    ]

    class _ScriptedChatModel(BaseChatModel):
        remaining: list[Any]
        bound_tool_names: list[str] = []

        @property
        def _llm_type(self) -> str:
            return "scripted-conformance-dispatch"

        def _next(self) -> AIMessage:
            return self.remaining.pop(0)

        def _generate(
            self,
            messages: Any,
            stop: Any = None,
            run_manager: Any = None,
            **kwargs: Any,
        ) -> ChatResult:
            del messages, stop, run_manager, kwargs
            return ChatResult(generations=[ChatGeneration(message=self._next())])

        async def _agenerate(
            self,
            messages: Any,
            stop: Any = None,
            run_manager: Any = None,
            **kwargs: Any,
        ) -> ChatResult:
            del messages, stop, run_manager, kwargs
            return ChatResult(generations=[ChatGeneration(message=self._next())])

        def bind_tools(self, tools_to_bind: Any, **kwargs: Any) -> Any:
            del kwargs
            self.bound_tool_names = [
                str(getattr(tool, "name", ""))
                for tool in tools_to_bind
                if getattr(tool, "name", "")
            ]
            return self

    model = _ScriptedChatModel(remaining=list(decisions))
    adapter = LangGraphAdapter(
        llm=model,
        checkpointer=InMemorySaver(),
        additional_tools=additional_tools,
    )
    await adapter.on_started("Test Agent", "A conformance test agent")
    await adapter.on_event(make_dispatch_agent_input(tools))
    assert {tool_name, "band_send_message"} <= set(model.bound_tool_names)


async def _dispatch_langgraph(
    tool_name: str,
    arguments: dict[str, Any],
    tools: ConformanceSchemaRecorder,
) -> None:
    await _drive_langgraph_script(tool_name=tool_name, arguments=arguments, tools=tools)


async def _dispatch_pydantic_ai(
    tool_name: str,
    arguments: dict[str, Any],
    tools: ConformanceSchemaRecorder,
) -> None:
    pytest.importorskip("pydantic_ai", reason="pydantic-ai extra not installed")
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
    from band.adapters.pydantic_ai import PydanticAIAdapter

    cursor: list[tuple[str, str, dict[str, Any] | None]] = [
        ("tool", tool_name, arguments),
        ("text", "done", None),
    ]

    async def _stream(messages: list[ModelMessage], info: AgentInfo) -> Any:
        del messages, info
        kind, name_or_text, args = cursor.pop(0) if cursor else ("text", "done", None)
        if kind == "tool":
            yield {
                0: DeltaToolCall(
                    name=name_or_text,
                    json_args=json.dumps(args or {}),
                    tool_call_id="call-1",
                )
            }
        else:
            yield name_or_text

    with tier1_sentinel_provider_env():
        adapter = PydanticAIAdapter(model="openai:gpt-4o-mini")
        await adapter.on_started("Test Agent", "A conformance test agent")
        if adapter._agent is None:
            raise AssertionError("PydanticAIAdapter did not create an agent")
        with adapter._agent.override(model=FunctionModel(stream_function=_stream)):
            await adapter.on_event(make_dispatch_agent_input(tools))


async def _drive_codex_replay(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    tools: ConformanceSchemaRecorder,
    additional_tools: list[Any] | None = None,
) -> None:
    from band.adapters.codex import CodexAdapter, CodexAdapterConfig
    from tests.framework_conformance.codex_replay import (
        ReplayCodexClient,
        frames_with_tool_call,
    )

    client = ReplayCodexClient(frames_with_tool_call(tool_name, arguments))
    adapter = CodexAdapter(
        config=CodexAdapterConfig(
            transport="stdio",
            model="gpt-5.4",
            enable_task_events=False,
        ),
        additional_tools=additional_tools,
        client_factory=lambda _config: cast(Any, client),
    )
    await adapter.on_started("Test Agent", "A conformance test agent")
    await adapter.on_event(make_dispatch_agent_input(tools))
    assert {tool_name, "band_send_message"} <= set(
        client.thread_start_dynamic_tool_names
    )


async def _dispatch_codex(
    tool_name: str,
    arguments: dict[str, Any],
    tools: ConformanceSchemaRecorder,
) -> None:
    await _drive_codex_replay(tool_name=tool_name, arguments=arguments, tools=tools)


async def _dispatch_l1_custom_anthropic(
    tool_name: str,
    arguments: dict[str, Any],
    custom_tool: tuple[type[BaseModel], Any],
    tools: ConformanceSchemaRecorder,
) -> None:
    from anthropic.types import TextBlock, ToolUseBlock
    from band.adapters.anthropic import AnthropicAdapter

    class _Adapter(AnthropicAdapter):
        def __init__(self) -> None:
            super().__init__(
                provider_key="test-provider-key",
                additional_tools=[custom_tool],
            )
            self._responses = [
                type(
                    "_Response",
                    (),
                    {
                        "stop_reason": "tool_use",
                        "content": [
                            ToolUseBlock(
                                id="tool-use-l1-custom",
                                name=tool_name,
                                input=arguments,
                                type="tool_use",
                            )
                        ],
                    },
                )(),
                type(
                    "_Response",
                    (),
                    {
                        "stop_reason": "end_turn",
                        "content": [TextBlock(text="done", type="text")],
                    },
                )(),
            ]

        async def _call_anthropic(
            self,
            messages: list[dict[str, Any]],
            tools: list[Any],
        ) -> Any:
            del messages
            exposed_names = {schema.get("name") for schema in tools}
            assert {tool_name, "band_send_message"} <= exposed_names
            return self._responses.pop(0)

    adapter = _Adapter()
    await adapter.on_started("Test Agent", "A conformance test agent")
    await adapter.on_event(make_dispatch_agent_input(tools))


async def _dispatch_l1_custom_gemini(
    tool_name: str,
    arguments: dict[str, Any],
    custom_tool: tuple[type[BaseModel], Any],
    tools: ConformanceSchemaRecorder,
) -> None:
    pytest.importorskip("google.genai", reason="gemini extra not installed")
    from google.genai import types
    from band.adapters.gemini import GeminiAdapter

    class _Adapter(GeminiAdapter):
        def __init__(self) -> None:
            super().__init__(
                model="gemini-2.5-flash",
                provider_key="unused",
                additional_tools=[custom_tool],
            )
            self._responses = [
                types.GenerateContentResponse(
                    candidates=[
                        types.Candidate(
                            content=types.Content(
                                role="model",
                                parts=[
                                    types.Part(
                                        function_call=types.FunctionCall(
                                            name=tool_name,
                                            args=arguments,
                                            id="fc-l1-custom",
                                        )
                                    )
                                ],
                            )
                        )
                    ]
                ),
                types.GenerateContentResponse(
                    candidates=[
                        types.Candidate(
                            content=types.Content(
                                role="model", parts=[types.Part(text="done")]
                            )
                        )
                    ]
                ),
            ]

        async def _call_gemini(self, contents: Any, tools: list[Any]) -> Any:
            del contents
            declarations = tools[0].function_declarations
            exposed_names = {declaration.name for declaration in declarations or []}
            assert {tool_name, "band_send_message"} <= exposed_names
            return self._responses.pop(0)

    adapter = _Adapter()
    await adapter.on_started("Test Agent", "A conformance test agent")
    await adapter.on_event(make_dispatch_agent_input(tools))


async def _dispatch_l1_custom_google_adk(
    tool_name: str,
    arguments: dict[str, Any],
    custom_tool: tuple[type[BaseModel], Any],
    tools: ConformanceSchemaRecorder,
) -> None:
    pytest.importorskip("google.adk", reason="google-adk extra not installed")
    from google.adk import Agent as ADKAgent
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.adk.runners import InMemoryRunner
    from google.genai import types
    from band.adapters.google_adk import GoogleADKAdapter, _sanitize_adk_agent_name

    class _ScriptedBaseLlm(BaseLlm):
        @property
        def _llm_type(self) -> str:
            return "scripted-l1-custom-fake"

        async def generate_content_async(
            self,
            llm_request: Any,
            stream: bool = False,
        ) -> Any:
            del llm_request, stream
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(
                                name=tool_name,
                                args=arguments,
                            )
                        )
                    ],
                )
            )
            yield LlmResponse(
                content=types.Content(role="model", parts=[types.Part(text="done")])
            )

    adapter = GoogleADKAdapter(model="gemini-2.5-flash", additional_tools=[custom_tool])

    def _create_runner(adk_tools: AgentToolsProtocol) -> InMemoryRunner:
        built_tools = adapter._build_adk_tools(adk_tools)
        exposed_names = {getattr(tool, "name", "") for tool in built_tools}
        assert {tool_name, "band_send_message"} <= exposed_names
        adk_agent = ADKAgent(
            name=_sanitize_adk_agent_name(adapter.agent_name),
            model=_ScriptedBaseLlm(model="scripted"),
            instruction=adapter._system_prompt,
            tools=built_tools,
        )
        return InMemoryRunner(agent=adk_agent, app_name="band")

    adapter._create_runner = _create_runner  # type: ignore[method-assign]
    await adapter.on_started("Test Agent", "A conformance test agent")
    await adapter.on_event(make_dispatch_agent_input(tools))


async def _dispatch_l1_custom_langgraph(
    tool_name: str,
    arguments: dict[str, Any],
    calls: list[LogKeywordInput],
    tools: ConformanceSchemaRecorder,
) -> None:
    pytest.importorskip("langchain", reason="langgraph extra not installed")

    async def handler(args: LogKeywordInput) -> dict[str, str]:
        calls.append(args)
        return {"keyword": "FLIBBERTIGIBBET"}

    custom_tool = make_l1_langgraph_structured_tool(handler)

    await _drive_langgraph_script(
        tool_name=tool_name,
        arguments=arguments,
        tools=tools,
        additional_tools=[custom_tool],
    )


async def _dispatch_l1_custom_pydantic_ai(
    tool_name: str,
    arguments: dict[str, Any],
    calls: list[LogKeywordInput],
    tools: ConformanceSchemaRecorder,
) -> None:
    pytest.importorskip("pydantic_ai", reason="pydantic-ai extra not installed")
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
    from band.adapters.pydantic_ai import PydanticAIAdapter

    async def handler(args: LogKeywordInput) -> dict[str, str]:
        calls.append(args)
        return {"keyword": "FLIBBERTIGIBBET"}

    custom_tool = make_l1_pydantic_ai_tool(handler)

    cursor: list[tuple[str, str, dict[str, Any] | None]] = [
        ("tool", tool_name, arguments),
        ("text", "done", None),
    ]

    async def _stream(messages: list[ModelMessage], info: AgentInfo) -> Any:
        del messages, info
        kind, name_or_text, args = cursor.pop(0) if cursor else ("text", "done", None)
        if kind == "tool":
            yield {
                0: DeltaToolCall(
                    name=name_or_text,
                    json_args=json.dumps(args or {}),
                    tool_call_id="call-l1-custom",
                )
            }
        else:
            yield name_or_text

    with tier1_sentinel_provider_env():
        adapter = PydanticAIAdapter(
            model="openai:gpt-4o-mini",
            additional_tools=[custom_tool],
        )
        await adapter.on_started("Test Agent", "A conformance test agent")
        if adapter._agent is None:
            raise AssertionError("PydanticAIAdapter did not create an agent")
        assert {tool_name, "band_send_message"} <= set(
            adapter._agent._function_toolset.tools
        )
        with adapter._agent.override(model=FunctionModel(stream_function=_stream)):
            await adapter.on_event(make_dispatch_agent_input(tools))


async def _dispatch_l1_custom_codex(
    tool_name: str,
    arguments: dict[str, Any],
    custom_tool: tuple[type[BaseModel], Any],
    tools: ConformanceSchemaRecorder,
) -> None:
    await _drive_codex_replay(
        tool_name=tool_name,
        arguments=arguments,
        tools=tools,
        additional_tools=[custom_tool],
    )
