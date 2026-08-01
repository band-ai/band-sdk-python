"""Anthropic ``ModelProvider`` — client ownership + request/response translation."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any, Unpack, cast

from anthropic import AsyncAnthropic
from anthropic.types import (
    Message,
    MessageParam,
    TextBlock,
    ToolParam,
    ToolUseBlock,
)

from band.core.contracts.model import (
    ModelMessage,
    ModelMessageRole,
    ModelRequest,
    ModelResponse,
    ModelSamplingOptions,
    ModelToolCall,
)
from band.core.turn.history import AnthropicHistoryPolicy
from band.core.options import (
    RAW_OPTIONS_RESERVED,
    UNSET,
    _Unset,
    ProviderOptionResolver,
    resolve_sampling,
)
from band.core.protocols import ModelContext
from band.core.tools import ToolSpec, tool_spec_to_anthropic_schema
from band.core.types import TurnUsage
from band.providers.types import (
    ANTHROPIC_PROVIDER_OPTION_KEYS,
    ANTHROPIC_RAW_OPTIONS_RESERVED,
    AnthropicProviderOptions,
)
from band.runtime.tools import ToolDefinition

logger = logging.getLogger(__name__)

# Anthropic requires max_tokens on every messages.create call.
_DEFAULT_MAX_OUTPUT_TOKENS = 4096


class AnthropicProvider:
    """Pure Anthropic request/response translation; owns the SDK client."""

    def __init__(
        self,
        model: str = "claude-sonnet-4-5-20250929",
        *,
        api_key: str | None = None,
        temperature: float | None | _Unset = UNSET,
        max_output_tokens: int | None | _Unset = UNSET,
        raw_options: Mapping[str, Any] | None = None,
        **provider_options: Unpack[AnthropicProviderOptions],
    ) -> None:
        self.model = model
        self.client = AsyncAnthropic(api_key=api_key)

        resolved_max = (
            _DEFAULT_MAX_OUTPUT_TOKENS
            if max_output_tokens is UNSET
            else max_output_tokens
        )
        if resolved_max is None:
            resolved_max = _DEFAULT_MAX_OUTPUT_TOKENS

        self._instance_sampling = ModelSamplingOptions(
            temperature=None if temperature is UNSET else temperature,  # type: ignore[arg-type]
            max_output_tokens=resolved_max,  # type: ignore[arg-type]
        )
        self._options = ProviderOptionResolver(
            aliases={"max_tokens": "max_output_tokens"},
            reserved_keys=RAW_OPTIONS_RESERVED | ANTHROPIC_RAW_OPTIONS_RESERVED,
        )
        self._options.bind(
            named={
                "temperature": temperature,
                "max_output_tokens": max_output_tokens,
            },
            provider_options=provider_options,
            allowed=ANTHROPIC_PROVIDER_OPTION_KEYS,
            raw_options=raw_options,
        )

    @property
    def sampling(self) -> ModelSamplingOptions:
        return self._instance_sampling

    def default_history_policy(self) -> AnthropicHistoryPolicy:
        return AnthropicHistoryPolicy()

    async def aclose(self) -> None:
        close = getattr(self.client, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result

    async def complete(
        self, request: ModelRequest, *, context: ModelContext
    ) -> ModelResponse:
        context.cancellation.throw_if_cancelled()
        sampling = resolve_sampling(
            instance=self._instance_sampling,
            request=request.sampling,
        )
        tools = _to_anthropic_tools(request.tools)
        messages = _to_anthropic_messages(request.messages)

        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": sampling.max_output_tokens or _DEFAULT_MAX_OUTPUT_TOKENS,
            "messages": messages,
        }
        if request.system:
            payload["system"] = request.system
        if tools:
            payload["tools"] = tools
        if sampling.temperature is not None:
            payload["temperature"] = sampling.temperature

        payload.update(self._options.provider_options)
        self._options.apply_raw(
            request_raw_options=request.raw_options,
            present=payload,
            applier=payload.__setitem__,
        )

        # Explicit non-stream call — ``**payload`` otherwise widens the return
        # type to ``Message | AsyncStream[...]``. Nothing above can have set
        # it: ``stream`` is a reserved raw option, so a caller asking for it
        # is refused rather than quietly overwritten here.
        payload["stream"] = False
        created = await self.client.messages.create(**payload)
        return _from_anthropic_message(cast(Message, created))


def _to_anthropic_messages(messages: Sequence[ModelMessage]) -> list[MessageParam]:
    out: list[MessageParam] = []
    for message in messages:
        if message.role is ModelMessageRole.SYSTEM:
            # System lives on the request.system field for Anthropic.
            continue
        role = "assistant" if message.role is ModelMessageRole.ASSISTANT else "user"
        if message.role is ModelMessageRole.TOOL:
            # Tool results are already Anthropic-shaped content when produced by
            # the Anthropic adapter loop; pass content through.
            out.append({"role": "user", "content": message.content})  # type: ignore[typeddict-item]
            continue
        out.append({"role": role, "content": message.content})  # type: ignore[typeddict-item]
    return out


def _to_anthropic_tools(tools: Sequence[Any] | None) -> list[ToolParam]:
    if not tools:
        return []
    converted: list[ToolParam] = []
    for tool in tools:
        match tool:
            case ToolDefinition():
                schema = tool.input_model.model_json_schema()
                converted.append(
                    {
                        "name": tool.name,
                        "description": (tool.input_model.__doc__ or tool.name).strip(),
                        "input_schema": schema,
                    }
                )
            case ToolSpec():
                converted.append(tool_spec_to_anthropic_schema(tool))  # type: ignore[arg-type]
            case Mapping():
                converted.append(dict(tool))  # type: ignore[arg-type]
            case _:
                raise TypeError(
                    f"Unsupported tool type for AnthropicProvider: {type(tool)!r}"
                )
    return converted


def _from_anthropic_message(response: Message) -> ModelResponse:
    text = ""
    tool_calls: list[ModelToolCall] = []
    for block in response.content:
        match block:
            case TextBlock(text=block_text):
                if block_text:
                    if text and not text[-1].isspace() and not block_text[0].isspace():
                        text += " "
                    text += block_text
            case ToolUseBlock(id=tool_id, name=tool_name, input=tool_input):
                tool_calls.append(
                    ModelToolCall(
                        id=tool_id,
                        name=tool_name,
                        arguments=dict(tool_input)
                        if isinstance(tool_input, Mapping)
                        else {},
                    )
                )
            case _:
                continue
    usage = None
    if response.usage is not None:
        usage = TurnUsage.from_object(
            response.usage,
            input="input_tokens",
            output="output_tokens",
            cache_read="cache_read_input_tokens",
            cache_write="cache_creation_input_tokens",
        )
    return ModelResponse(
        text=text or None,
        tool_calls=tuple(tool_calls),
        usage=usage,
        raw=response,
        stop_reason=response.stop_reason,
    )
