"""Request-capture helpers for baseline conformance probes."""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable, Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Literal, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from thenvoi.core.types import AgentInput
from thenvoi.runtime.tools import iter_tool_definitions
from thenvoi.testing.fake_tools import FakeAgentTools

from tests.framework_conformance.baseline_status import SeamOwner
from tests.framework_conformance.injection_registry import (
    bindings_by_adapter,
    tier1_dependency_blocked_reason,
)

HistoryShape = Literal["discrete", "flattened", "engine_input", "metadata_only"]
CaptureFn = Callable[[AgentInput, str | None], Awaitable["CapturedRequest"]]

DEFAULT_CUSTOM_PROMPT = "Custom conformance prompt."
SENTINEL_OPENAI_API_KEY = "sk-tier1-conformance-sentinel-not-a-secret"
_PROVIDER_BASE_URL_ENV_VARS = (
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
    "OPENAI_API_HOST",
)


@contextmanager
def tier1_sentinel_provider_env() -> Generator[None, None, None]:
    """Force Tier-1 provider construction to use a non-secret sentinel key.

    The PydanticAI adapter requires an OpenAI-shaped key to construct an
    ``openai:`` model before the test overrides it with ``FunctionModel``. Tier 1
    must never preserve a developer's real provider key or base URL, because a
    broken override should fail locally rather than falling through to a live
    provider.
    """

    names = ("OPENAI_API_KEY", *_PROVIDER_BASE_URL_ENV_VARS)
    original = {name: os.environ.get(name) for name in names}
    try:
        os.environ["OPENAI_API_KEY"] = SENTINEL_OPENAI_API_KEY
        for name in _PROVIDER_BASE_URL_ENV_VARS:
            os.environ.pop(name, None)
        yield
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class RequestItemPurpose(str, Enum):
    HISTORY = "history"
    CURRENT_WORK = "current_work"
    SYSTEM_CONTEXT = "system_context"
    TOOL_SCHEMA = "tool_schema"
    UNKNOWN = "unknown"


@dataclass(frozen=True, kw_only=True)
class CapturedRequestItem:
    surface: str
    index: int
    role: str
    text: str
    purpose: RequestItemPurpose
    source_message_id: str | None = None
    message_type: str | None = None
    tool_call_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class RehydrationRequestState:
    history_message_ids: tuple[str, ...]
    current_work_message_ids: tuple[str, ...]
    completed_tool_call_ids: tuple[str, ...]
    pending_tool_call_ids: tuple[str, ...]
    source_message_counts: Mapping[str, int]


@dataclass(frozen=True, kw_only=True)
class CapturedRequest:
    adapter_id: str
    family: str
    base_instruction_surface: str | None
    system_text: str | None
    message_texts: list[str]
    message_roles: list[str]
    message_ids: list[str]
    tool_names: list[str]
    seam_owner: SeamOwner
    raw_summary: str
    history_shape: HistoryShape = "discrete"
    supports_speaker_roles: bool = True
    items: tuple[CapturedRequestItem, ...] = ()
    rehydration: RehydrationRequestState | None = None
    supports_rehydration_state: bool = False


def visible_text(captured: CapturedRequest) -> str:
    return "\n".join([captured.system_text or "", *captured.message_texts])


def token_position(captured: CapturedRequest, token: str) -> tuple[int, int]:
    for index, text in enumerate(captured.message_texts):
        offset = text.find(token)
        if offset != -1:
            return index, offset
    raise AssertionError(
        f"{token!r} not found in captured message texts: {captured.message_texts}"
    )


def assert_token_order(captured: CapturedRequest, *tokens: str) -> None:
    positions = [token_position(captured, token) for token in tokens]
    assert positions == sorted(positions)


@dataclass(frozen=True, kw_only=True)
class RequestCaptureProbe:
    adapter_id: str
    family: str
    required_module: str | None
    history_shape: HistoryShape
    capture: CaptureFn


class ConformanceSchemaRecorder(FakeAgentTools):
    """Fake tools whose schemas come from the runtime tool-definition registry.

    The read-only platform tools (get_participants / lookup_peers) have no
    observable side effect on the fake state, so the recorder logs their
    invocations into ``tool_calls`` at the typed-method boundary. The record
    captures the arguments the adapter actually passed — it is written here,
    uniformly for every adapter, never by per-adapter test code.
    """

    async def get_participants(self) -> list[dict[str, Any]]:
        self.tool_calls.append(
            {"tool_name": "thenvoi_get_participants", "arguments": {}}
        )
        return await super().get_participants()

    async def lookup_peers(self, page: int = 1, page_size: int = 50) -> dict[str, Any]:
        self.tool_calls.append(
            {
                "tool_name": "thenvoi_lookup_peers",
                "arguments": {"page": page, "page_size": page_size},
            }
        )
        return await super().lookup_peers(page=page, page_size=page_size)

    async def execute_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Record generic dispatch and route platform tools to fake side effects."""

        self.tool_calls.append({"tool_name": tool_name, "arguments": arguments})
        if tool_name == "thenvoi_send_message":
            return await self.send_message(
                content=arguments.get("content", ""),
                mentions=arguments.get("mentions"),
            )
        if tool_name == "thenvoi_add_participant":
            return await self.add_participant(
                identifier=arguments.get("identifier", ""),
                role=arguments.get("role", "member"),
            )
        if tool_name == "thenvoi_remove_participant":
            return await self.remove_participant(
                identifier=arguments.get("identifier", "")
            )
        if tool_name == "thenvoi_get_participants":
            return await FakeAgentTools.get_participants(self)
        if tool_name == "thenvoi_lookup_peers":
            return await FakeAgentTools.lookup_peers(
                self,
                page=arguments.get("page", 1),
                page_size=arguments.get("page_size", 50),
            )
        return {"status": "ok"}

    def get_tool_schemas(
        self,
        format: str,
        *,
        include_memory: bool = False,
        include_contacts: bool = True,
    ) -> list[dict[str, Any]]:
        if format not in {"openai", "anthropic"}:
            raise ValueError("format must be 'openai' or 'anthropic'")

        schemas: list[dict[str, Any]] = []
        for definition in iter_tool_definitions(
            include_memory=include_memory,
            include_contacts=include_contacts,
        ):
            schema = definition.input_model.model_json_schema()
            schema.pop("title", None)
            if format == "openai":
                schemas.append(
                    {
                        "type": "function",
                        "function": {
                            "name": definition.name,
                            "description": definition.input_model.__doc__ or "",
                            "parameters": schema,
                        },
                    }
                )
            else:
                schemas.append(
                    {
                        "name": definition.name,
                        "description": definition.input_model.__doc__ or "",
                        "input_schema": schema,
                    }
                )
        return schemas

    def get_anthropic_tool_schemas(
        self,
        *,
        include_memory: bool = False,
        include_contacts: bool = True,
    ) -> list[dict[str, Any]]:
        return self.get_tool_schemas(
            "anthropic",
            include_memory=include_memory,
            include_contacts=include_contacts,
        )

    def get_openai_tool_schemas(
        self,
        *,
        include_memory: bool = False,
        include_contacts: bool = True,
    ) -> list[dict[str, Any]]:
        return self.get_tool_schemas(
            "openai",
            include_memory=include_memory,
            include_contacts=include_contacts,
        )


def canonical_tool_names(
    *,
    include_memory: bool = False,
    include_contacts: bool = True,
) -> list[str]:
    return [
        definition.name
        for definition in iter_tool_definitions(
            include_memory=include_memory,
            include_contacts=include_contacts,
        )
    ]


def schema_tool_names(
    schemas: list[dict[str, Any]],
    *,
    format: Literal["openai", "anthropic"],
) -> list[str]:
    if format == "openai":
        return [str(schema["function"]["name"]) for schema in schemas]
    return [str(schema["name"]) for schema in schemas]


def _structured_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts = [_structured_text(item) for item in value.values()]
        return " ".join(part for part in parts if part)
    if isinstance(value, list | tuple):
        parts = [_structured_text(item) for item in value]
        return " ".join(part for part in parts if part)

    parts: list[str] = []
    for attr in (
        "content",
        "text",
        "id",
        "name",
        "tool_name",
        "tool_call_id",
        "tool_use_id",
        "input",
        "output",
        "args",
        "response",
        "function_call",
        "function_response",
        "tool_calls",
    ):
        if hasattr(value, attr):
            parts.append(_structured_text(getattr(value, attr)))
    return " ".join(part for part in parts if part) or str(value)


def _message_text(value: Any) -> str:
    if isinstance(value, tuple) and len(value) >= 2:
        return _structured_text(value[1])
    if isinstance(value, dict):
        content_text = _structured_text(value.get("content", ""))
        return content_text or _structured_text(value)
    content_text = _structured_text(getattr(value, "content", ""))
    toolish_values = [
        getattr(value, attr, None)
        for attr in ("tool_call_id", "tool_calls", "function_call", "function_response")
    ]
    if content_text and any(toolish_values):
        object_text = _structured_text(value)
        return f"{content_text} {object_text}" if object_text else content_text
    return content_text or _structured_text(value)


def _message_role(value: Any) -> str:
    if isinstance(value, tuple) and value:
        return str(value[0])
    if isinstance(value, dict):
        return str(value.get("role", ""))
    message_type = str(getattr(value, "type", ""))
    if message_type == "human":
        return "user"
    if message_type == "ai":
        return "assistant"
    return message_type


def _system_text_from_messages(messages: list[Any]) -> str | None:
    for message in messages:
        if isinstance(message, tuple) and len(message) >= 2 and message[0] == "system":
            return str(message[1])
    return None


def _source_match_tokens(content: str) -> list[str]:
    tokens = [content]
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return tokens
    if not isinstance(parsed, dict):
        return tokens
    tool_call_id = parsed.get("tool_call_id")
    if tool_call_id is not None:
        tokens.append(str(tool_call_id))
    output = parsed.get("output")
    if output is None:
        name = parsed.get("name")
        if name is not None:
            tokens.append(str(name))
    if isinstance(output, dict):
        tokens.extend(str(value) for value in output.values() if value is not None)
    elif output is not None:
        tokens.append(str(output))
    return tokens


def _source_message_records(agent_input: AgentInput) -> list[dict[str, str | None]]:
    records: list[dict[str, str | None]] = []
    for raw in agent_input.history.raw:
        metadata = raw.get("metadata")
        if not isinstance(metadata, dict) or not metadata.get("source_message_id"):
            continue
        content = str(raw.get("content", ""))
        if not content:
            continue
        records.append(
            {
                "source_message_id": str(metadata["source_message_id"]),
                "content": content,
                "message_type": str(raw.get("message_type", "text")),
                "tool_call_id": _tool_call_id_from_content(content),
            }
        )
    if agent_input.msg.content:
        records.append(
            {
                "source_message_id": agent_input.msg.id,
                "content": agent_input.msg.content,
                "message_type": agent_input.msg.message_type,
                "tool_call_id": _tool_call_id_from_content(agent_input.msg.content),
            }
        )
    return records


def _tool_call_id_from_content(content: str) -> str | None:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    value = parsed.get("tool_call_id")
    return str(value) if value is not None else None


def _observed_message_matches(
    agent_input: AgentInput,
    message_texts: list[str],
) -> list[tuple[tuple[int, int], dict[str, str | None]]]:
    observed: list[tuple[tuple[int, int], dict[str, str | None]]] = []
    used_positions: set[tuple[int, int]] = set()
    for record in _source_message_records(agent_input):
        content = str(record["content"] or "")
        match_tokens = _source_match_tokens(content)
        for index, text in enumerate(message_texts):
            offset = text.find(content)
            if offset == -1:
                offsets = [text.find(token) for token in match_tokens[1:] if token]
                if offsets and all(candidate != -1 for candidate in offsets):
                    offset = min(offsets)
            position = (index, offset)
            if offset != -1 and position not in used_positions:
                observed.append((position, record))
                used_positions.add(position)
                break
    return sorted(observed, key=lambda item: item[0])


def _observed_message_ids(
    agent_input: AgentInput,
    message_texts: list[str],
) -> list[str]:
    return [
        str(record["source_message_id"])
        for _position, record in _observed_message_matches(agent_input, message_texts)
        if record.get("source_message_id")
    ]


def _rehydration_items(
    captured: CapturedRequest,
    agent_input: AgentInput,
) -> tuple[CapturedRequestItem, ...]:
    items: list[CapturedRequestItem] = []
    for index, text in enumerate(captured.message_texts):
        if not text:
            continue
        role = (
            captured.message_roles[index] if index < len(captured.message_roles) else ""
        )
        items.append(
            CapturedRequestItem(
                surface=captured.family,
                index=index,
                role=role,
                text=text,
                purpose=RequestItemPurpose.UNKNOWN,
            )
        )

    for (index, offset), record in _observed_message_matches(
        agent_input, captured.message_texts
    ):
        source_id = str(record["source_message_id"])
        message_type = str(record.get("message_type") or "text")
        tool_call_id = record.get("tool_call_id")
        role = (
            captured.message_roles[index] if index < len(captured.message_roles) else ""
        )
        purpose = (
            RequestItemPurpose.CURRENT_WORK
            if source_id == agent_input.msg.id
            else RequestItemPurpose.HISTORY
        )
        text = captured.message_texts[index]
        items.append(
            CapturedRequestItem(
                surface=captured.family,
                index=index,
                role=role,
                text=text[offset:] if offset >= 0 else text,
                purpose=purpose,
                source_message_id=source_id,
                message_type=message_type,
                tool_call_id=str(tool_call_id) if tool_call_id else None,
            )
        )
    return tuple(
        sorted(items, key=lambda item: (item.index, item.source_message_id or ""))
    )


def _rehydration_state(
    items: tuple[CapturedRequestItem, ...],
) -> RehydrationRequestState:
    history_ids = tuple(
        item.source_message_id
        for item in items
        if item.purpose is RequestItemPurpose.HISTORY and item.source_message_id
    )
    current_ids = tuple(
        item.source_message_id
        for item in items
        if item.purpose is RequestItemPurpose.CURRENT_WORK and item.source_message_id
    )
    source_counts: dict[str, int] = {}
    tool_call_ids: set[str] = set()
    tool_result_ids: set[str] = set()
    current_tool_call_ids: set[str] = set()
    for item in items:
        if item.source_message_id:
            source_counts[item.source_message_id] = (
                source_counts.get(item.source_message_id, 0) + 1
            )
        if item.tool_call_id and item.message_type == "tool_call":
            tool_call_ids.add(item.tool_call_id)
            if item.purpose is RequestItemPurpose.CURRENT_WORK:
                current_tool_call_ids.add(item.tool_call_id)
        if item.tool_call_id and item.message_type == "tool_result":
            tool_result_ids.add(item.tool_call_id)

    completed = tuple(sorted(tool_call_ids & tool_result_ids))
    pending = tuple(sorted(current_tool_call_ids - tool_result_ids))
    return RehydrationRequestState(
        history_message_ids=history_ids,
        current_work_message_ids=current_ids,
        completed_tool_call_ids=completed,
        pending_tool_call_ids=pending,
        source_message_counts=source_counts,
    )


def _with_rehydration_state(
    captured: CapturedRequest,
    agent_input: AgentInput,
) -> CapturedRequest:
    items = _rehydration_items(captured, agent_input)
    state = _rehydration_state(items)
    supports = bool(state.current_work_message_ids)
    return replace(
        captured,
        items=items,
        rehydration=state,
        supports_rehydration_state=supports,
    )


def _part_texts(parts: Any) -> list[str]:
    texts: list[str] = []
    for part in parts or []:
        text = _structured_text(part)
        if text:
            texts.append(text)
    return texts


def _normalize_tool_name(name: str) -> str:
    prefix = "mcp__thenvoi__"
    return name.removeprefix(prefix)


async def capture_anthropic_request(
    agent_input: AgentInput,
    custom_prompt: str | None = DEFAULT_CUSTOM_PROMPT,
) -> CapturedRequest:
    from thenvoi.adapters.anthropic import AnthropicAdapter

    class _CapturingMessagesClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def create(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            return type("_Response", (), {"stop_reason": "end_turn", "content": []})()

    class _CapturingAnthropicClient:
        def __init__(self) -> None:
            self.messages = _CapturingMessagesClient()

    adapter = AnthropicAdapter(
        provider_key="test-provider-key",
        prompt=custom_prompt,
    )
    client = _CapturingAnthropicClient()
    adapter.client = cast(Any, client)
    await adapter.on_started("Test Agent", "A conformance test agent")
    await adapter.on_event(agent_input)

    call = client.messages.calls[0]
    captured_messages = [dict(message) for message in call["messages"]]
    captured_tools = [dict(cast(dict[str, Any], tool)) for tool in call["tools"]]
    system_text = str(call.get("system") or "")
    tool_names = schema_tool_names(captured_tools, format="anthropic")
    message_texts = [_message_text(message) for message in captured_messages]
    return CapturedRequest(
        adapter_id="anthropic",
        family="anthropic_messages",
        base_instruction_surface="system",
        system_text=system_text,
        message_texts=message_texts,
        message_roles=[_message_role(message) for message in captured_messages],
        message_ids=_observed_message_ids(agent_input, message_texts),
        tool_names=tool_names,
        seam_owner=SeamOwner.ADAPTER_PAYLOAD,
        raw_summary=(
            f"system={bool(system_text)} "
            f"messages={len(captured_messages)} tools={len(tool_names)}"
        ),
    )


async def capture_langgraph_request(
    agent_input: AgentInput,
    custom_prompt: str | None = DEFAULT_CUSTOM_PROMPT,
) -> CapturedRequest:
    from thenvoi.adapters.langgraph import LangGraphAdapter

    class _CapturingGraph:
        graph_input: dict[str, Any]

        async def astream_events(self, graph_input: dict[str, Any], **_kwargs: Any):
            self.graph_input = graph_input
            if False:
                yield {}

    graph = _CapturingGraph()
    captured_tool_names: list[str] = []

    def graph_factory(tools: list[Any]) -> _CapturingGraph:
        captured_tool_names[:] = [str(getattr(tool, "name", "")) for tool in tools]
        return graph

    adapter = LangGraphAdapter(
        graph_factory=graph_factory,
        custom_section=custom_prompt,
        inject_system_prompt=True,
    )
    await adapter.on_started("Test Agent", "A conformance test agent")
    await adapter.on_event(agent_input)

    messages = list(graph.graph_input["messages"])
    message_texts = [_message_text(message) for message in messages]
    return CapturedRequest(
        adapter_id="langgraph",
        family="langchain_messages",
        base_instruction_surface="system_message",
        system_text=_system_text_from_messages(messages),
        message_texts=message_texts,
        message_roles=[_message_role(message) for message in messages],
        message_ids=_observed_message_ids(agent_input, message_texts),
        tool_names=[name for name in captured_tool_names if name],
        seam_owner=SeamOwner.ADAPTER_PAYLOAD,
        raw_summary=f"messages={len(messages)} tools={len(captured_tool_names)}",
    )


async def capture_gemini_request(
    agent_input: AgentInput,
    custom_prompt: str | None = DEFAULT_CUSTOM_PROMPT,
) -> CapturedRequest:
    pytest.importorskip("google.genai", reason="gemini extra not installed")
    from google.genai import types
    from thenvoi.adapters.gemini import GeminiAdapter

    class _CapturingGeminiModels:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def generate_content(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            content = types.Content(role="model", parts=[types.Part(text="done")])
            candidate = types.Candidate(content=content)
            return types.GenerateContentResponse(candidates=[candidate])

    class _CapturingGeminiAio:
        def __init__(self) -> None:
            self.models = _CapturingGeminiModels()

    class _CapturingGeminiClient:
        def __init__(self) -> None:
            self.aio = _CapturingGeminiAio()

    captured_client = _CapturingGeminiClient()

    class _CapturingGeminiAdapter(GeminiAdapter):
        def _ensure_client(self) -> Any:
            return captured_client

    adapter = _CapturingGeminiAdapter(
        model="gemini-2.5-flash",
        provider_key="unused-in-capture",
        prompt=custom_prompt,
    )
    await adapter.on_started("Test Agent", "A conformance test agent")
    await adapter.on_event(agent_input)

    call = captured_client.aio.models.calls[0]
    captured_contents = list(call["contents"])
    config = call["config"]
    message_texts = [
        "\n".join(_part_texts(getattr(content, "parts", [])))
        for content in captured_contents
    ]
    message_roles = [str(getattr(content, "role", "")) for content in captured_contents]
    tool_names: list[str] = []
    for tool in getattr(config, "tools", None) or []:
        for declaration in getattr(tool, "function_declarations", None) or []:
            name = getattr(declaration, "name", None)
            if name:
                tool_names.append(str(name))
    system_instruction = getattr(config, "system_instruction", None)
    system_text = _structured_text(system_instruction)

    return CapturedRequest(
        adapter_id="gemini",
        family="gemini_contents",
        base_instruction_surface="system_instruction",
        system_text=system_text,
        message_texts=message_texts,
        message_roles=message_roles,
        message_ids=_observed_message_ids(agent_input, message_texts),
        tool_names=tool_names,
        seam_owner=SeamOwner.ADAPTER_PAYLOAD,
        raw_summary=f"contents={len(message_texts)} tools={len(tool_names)}",
    )


async def capture_pydantic_ai_request(
    agent_input: AgentInput,
    custom_prompt: str | None = DEFAULT_CUSTOM_PROMPT,
) -> CapturedRequest:
    pytest.importorskip("pydantic_ai", reason="pydantic-ai extra not installed")
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import AgentInfo, FunctionModel
    from thenvoi.adapters.pydantic_ai import PydanticAIAdapter

    captured_messages: list[ModelMessage] = []
    captured_tool_names: list[str] = []

    async def _stream(messages: list[ModelMessage], info: AgentInfo) -> Any:
        captured_messages[:] = list(messages)
        captured_tool_names[:] = [tool.name for tool in info.function_tools]
        yield "done"

    with tier1_sentinel_provider_env():
        adapter = PydanticAIAdapter(
            model="openai:gpt-4o-mini",
            custom_section=custom_prompt,
        )
        await adapter.on_started("Test Agent", "A conformance test agent")
        if adapter._agent is None:
            raise AssertionError("PydanticAIAdapter did not create an agent")
        with adapter._agent.override(model=FunctionModel(stream_function=_stream)):
            await adapter.on_event(agent_input)

    message_texts: list[str] = []
    message_roles: list[str] = []
    # system_text must come from the captured model call, not adapter
    # internals — otherwise the row cannot fail when the prompt stops
    # reaching the model.
    system_text: str | None = None
    for message in captured_messages:
        kind = str(getattr(message, "kind", ""))
        role = "assistant" if kind == "response" else "user"
        # Instructions arrive on the request message itself; this is the
        # model-visible system surface for instructions-based agents.
        instructions = getattr(message, "instructions", None)
        if instructions:
            system_text = str(instructions)
        parts = getattr(message, "parts", [])
        texts: list[str] = []
        for part in parts:
            if getattr(part, "part_kind", "") == "system-prompt":
                prompt = getattr(part, "content", "")
                if prompt:
                    system_text = str(prompt)
                continue
            content = getattr(part, "content", None)
            text = getattr(part, "text", None)
            if content:
                structured = (
                    _structured_text(part)
                    if any(
                        hasattr(part, attr)
                        for attr in ("tool_call_id", "tool_name", "tool_calls")
                    )
                    else ""
                )
                texts.append(f"{content} {structured}" if structured else str(content))
            elif text:
                texts.append(str(text))
            else:
                structured = _structured_text(part)
                if structured:
                    texts.append(structured)
        body = "\n".join(texts)
        if body:
            message_texts.append(body)
            message_roles.append(role)

    return CapturedRequest(
        adapter_id="pydantic_ai",
        family="pydantic_ai_agent_input",
        base_instruction_surface="system_prompt",
        system_text=system_text,
        message_texts=message_texts,
        message_roles=message_roles,
        message_ids=_observed_message_ids(agent_input, message_texts),
        tool_names=captured_tool_names,
        seam_owner=SeamOwner.ADAPTER_PAYLOAD,
        raw_summary=f"messages={len(message_texts)} tools={len(captured_tool_names)}",
    )


async def capture_google_adk_request(
    agent_input: AgentInput,
    custom_prompt: str | None = DEFAULT_CUSTOM_PROMPT,
) -> CapturedRequest:
    pytest.importorskip("google.adk", reason="google-adk extra not installed")
    from thenvoi.adapters.google_adk import GoogleADKAdapter

    class _FinalEvent:
        def is_final_response(self) -> bool:
            return True

    class _SessionService:
        async def create_session(self, **_kwargs: Any) -> None:
            return None

    class _CapturingRunner:
        def __init__(self, tool_names: list[str]) -> None:
            self.session_service = _SessionService()
            self.tool_names = tool_names
            self.new_message: Any = None

        async def run_async(self, **kwargs: Any) -> Any:
            self.new_message = kwargs["new_message"]
            yield _FinalEvent()

        async def close(self) -> None:
            return None

    from thenvoi.adapters import google_adk as google_adk_module

    real_adk_agent, _real_runner, real_base_tool, real_types = (
        google_adk_module._require_adk()
    )
    del real_adk_agent, _real_runner

    captured_agents: list[Any] = []
    captured_runners: list[_CapturingRunner] = []

    class _CapturingADKAgent:
        def __init__(self, **kwargs: Any) -> None:
            self.name = kwargs.get("name")
            self.model = kwargs.get("model")
            self.instruction = kwargs.get("instruction")
            self.tools = list(kwargs.get("tools") or [])
            captured_agents.append(self)

    class _CapturingInMemoryRunner:
        def __init__(self, *, agent: Any, app_name: str) -> None:
            del app_name
            tool_names = [str(getattr(tool, "name", "")) for tool in agent.tools]
            self._runner = _CapturingRunner(tool_names)
            captured_runners.append(self._runner)

        @property
        def session_service(self) -> Any:
            return self._runner.session_service

        async def run_async(self, **kwargs: Any) -> Any:
            async for event in self._runner.run_async(**kwargs):
                yield event

        async def close(self) -> None:
            await self._runner.close()

    class _CapturingGoogleADKAdapter(GoogleADKAdapter):
        def _extract_event_text(self, event: Any) -> str:
            return "done"

    with patch.object(
        google_adk_module,
        "_require_adk",
        return_value=(
            _CapturingADKAgent,
            _CapturingInMemoryRunner,
            real_base_tool,
            real_types,
        ),
    ):
        adapter = _CapturingGoogleADKAdapter(
            model="gemini-2.5-flash",
            custom_section=custom_prompt,
        )
        await adapter.on_started("Test Agent", "A conformance test agent")
        await adapter.on_event(agent_input)

    captured_runner = captured_runners[0]
    parts = getattr(captured_runner.new_message, "parts", [])
    text = "\n".join(_part_texts(parts))
    message_texts = [text]
    system_text = str(getattr(captured_agents[0], "instruction", "") or "")
    return CapturedRequest(
        adapter_id="google_adk",
        family="adk_content_request",
        base_instruction_surface="instruction",
        system_text=system_text,
        message_texts=message_texts,
        message_roles=["user"],
        message_ids=_observed_message_ids(agent_input, message_texts),
        tool_names=[name for name in captured_runner.tool_names if name],
        seam_owner=SeamOwner.ADAPTER_INPUT,
        raw_summary=f"flattened_chars={len(text)} tools={len(captured_runner.tool_names)}",
        history_shape="flattened",
        supports_speaker_roles=False,
    )


async def capture_codex_request(
    agent_input: AgentInput,
    custom_prompt: str | None = DEFAULT_CUSTOM_PROMPT,
) -> CapturedRequest:
    from thenvoi.adapters.codex import CodexAdapter, CodexAdapterConfig, _TurnResult

    class _RecordingClient:
        def __init__(self) -> None:
            self.requests: list[tuple[str, dict[str, Any]]] = []

        async def connect(self) -> None:
            return None

        async def close(self) -> None:
            return None

        async def initialize(self, **_kwargs: Any) -> dict[str, Any]:
            return {}

        async def request(
            self,
            method: str,
            params: dict[str, Any] | None = None,
            *,
            retry_on_overload: bool = True,
        ) -> dict[str, Any]:
            del retry_on_overload
            self.requests.append((method, dict(params or {})))
            if method == "thread/start":
                return {"thread": {"id": "codex-thread-1"}}
            if method == "turn/start":
                return {"turn": {"id": "codex-turn-1"}}
            return {}

        async def recv_event(self, *, timeout_s: float | None = None) -> Any:
            del timeout_s
            raise AssertionError("Codex capture overrides turn processing")

    class _CapturingCodexAdapter(CodexAdapter):
        async def _process_turn_events(self, **_kwargs: Any) -> _TurnResult:
            return _TurnResult(
                final_text="",
                turn_status="completed",
                turn_error="",
                saw_send_message_tool=False,
            )

    client = _RecordingClient()
    adapter = _CapturingCodexAdapter(
        config=CodexAdapterConfig(
            transport="stdio",
            model="gpt-5.4",
            custom_section=custom_prompt or "",
            enable_task_events=False,
        ),
        client_factory=lambda _config: cast(Any, client),
    )
    await adapter.on_started("Test Agent", "A conformance test agent")
    await adapter.on_event(agent_input)

    thread_start = next(
        params for method, params in client.requests if method == "thread/start"
    )
    turn_start = next(
        params for method, params in client.requests if method == "turn/start"
    )
    texts = [str(item.get("text", "")) for item in turn_start.get("input", [])]
    system_text = next(
        (
            text.removeprefix("[System Instructions]\n")
            for text in texts
            if text.startswith("[System Instructions]\n")
        ),
        "",
    )
    tool_names = [
        str(tool.get("name", "")) for tool in thread_start.get("dynamicTools", [])
    ]
    return CapturedRequest(
        adapter_id="codex",
        family="codex_prompt",
        base_instruction_surface="session_prompt_prefix",
        system_text=system_text,
        message_texts=texts,
        message_roles=["user" for _ in texts],
        message_ids=_observed_message_ids(agent_input, texts),
        tool_names=[name for name in tool_names if name],
        seam_owner=SeamOwner.ADAPTER_PAYLOAD,
        raw_summary=f"turn_items={len(texts)} tools={len(tool_names)}",
        history_shape="flattened",
        supports_speaker_roles=False,
    )


async def capture_claude_sdk_request(
    agent_input: AgentInput,
    custom_prompt: str | None = DEFAULT_CUSTOM_PROMPT,
) -> CapturedRequest:
    pytest.importorskip(
        "claude_agent_sdk", reason="claude-agent-sdk extra not installed"
    )
    from thenvoi.adapters.claude_sdk import ClaudeSDKAdapter

    mock_client = MagicMock()
    mock_client.query = AsyncMock()
    mock_manager = AsyncMock()
    mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)

    adapter = ClaudeSDKAdapter(custom_section=custom_prompt)
    # The real MCP backend is built in-process (no network); letting it run
    # means the captured allowed_tools reflect the adapter's actual tool
    # exposure instead of a fixture round-trip.
    with (
        patch(
            "thenvoi.adapters.claude_sdk.ClaudeSessionManager",
            return_value=mock_manager,
        ) as manager_class,
        patch.object(adapter, "_process_response", new=AsyncMock()),
    ):
        await adapter.on_started("Test Agent", "A conformance test agent")
        await adapter.on_event(agent_input)

    sdk_options = manager_class.call_args[0][0]
    query_text = str(mock_client.query.await_args.args[0])
    message_texts = [query_text]
    system_prompt = getattr(sdk_options, "system_prompt", None)
    system_text = str(system_prompt)
    allowed_tools = [
        _normalize_tool_name(str(name))
        for name in getattr(sdk_options, "allowed_tools", [])
    ]
    return CapturedRequest(
        adapter_id="claude_sdk",
        family="claude_sdk_prompt",
        base_instruction_surface="system_prompt_append",
        system_text=system_text,
        message_texts=message_texts,
        message_roles=["user"],
        message_ids=_observed_message_ids(agent_input, message_texts),
        tool_names=allowed_tools,
        seam_owner=SeamOwner.ADAPTER_PAYLOAD,
        raw_summary=f"query_chars={len(query_text)} tools={len(allowed_tools)}",
        history_shape="flattened",
        supports_speaker_roles=False,
    )


async def capture_opencode_request(
    agent_input: AgentInput,
    custom_prompt: str | None = DEFAULT_CUSTOM_PROMPT,
) -> CapturedRequest:
    from tests.adapters.test_opencode_adapter import (
        FakeMCPBackend,
        FakeOpencodeClient,
        event_message_updated,
        event_session_idle,
        event_text_part,
    )
    from thenvoi.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig

    class _CapturingOpencodeAdapter(OpencodeAdapter):
        async def _watch_turn_completion(self, *_args: Any, **_kwargs: Any) -> None:
            return None

    fake_backend = FakeMCPBackend()
    captured_backend_tool_names: list[str] = []

    async def _backend_factory(**kwargs: Any) -> FakeMCPBackend:
        definitions = kwargs.get("tool_definitions") or []
        captured_backend_tool_names[:] = [
            str(getattr(definition, "name", "")) for definition in definitions
        ]
        fake_backend.allowed_tools = [
            f"mcp__thenvoi__{name}" for name in captured_backend_tool_names if name
        ]
        return fake_backend

    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[
            [
                event_message_updated("sess-1", "msg-1"),
                event_text_part("sess-1", "msg-1", "done"),
                event_session_idle("sess-1"),
            ]
        ]
    )
    adapter = _CapturingOpencodeAdapter(
        config=OpencodeAdapterConfig(
            custom_section=custom_prompt,
            enable_task_events=False,
        ),
        client_factory=lambda _config: fake_client,
    )
    with patch(
        "thenvoi.adapters.opencode.create_thenvoi_mcp_backend",
        AsyncMock(side_effect=_backend_factory),
    ):
        await adapter.on_started("Test Agent", "A conformance test agent")
        await adapter.on_event(agent_input)

    call = fake_client.prompt_calls[0]
    texts = [str(part.get("text", "")) for part in call["parts"]]
    await adapter.on_cleanup(agent_input.room_id)
    return CapturedRequest(
        adapter_id="opencode",
        family="opencode_prompt_call",
        base_instruction_surface="system_prompt",
        system_text=call["system"],
        message_texts=texts,
        message_roles=["user" for _ in texts],
        message_ids=_observed_message_ids(agent_input, texts),
        tool_names=captured_backend_tool_names,
        seam_owner=SeamOwner.ADAPTER_PAYLOAD,
        raw_summary=f"parts={len(texts)} tools={len(fake_backend.allowed_tools)}",
        history_shape="flattened",
        supports_speaker_roles=False,
    )


async def capture_letta_request(
    agent_input: AgentInput,
    custom_prompt: str | None = DEFAULT_CUSTOM_PROMPT,
) -> CapturedRequest:
    from thenvoi.adapters.letta import LettaAdapter, LettaAdapterConfig

    canonical_names = canonical_tool_names()
    tool_objects = [
        type("_LettaTool", (), {"id": f"tool-{index}", "name": name})()
        for index, name in enumerate(canonical_names)
    ]
    attached_tool_ids: list[str] = []
    created_agent_kwargs: dict[str, Any] = {}
    sent_messages: list[str] = []

    class _McpServers:
        async def list(self) -> list[Any]:
            return []

        async def create(self, **_kwargs: Any) -> Any:
            return type("_Server", (), {"id": "mcp-server-1"})()

        class tools:
            @staticmethod
            async def list(**_kwargs: Any) -> list[Any]:
                return tool_objects

    class _AgentTools:
        async def attach(self, *, agent_id: str, tool_id: str) -> None:
            del agent_id
            attached_tool_ids.append(tool_id)

    class _Messages:
        async def create(self, *, agent_id: str, messages: list[dict[str, Any]]) -> Any:
            del agent_id
            sent_messages.extend(
                str(message.get("content", "")) for message in messages
            )
            return type("_Response", (), {"messages": []})()

    class _Agents:
        def __init__(self) -> None:
            self.tools = _AgentTools()
            self.messages = _Messages()

        async def create(self, **kwargs: Any) -> Any:
            created_agent_kwargs.update(kwargs)
            return type("_Agent", (), {"id": "letta-agent-1"})()

    class _AsyncLetta:
        def __init__(self, **_kwargs: Any) -> None:
            self.mcp_servers = _McpServers()
            self.agents = _Agents()

    with patch.dict(
        "sys.modules",
        {"letta_client": type("_LettaModule", (), {"AsyncLetta": _AsyncLetta})()},
    ):
        adapter = LettaAdapter(
            config=LettaAdapterConfig(
                custom_section=custom_prompt or "",
                enable_task_events=False,
            )
        )
        await adapter.on_started("Test Agent", "A conformance test agent")
        await adapter.on_event(agent_input)

    persona = ""
    for block in created_agent_kwargs.get("memory_blocks", []):
        if isinstance(block, dict) and block.get("label") == "persona":
            persona = str(block.get("value", ""))
            break
    attached_names = [
        tool.name
        for tool in tool_objects
        if tool.id in set(attached_tool_ids) and getattr(tool, "name", None)
    ]
    message_texts = sent_messages
    return CapturedRequest(
        adapter_id="letta",
        family="letta_agent_prompt",
        base_instruction_surface="agent_instructions",
        system_text=persona,
        message_texts=message_texts,
        message_roles=["user" for _ in message_texts],
        message_ids=_observed_message_ids(agent_input, message_texts),
        tool_names=[str(name) for name in attached_names],
        seam_owner=SeamOwner.ADAPTER_INPUT,
        raw_summary=f"sent_messages={len(sent_messages)} tools={len(attached_names)}",
        history_shape="engine_input",
        supports_speaker_roles=False,
    )


async def capture_crewai_request(
    agent_input: AgentInput,
    custom_prompt: str | None = DEFAULT_CUSTOM_PROMPT,
) -> CapturedRequest:
    pytest.importorskip("crewai", reason="crewai extra not installed")
    from thenvoi.adapters.crewai import CrewAIAdapter

    class _Result:
        raw = ""

    class _RecorderAgent:
        def __init__(self, *, backstory: str, tool_names: list[str]) -> None:
            self.backstory = backstory
            self.tool_names = tool_names
            self.messages: list[dict[str, Any]] = []

        async def kickoff_async(self, messages: list[dict[str, Any]]) -> _Result:
            self.messages = list(messages)
            return _Result()

    adapter = CrewAIAdapter(custom_section=custom_prompt)
    await adapter.on_started("Test Agent", "A conformance test agent")
    real_agent = adapter._crewai_agent
    recorder = _RecorderAgent(
        backstory=str(getattr(real_agent, "backstory", "")),
        tool_names=[
            str(getattr(tool, "name", "")) for tool in getattr(real_agent, "tools", [])
        ],
    )
    adapter._crewai_agent = recorder
    await adapter.on_event(agent_input)

    message_texts = [str(message.get("content", "")) for message in recorder.messages]
    return CapturedRequest(
        adapter_id="crewai",
        family="crewai_task_prompt",
        base_instruction_surface="backstory",
        system_text=recorder.backstory,
        message_texts=message_texts,
        message_roles=[str(message.get("role", "")) for message in recorder.messages],
        message_ids=_observed_message_ids(agent_input, message_texts),
        tool_names=[name for name in recorder.tool_names if name],
        seam_owner=SeamOwner.ADAPTER_PAYLOAD,
        raw_summary=f"messages={len(recorder.messages)} tools={len(recorder.tool_names)}",
        history_shape="flattened",
        supports_speaker_roles=False,
    )


async def capture_parlant_request(
    agent_input: AgentInput,
    custom_prompt: str | None = DEFAULT_CUSTOM_PROMPT,
) -> CapturedRequest:
    pytest.importorskip("parlant", reason="parlant extra not installed")
    from parlant.core.application import Application  # type: ignore[missing-import]
    from thenvoi.adapters.parlant import ParlantAdapter

    captured_engine_messages: list[str] = []

    async def _create_customer_message(**kwargs: Any) -> Any:
        captured_engine_messages.append(str(kwargs.get("message", "")))
        return MagicMock(offset=len(captured_engine_messages))

    async def _create_event(**kwargs: Any) -> Any:
        data = kwargs.get("data")
        if isinstance(data, dict) and data.get("message"):
            captured_engine_messages.append(str(data["message"]))
        return MagicMock(offset=len(captured_engine_messages))

    mock_app = MagicMock()
    mock_app.sessions.create = AsyncMock(return_value=MagicMock(id="session-123"))
    mock_app.sessions.create_customer_message = AsyncMock(
        side_effect=_create_customer_message
    )
    mock_app.sessions.create_event = AsyncMock(side_effect=_create_event)
    mock_app.sessions.wait_for_update = AsyncMock(return_value=True)
    mock_app.sessions.wait_for_more_events = AsyncMock(return_value=True)
    mock_app.sessions.find_events = AsyncMock(return_value=[])

    server = MagicMock()
    server.container = {Application: mock_app}
    server.find_customer = AsyncMock(return_value=None)
    server.create_customer = AsyncMock(return_value=MagicMock(id="customer-123"))
    parlant_agent = MagicMock()
    parlant_agent.id = "parlant-agent-123"
    parlant_agent.create_guideline = AsyncMock(
        return_value=MagicMock(id="guideline-123")
    )

    class _CapturingParlantAdapter(ParlantAdapter):
        async def _process_agent_response(self, **_kwargs: Any) -> None:
            return None

    adapter = _CapturingParlantAdapter(
        server=server,
        parlant_agent=parlant_agent,
        custom_section=custom_prompt,
    )
    await adapter.on_started("Test Agent", "A conformance test agent")
    await adapter.on_event(agent_input)

    guideline_kwargs = parlant_agent.create_guideline.await_args.kwargs
    tool_names = [
        str(getattr(getattr(entry, "tool", entry), "name", ""))
        for entry in guideline_kwargs.get("tools", [])
    ]
    message_texts = captured_engine_messages
    return CapturedRequest(
        adapter_id="parlant",
        family="parlant_guidelines",
        base_instruction_surface="guideline_description",
        system_text=str(guideline_kwargs.get("description", "")),
        message_texts=message_texts,
        message_roles=["user" for _ in message_texts],
        message_ids=_observed_message_ids(agent_input, message_texts),
        tool_names=[name for name in tool_names if name],
        seam_owner=SeamOwner.ADAPTER_INPUT,
        raw_summary=f"customer_messages={len(message_texts)} tools={len(tool_names)}",
        history_shape="engine_input",
        supports_speaker_roles=False,
    )


REQUEST_CAPTURE_PROBES: dict[str, RequestCaptureProbe] = {
    "anthropic": RequestCaptureProbe(
        adapter_id="anthropic",
        family="anthropic_messages",
        required_module="anthropic",
        history_shape="discrete",
        capture=capture_anthropic_request,
    ),
    "langgraph": RequestCaptureProbe(
        adapter_id="langgraph",
        family="langchain_messages",
        required_module="langgraph",
        history_shape="discrete",
        capture=capture_langgraph_request,
    ),
    "gemini": RequestCaptureProbe(
        adapter_id="gemini",
        family="gemini_contents",
        required_module="google.genai",
        history_shape="discrete",
        capture=capture_gemini_request,
    ),
    "pydantic_ai": RequestCaptureProbe(
        adapter_id="pydantic_ai",
        family="pydantic_ai_agent_input",
        required_module="pydantic_ai",
        history_shape="discrete",
        capture=capture_pydantic_ai_request,
    ),
    "google_adk": RequestCaptureProbe(
        adapter_id="google_adk",
        family="adk_content_request",
        required_module="google.adk",
        history_shape="flattened",
        capture=capture_google_adk_request,
    ),
    "codex": RequestCaptureProbe(
        adapter_id="codex",
        family="codex_prompt",
        required_module=None,
        history_shape="flattened",
        capture=capture_codex_request,
    ),
    "claude_sdk": RequestCaptureProbe(
        adapter_id="claude_sdk",
        family="claude_sdk_prompt",
        required_module="claude_agent_sdk",
        history_shape="flattened",
        capture=capture_claude_sdk_request,
    ),
    "opencode": RequestCaptureProbe(
        adapter_id="opencode",
        family="opencode_prompt_call",
        required_module=None,
        history_shape="flattened",
        capture=capture_opencode_request,
    ),
    "letta": RequestCaptureProbe(
        adapter_id="letta",
        family="letta_agent_prompt",
        required_module=None,
        history_shape="engine_input",
        capture=capture_letta_request,
    ),
    "crewai": RequestCaptureProbe(
        adapter_id="crewai",
        family="crewai_task_prompt",
        required_module="crewai",
        history_shape="flattened",
        capture=capture_crewai_request,
    ),
    "parlant": RequestCaptureProbe(
        adapter_id="parlant",
        family="parlant_guidelines",
        required_module="parlant",
        history_shape="engine_input",
        capture=capture_parlant_request,
    ),
}

REQUEST_CAPTURE_ADAPTER_IDS = tuple(sorted(REQUEST_CAPTURE_PROBES))


async def capture_request(
    adapter_id: str,
    agent_input: AgentInput,
    *,
    custom_prompt: str | None = DEFAULT_CUSTOM_PROMPT,
) -> CapturedRequest:
    probe = REQUEST_CAPTURE_PROBES[adapter_id]
    binding = bindings_by_adapter().get(adapter_id)
    if binding is not None:
        blocked_reason = tier1_dependency_blocked_reason(binding)
        if blocked_reason is not None:
            raise AssertionError(blocked_reason)
    if probe.required_module:
        pytest.importorskip(probe.required_module)
    captured = await probe.capture(agent_input, custom_prompt)
    if captured.family != probe.family:
        raise AssertionError(
            f"{adapter_id} probe returned family {captured.family!r}, "
            f"expected {probe.family!r}"
        )
    if captured.history_shape != probe.history_shape:
        raise AssertionError(
            f"{adapter_id} probe returned history_shape {captured.history_shape!r}, "
            f"expected {probe.history_shape!r}"
        )
    return _with_rehydration_state(captured, agent_input)
