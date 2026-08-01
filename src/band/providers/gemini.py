"""Gemini ``ModelProvider`` — client ownership + request/response translation."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from uuid import uuid4
from typing import Any, Unpack

import httpx
from google import genai
from google.genai import types
from google.genai.errors import ServerError

from band.core.contracts.model import (
    ModelMessage,
    ModelMessageRole,
    ModelRequest,
    ModelResponse,
    ModelSamplingOptions,
    ModelToolCall,
)
from band.core.turn.history import GeminiHistoryPolicy
from band.core.options import (
    RAW_OPTIONS_RESERVED,
    UNSET,
    _Unset,
    ProviderOptionResolver,
    resolve_sampling,
)
from band.core.protocols import ModelContext
from band.core.tool_filter import sanitize_tool_schema
from band.core.tools import ToolSpec, tool_spec_to_openai_schema
from band.core.types import TurnUsage
from band.providers.types import (
    GEMINI_PROVIDER_OPTION_KEYS,
    GEMINI_RAW_OPTIONS_RESERVED,
    GeminiProviderOptions,
)
from band.runtime.tools import ToolDefinition

logger = logging.getLogger(__name__)


class GeminiProvider:
    """Pure Gemini request/response translation; owns the SDK client."""

    def __init__(
        self,
        model: str = "gemini-2.5-flash",
        *,
        api_key: str | None = None,
        temperature: float | None | _Unset = UNSET,
        max_output_tokens: int | None | _Unset = UNSET,
        max_retries: int = 2,
        retry_base_delay_s: float = 1.0,
        raw_options: Mapping[str, Any] | None = None,
        **provider_options: Unpack[GeminiProviderOptions],
    ) -> None:
        self.model = model
        self._api_key = api_key
        self.client: genai.Client | None = None
        self.max_retries = max_retries
        self.retry_base_delay_s = retry_base_delay_s

        self._instance_sampling = ModelSamplingOptions(
            temperature=None if temperature is UNSET else temperature,  # type: ignore[arg-type]
            max_output_tokens=None if max_output_tokens is UNSET else max_output_tokens,  # type: ignore[arg-type]
        )
        self._options = ProviderOptionResolver(
            reserved_keys=RAW_OPTIONS_RESERVED | GEMINI_RAW_OPTIONS_RESERVED
        )
        self._options.bind(
            named={
                "temperature": temperature,
                "max_output_tokens": max_output_tokens,
            },
            provider_options=provider_options,
            allowed=GEMINI_PROVIDER_OPTION_KEYS,
            raw_options=raw_options,
        )

    @property
    def sampling(self) -> ModelSamplingOptions:
        return self._instance_sampling

    def default_history_policy(self) -> GeminiHistoryPolicy:
        return GeminiHistoryPolicy()

    def ensure_client(self) -> genai.Client:
        if self.client is not None:
            return self.client
        try:
            self.client = genai.Client(api_key=self._api_key)
        except ValueError as exc:
            raise ValueError(
                "Gemini client initialization failed. Either set GOOGLE_API_KEY "
                "/ GEMINI_API_KEY, or enable Vertex AI mode "
                "(GOOGLE_GENAI_USE_VERTEXAI=true + GOOGLE_CLOUD_PROJECT)."
            ) from exc
        return self.client

    async def aclose(self) -> None:
        if self.client is not None:
            self.client.close()
        self.client = None

    async def complete(
        self, request: ModelRequest, *, context: ModelContext
    ) -> ModelResponse:
        context.cancellation.throw_if_cancelled()
        sampling = resolve_sampling(
            instance=self._instance_sampling,
            request=request.sampling,
        )
        contents = _to_gemini_contents(request.messages)
        tools = _to_gemini_tools(request.tools)

        config = types.GenerateContentConfig(
            system_instruction=request.system,
            tools=tools or None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        )
        if sampling.max_output_tokens is not None:
            config.max_output_tokens = sampling.max_output_tokens
        if sampling.temperature is not None:
            config.temperature = sampling.temperature

        for key, value in self._options.provider_options.items():
            setattr(config, key, value)

        # Everything this method put on the config is explicitly set, so
        # raw_options must collide rather than silently overwrite it —
        # `system_instruction` and `automatic_function_calling` especially,
        # since replacing those changes the prompt or breaks the tool loop.
        present: dict[str, Any] = dict(self._options.provider_options)
        for key in _CONFIGURED_KEYS:
            value = getattr(config, key, None)
            if value is not None:
                present[key] = value

        self._options.apply_raw(
            request_raw_options=request.raw_options,
            present=present,
            applier=lambda key, value: setattr(config, key, value),
        )

        response = await self._generate(contents, config)
        return _from_gemini_response(response)

    async def _generate(
        self,
        contents: list[types.Content],
        config: types.GenerateContentConfig,
    ) -> types.GenerateContentResponse:
        max_attempts = self.max_retries + 1
        client = self.ensure_client()
        for attempt in range(1, max_attempts + 1):
            try:
                return await client.aio.models.generate_content(
                    model=self.model,
                    contents=contents,  # type: ignore[arg-type]
                    config=config,
                )
            except (ServerError, httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt >= max_attempts:
                    raise
                delay_s = self.retry_base_delay_s * (2 ** (attempt - 1))
                logger.warning(
                    "Gemini transient error on attempt %s/%s: %s (retrying in %.2fs)",
                    attempt,
                    max_attempts,
                    exc,
                    delay_s,
                )
                await asyncio.sleep(delay_s)
        raise AssertionError("unreachable")  # pragma: no cover


def _to_gemini_contents(messages: Sequence[ModelMessage]) -> list[types.Content]:
    contents: list[types.Content] = []
    for message in messages:
        if message.role is ModelMessageRole.SYSTEM:
            continue
        # Pass through already-built Content objects from the Gemini adapter loop.
        if isinstance(message.content, types.Content):
            contents.append(message.content)
            continue
        role = "model" if message.role is ModelMessageRole.ASSISTANT else "user"
        contents.append(
            types.Content(role=role, parts=[types.Part(text=str(message.content))])
        )
    return contents


def _to_gemini_tools(tools: Sequence[Any] | None) -> list[types.Tool]:
    if not tools:
        return []
    declarations: list[types.FunctionDeclaration] = []
    for tool in tools:
        match tool:
            case ToolDefinition():
                schema = sanitize_tool_schema(tool.input_model.model_json_schema())
                declarations.append(
                    types.FunctionDeclaration(
                        name=tool.name,
                        description=(tool.input_model.__doc__ or tool.name).strip(),
                        parameters=schema,
                    )
                )
            case ToolSpec():
                openai = tool_spec_to_openai_schema(tool)
                function = openai.get("function", {})
                declarations.append(
                    types.FunctionDeclaration(
                        name=function.get("name", tool.name),
                        description=function.get("description", ""),
                        parameters=sanitize_tool_schema(
                            function.get(
                                "parameters", {"type": "object", "properties": {}}
                            )
                        ),
                    )
                )
            case types.Tool():
                return list(tools)  # already Gemini tools
            case Mapping():
                # OpenAI-shaped function schema
                function = tool.get("function", tool)
                name = function.get("name")
                if not name:
                    continue
                declarations.append(
                    types.FunctionDeclaration(
                        name=name,
                        description=function.get("description", ""),
                        parameters=sanitize_tool_schema(
                            function.get(
                                "parameters", {"type": "object", "properties": {}}
                            )
                        ),
                    )
                )
            case _:
                raise TypeError(
                    f"Unsupported tool type for GeminiProvider: {type(tool)!r}"
                )
    return [types.Tool(function_declarations=declarations)] if declarations else []


# Keys ``complete`` sets on the config itself; raw_options may not replace them.
_CONFIGURED_KEYS = (
    "system_instruction",
    "tools",
    "automatic_function_calling",
    "max_output_tokens",
    "temperature",
)


def _synthetic_call_id() -> str:
    """A tool-call id when Gemini omits one — unique across the whole turn.

    A per-response index restarts on the next round and collides with it.
    """
    return f"gemini_tool_call_{uuid4().hex}"


def _from_gemini_response(response: types.GenerateContentResponse) -> ModelResponse:
    text_parts: list[str] = []
    tool_calls: list[ModelToolCall] = []
    candidates = response.candidates or []
    if candidates:
        content = candidates[0].content
        parts = content.parts if content and content.parts else []
        for part in parts:
            if part.text:
                text_parts.append(part.text)
            fn = part.function_call
            if fn is not None and fn.name:
                args = dict(fn.args) if isinstance(fn.args, Mapping) else {}
                tool_calls.append(
                    ModelToolCall(
                        id=fn.id or _synthetic_call_id(),
                        name=fn.name,
                        arguments=args,
                    )
                )

    if not tool_calls:
        for fn in list(getattr(response, "function_calls", None) or []):
            if fn is None or not fn.name:
                continue
            args = dict(fn.args) if isinstance(fn.args, Mapping) else {}
            tool_calls.append(
                ModelToolCall(
                    id=fn.id or _synthetic_call_id(),
                    name=fn.name,
                    arguments=args,
                )
            )

    usage = None
    meta = getattr(response, "usage_metadata", None)
    if meta is not None:
        usage = TurnUsage.from_object(
            meta,
            input="prompt_token_count",
            output="candidates_token_count",
            reasoning="thoughts_token_count",
            cache_read="cached_content_token_count",
        )

    finish = None
    if candidates and candidates[0].finish_reason is not None:
        finish = str(candidates[0].finish_reason)

    return ModelResponse(
        text="".join(text_parts) or None,
        tool_calls=tuple(tool_calls),
        usage=usage,
        raw=response,
        stop_reason=finish,
    )
