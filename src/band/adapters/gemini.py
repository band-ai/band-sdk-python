"""Gemini adapter using the official google-genai SDK."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, ClassVar, cast

import httpx
from band_sdk_core import AgentFailure
from pydantic import (
    Field,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveInt,
    ValidationError,
)
from typing_extensions import Unpack

try:
    from google import genai  # type: ignore[missing-module-attribute]
    from google.genai import types  # type: ignore[missing-import]
    from google.genai.errors import ServerError  # type: ignore[missing-import]
except ImportError as e:
    raise ImportError(
        "google-genai is required for Gemini adapter.\n"
        "Install with: pip install 'band-sdk[gemini]'\n"
        "Or: uv add google-genai"
    ) from e

from band.converters.gemini import GeminiHistoryConverter, GeminiMessages
from band.core.adapterconfig import BaseAdapterConfig
from band.core.exceptions import ProviderRunError
from band.core.protocols import AgentToolsProtocol, generic_provider_failure
from band.core.simple_adapter import SimpleAdapter
from band.core.tool_filter import sanitize_tool_schema
from band.core.types import (
    Capability,
    Emit,
    FeatureKwargs,
    PlatformMessage,
    ToolEventKey,
    TurnUsage,
)
from band.runtime.custom_tools import (
    CustomToolDef,
    execute_custom_tool,
    find_custom_tool,
    format_validation_error,
    get_custom_tool_name,
)
from band.runtime.prompts import render_system_prompt
from band.runtime.tools import (
    decode_image_block,
    image_block_placeholder,
    is_image_passthrough_result,
    redact_tool_call_args,
)

logger = logging.getLogger(__name__)

_PROVIDER = "gemini"


def _image_function_response_parts(
    result: dict[str, Any],
) -> list[types.FunctionResponsePart]:
    """Convert an MCP-content-shaped band_read_room_file result into Gemini
    FunctionResponsePart inline_data blocks, so the model receives real image
    content instead of a JSON-stringified blob."""
    parts: list[types.FunctionResponsePart] = []
    for block in result["content"]:
        data, mime_type = decode_image_block(block)
        parts.append(
            types.FunctionResponsePart(
                inline_data=types.FunctionResponseBlob(mime_type=mime_type, data=data)
            )
        )
    return parts


def _to_agent_failure(e: Exception) -> AgentFailure:
    """Parse a turn-ending exception into the shared provider-failure shape.

    ``ServerError`` carries an HTTP status and message that a plain
    exception's text alone does not.
    """
    if isinstance(e, ServerError):
        status = e.status if e.status is None else str(e.status)
        return AgentFailure(_PROVIDER, str(e), status, e.message)
    return generic_provider_failure(_PROVIDER, e)


def _model_failure(response: types.GenerateContentResponse) -> ProviderRunError | None:
    """The failure a response carries as data (a blocked prompt, a safety stop),
    read the way google-adk's ``LlmResponse.create`` reads it. A reply with
    content, even one cut off at MAX_TOKENS, and an empty normal finish are
    not failures."""
    if response.candidates:
        candidate = response.candidates[0]
        if candidate.content and candidate.content.parts:
            return None
        reason = candidate.finish_reason
        if reason is None or reason == types.FinishReason.STOP:
            return None
        return ProviderRunError(reason.value, candidate.finish_message)
    feedback = response.prompt_feedback
    if feedback is None or feedback.block_reason is None:
        return None
    return ProviderRunError(feedback.block_reason.value, feedback.block_reason_message)


class GeminiAdapterConfig(BaseAdapterConfig):
    """Settings for :class:`GeminiAdapter`.

    Attributes:
        model: Gemini model name sent with every request.
        provider_key: Gemini API key; ``None`` lets the google-genai client
            read ``GOOGLE_API_KEY``/``GEMINI_API_KEY`` or use Vertex AI.
        system_prompt: Complete system prompt. When set it is sent verbatim
            and ``custom_section``, ``include_base_instructions`` and the
            capability sections are ignored.
        custom_section: Extra instructions appended to the rendered system
            prompt.
        include_base_instructions: Whether the rendered system prompt starts
            with the SDK's base instructions.
        max_output_tokens: Output token cap per call; ``None`` uses the model
            default.
        temperature: Sampling temperature; ``None`` uses the model default.
        max_tool_rounds: Model calls a single turn may make before it fails.
        max_retries: Retries of a call after a transient server or transport
            error.
        retry_base_delay_s: First retry delay in seconds, doubled on each
            further retry.
        max_history_messages: Room history entries kept per room; older ones
            are trimmed.
    """

    model: str = "gemini-2.5-flash"
    provider_key: str | None = Field(default=None, repr=False)
    system_prompt: str | None = None
    custom_section: str = ""
    include_base_instructions: bool = True
    max_output_tokens: PositiveInt | None = None
    temperature: float | None = None
    max_tool_rounds: PositiveInt = 20
    max_retries: NonNegativeInt = 2
    retry_base_delay_s: NonNegativeFloat = 1.0
    max_history_messages: PositiveInt = 200


class GeminiAdapter(SimpleAdapter[GeminiMessages]):
    """
    Gemini SDK adapter using SimpleAdapter pattern.

    Uses the official google-genai Python SDK with explicit tool-loop control.

    Example:
        adapter = GeminiAdapter(
            GeminiAdapterConfig(
                model="gemini-2.5-flash",
                custom_section="You are a helpful assistant.",
            ),
            capabilities=Capability.MEMORY,
        )
        agent = Agent.create(adapter=adapter, agent_id="...", api_key="...")
        await agent.run()
    """

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset({Emit.TOOL_CALLS, Emit.USAGE})
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.MEMORY, Capability.CONTACTS, Capability.TASKS, Capability.FILES}
    )

    def __init__(
        self,
        config: GeminiAdapterConfig | None = None,
        *,
        history_converter: GeminiHistoryConverter | None = None,
        additional_tools: list[CustomToolDef] | None = None,
        **features: Unpack[FeatureKwargs],
    ) -> None:
        """
        Args:
            config: Model, credentials, prompt, sampling and retry settings.
            history_converter: Converts platform history into Gemini contents;
                defaults to :class:`GeminiHistoryConverter`.
            additional_tools: Custom tools offered alongside the platform tools.
        """
        super().__init__(
            history_converter=history_converter or GeminiHistoryConverter(),
            **features,
        )
        self.config = config or GeminiAdapterConfig()
        self.client: genai.Client | None = None
        self._message_history: dict[str, GeminiMessages] = {}
        self._system_prompt: str = ""
        self._custom_tools: list[CustomToolDef] = additional_tools or []

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Render system prompt after agent metadata is fetched.

        ``config.system_prompt``, when set, wins outright; otherwise
        ``render_system_prompt`` builds the prompt from the config's
        ``custom_section`` and ``include_base_instructions`` with capability
        sections gated on ``features.capabilities``.
        """
        await super().on_started(agent_name, agent_description)
        self._system_prompt = self.config.system_prompt or render_system_prompt(
            agent_name=agent_name,
            agent_description=agent_description,
            custom_section=self.config.custom_section,
            include_base_instructions=self.config.include_base_instructions,
            features=self.features,
        )
        logger.info("Gemini adapter started for agent: %s", agent_name)

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: GeminiMessages,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        """Handle incoming message with explicit function-calling loop."""
        if is_session_bootstrap:
            if history:
                self._message_history[room_id] = list(history)
                logger.info(
                    "Room %s: Loaded %s historical Gemini messages",
                    room_id,
                    len(history),
                )
            else:
                self._message_history[room_id] = []
                logger.info("Room %s: No historical messages found", room_id)
        elif room_id not in self._message_history:
            self._message_history[room_id] = []

        # Merge all user-side content into a single Content to respect
        # Gemini's strict user/model turn alternation requirement.
        user_parts: list[types.Part] = []
        if participants_msg:
            user_parts.append(
                types.Part.from_text(text=f"[System]: {participants_msg}")
            )
            logger.info("Room %s: Participants updated", room_id)
        if contacts_msg:
            user_parts.append(types.Part.from_text(text=f"[System]: {contacts_msg}"))
            logger.info("Room %s: Contacts broadcast received", room_id)
        user_parts.append(types.Part.from_text(text=msg.format_for_llm()))
        self._message_history[room_id].append(
            types.Content(role="user", parts=user_parts)
        )

        gemini_tools = self._build_gemini_tools(tools)
        tool_rounds = 0
        tool_sequence: list[list[str]] = []
        # Gemini reports usage per call; sum across the loop into one
        # TurnUsage, emitted on every exit via the finally.
        turn_usage = TurnUsage()
        try:
            while True:
                if tool_rounds >= self.config.max_tool_rounds:
                    message = (
                        f"Exceeded max tool rounds ({self.config.max_tool_rounds}) "
                        f"in room {room_id}; tool sequence: {tool_sequence}"
                    )
                    await tools.send_failure(AgentFailure(_PROVIDER, message))
                    raise RuntimeError(message)

                try:
                    response = await self._call_gemini(
                        contents=self._message_history[room_id], tools=gemini_tools
                    )
                    turn_usage = turn_usage + self._usage_from_response(response)
                    if failure := _model_failure(response):
                        raise failure
                except Exception as e:
                    logger.exception("Error calling Gemini")
                    await tools.send_failure(_to_agent_failure(e))
                    raise

                candidate_content = self._extract_candidate_content(response)
                if candidate_content is not None:
                    self._message_history[room_id].append(candidate_content)

                function_calls = list(response.function_calls or [])
                if not function_calls:
                    break
                tool_sequence.append([call.name or "" for call in function_calls])

                tool_response_parts = await self._process_function_calls(
                    function_calls=function_calls,
                    tools=tools,
                )
                if tool_response_parts:
                    self._message_history[room_id].append(
                        types.Content(role="user", parts=tool_response_parts)
                    )

                tool_rounds += 1
        finally:
            # No-op unless Emit.USAGE is on; best-effort, never raises.
            await self.emit_usage(tools, turn_usage)

        # Trim after the tool loop so the LLM always sees full context for the
        # current turn; trimming only affects the next turn's window.
        self._trim_history(room_id)

    async def on_cleanup(self, room_id: str) -> None:
        """Clean up message history when the agent leaves a room."""
        self._message_history.pop(room_id, None)
        logger.debug("Room %s: Cleaned up Gemini history", room_id)

    @staticmethod
    def _usage_from_response(response: Any) -> TurnUsage:
        """Map a Gemini ``GenerateContentResponse.usage_metadata`` onto TurnUsage.

        A response without usage yields empty usage. Gemini has no cache-write
        dimension (left 0); ``cached_content_token_count`` is the cache read.

        Gemini reports thinking tokens *disjointly* from output (its own
        ``total_token_count`` is ``prompt + candidates + thoughts``, so
        ``candidates_token_count`` excludes thoughts), so fold
        ``thoughts_token_count`` into ``output_tokens`` — otherwise thinking-model
        turns undercount, and this stays consistent with providers that already
        count reasoning inside output.
        """
        return TurnUsage.from_object(
            getattr(response, "usage_metadata", None),
            input="prompt_token_count",
            output="candidates_token_count",
            reasoning="thoughts_token_count",
            cache_read="cached_content_token_count",
        )

    def _trim_history(self, room_id: str) -> None:
        """Trim message history to stay within ``max_history_messages``.

        After slicing, realigns to the next user turn and drops orphaned
        function responses that no longer have their matching function call.
        """
        history = self._message_history.get(room_id)
        if not history or len(history) <= self.config.max_history_messages:
            return

        trimmed_count = len(history) - self.config.max_history_messages
        trimmed = history[-self.config.max_history_messages :]

        while trimmed:
            first = trimmed[0]
            if first.role == "model":
                trimmed.pop(0)
                trimmed_count += 1
                continue

            normalized = self._drop_leading_tool_response_parts(first)
            if normalized is None:
                trimmed.pop(0)
                trimmed_count += 1
                continue

            trimmed[0] = normalized
            break

        self._message_history[room_id] = trimmed
        logger.debug(
            "Room %s: Trimmed %s oldest messages (kept %s)",
            room_id,
            trimmed_count,
            len(trimmed),
        )

    @staticmethod
    def _drop_leading_tool_response_parts(
        content: types.Content,
    ) -> types.Content | None:
        """Remove orphaned leading function_response parts from a user turn."""
        parts = list(content.parts or [])
        first_non_response_index = 0
        while (
            first_non_response_index < len(parts)
            and parts[first_non_response_index].function_response is not None
        ):
            first_non_response_index += 1

        if first_non_response_index == 0:
            return content

        remaining_parts = parts[first_non_response_index:]
        if not remaining_parts:
            return None

        return types.Content(role=content.role, parts=remaining_parts)

    @staticmethod
    def _is_tool_response(content: types.Content) -> bool:
        """Check if a Content entry contains only function_response parts."""
        if not content.parts:
            return False
        return all(part.function_response is not None for part in content.parts)

    def _ensure_client(self) -> genai.Client:
        """Create client lazily to avoid requiring API key during adapter init."""
        if self.client is not None:
            return self.client

        try:
            self.client = genai.Client(api_key=self.config.provider_key)
        except ValueError as e:
            raise ValueError(
                "Gemini client initialization failed. Either set GOOGLE_API_KEY "
                "/ GEMINI_API_KEY, or enable Vertex AI mode "
                "(GOOGLE_GENAI_USE_VERTEXAI=true + GOOGLE_CLOUD_PROJECT)."
            ) from e
        return self.client

    async def _call_gemini(
        self,
        contents: GeminiMessages,
        tools: list[types.Tool],
    ) -> types.GenerateContentResponse:
        """
        Call Gemini API with bounded retries for transient transport/server failures.
        """
        request_config = types.GenerateContentConfig(
            system_instruction=self._system_prompt,
            tools=tools or None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
            max_output_tokens=self.config.max_output_tokens,
            temperature=self.config.temperature,
        )

        max_attempts = self.config.max_retries + 1
        client = self._ensure_client()
        for attempt in range(1, max_attempts + 1):
            try:
                return await client.aio.models.generate_content(
                    model=self.config.model,
                    contents=cast(Any, contents),
                    config=request_config,
                )
            except (ServerError, httpx.TimeoutException, httpx.TransportError) as e:
                if attempt >= max_attempts:
                    raise
                delay_s = self.config.retry_base_delay_s * (2 ** (attempt - 1))
                logger.warning(
                    "Gemini transient error on attempt %s/%s: %s (retrying in %.2fs)",
                    attempt,
                    max_attempts,
                    e,
                    delay_s,
                )
                await asyncio.sleep(delay_s)
        # Unreachable: loop always returns on success or re-raises on last attempt.
        raise AssertionError("unreachable")  # pragma: no cover

    def _build_gemini_tools(self, tools: AgentToolsProtocol) -> list[types.Tool]:
        """Build Gemini function declarations from platform and custom tools."""
        declarations: list[types.FunctionDeclaration] = []

        openai_schemas = tools.get_openai_tool_schemas(
            capabilities=self.features.capabilities,
        )
        for schema in openai_schemas:
            function = schema.get("function", {})
            name = function.get("name")
            if not name:
                continue
            parameters = sanitize_tool_schema(
                function.get("parameters", {"type": "object", "properties": {}}),
                drop_numeric_bounds=True,
                drop_additional_properties=True,
            )
            declarations.append(
                types.FunctionDeclaration(
                    name=name,
                    description=function.get("description", "") or "",
                    parameters_json_schema=parameters,
                )
            )

        for input_model, _func in self._custom_tools:
            schema = input_model.model_json_schema()
            schema.pop("title", None)
            schema = sanitize_tool_schema(
                schema, drop_numeric_bounds=True, drop_additional_properties=True
            )
            tool_name = get_custom_tool_name(input_model)
            declarations.append(
                types.FunctionDeclaration(
                    name=tool_name,
                    description=input_model.__doc__ or "",
                    parameters_json_schema=schema,
                )
            )

        if not declarations:
            return []
        return [types.Tool(function_declarations=declarations)]

    def _extract_candidate_content(
        self, response: types.GenerateContentResponse
    ) -> types.Content | None:
        """Extract the model output content for history persistence."""
        if response.candidates and response.candidates[0].content:
            return response.candidates[0].content

        function_calls = response.function_calls or []
        if function_calls:
            parts: list[types.Part] = []
            for call in function_calls:
                if call.name:
                    parts.append(
                        types.Part(
                            function_call=types.FunctionCall(
                                id=call.id,
                                name=call.name,
                                args=dict(call.args or {}),
                            )
                        )
                    )
            if parts:
                return types.Content(role="model", parts=parts)
        return None

    async def _process_function_calls(
        self,
        function_calls: list[types.FunctionCall],
        tools: AgentToolsProtocol,
    ) -> list[types.Part]:
        """Execute model function calls and return function_response parts."""
        tool_response_parts: list[types.Part] = []

        for index, function_call in enumerate(function_calls):
            tool_name = function_call.name or ""
            tool_input = dict(function_call.args or {})
            tool_call_id = function_call.id or f"gemini_tool_call_{index}"

            if Emit.TOOL_CALLS in self.features.emit:
                try:
                    await tools.send_event(
                        content=json.dumps(
                            {
                                ToolEventKey.NAME: tool_name,
                                ToolEventKey.ARGS: redact_tool_call_args(
                                    tool_name, tool_input
                                ),
                                ToolEventKey.TOOL_CALL_ID: tool_call_id,
                            }
                        ),
                        message_type="tool_call",
                    )
                except Exception as e:  # noqa: BLE001 -- tool calls may raise any exception type; must surface to the LLM as an error string, not crash the turn
                    logger.warning("Failed to send tool_call event: %s", e)

            response_parts: list[types.FunctionResponsePart] | None = None
            try:
                custom_tool = find_custom_tool(self._custom_tools, tool_name)
                if custom_tool:
                    result = await execute_custom_tool(
                        custom_tool, tool_input, turn=tools.turn
                    )
                else:
                    result = await tools.execute_tool_call(tool_name, tool_input)
                if is_image_passthrough_result(tool_name, result):
                    response_parts = _image_function_response_parts(result)
                    result_str = image_block_placeholder(len(response_parts))
                else:
                    result_str = (
                        json.dumps(result, default=str)
                        if not isinstance(result, str)
                        else result
                    )
                is_error = False
            except ValidationError as exc:
                errors = format_validation_error(exc)
                result_str = f"Invalid arguments for {tool_name}: {errors}"
                is_error = True
                logger.warning("Validation error for tool %s: %s", tool_name, errors)
            except Exception as e:
                result_str = f"Error: {e}"
                is_error = True
                logger.exception("Tool %s failed", tool_name)
            if Emit.TOOL_CALLS in self.features.emit:
                try:
                    await tools.send_event(
                        content=json.dumps(
                            {
                                ToolEventKey.NAME: tool_name,
                                ToolEventKey.OUTPUT: result_str,
                                ToolEventKey.TOOL_CALL_ID: tool_call_id,
                                ToolEventKey.IS_ERROR: is_error,
                            }
                        ),
                        message_type="tool_result",
                    )
                except Exception as e:  # noqa: BLE001 -- tool calls may raise any exception type; must surface to the LLM as an error string, not crash the turn
                    logger.warning("Failed to send tool_result event: %s", e)

            response_payload = (
                {"error": result_str} if is_error else {"output": result_str}
            )
            tool_response_parts.append(
                types.Part(
                    function_response=types.FunctionResponse(
                        id=tool_call_id,
                        name=tool_name,
                        response=response_payload,
                        parts=response_parts,
                    )
                )
            )

        return tool_response_parts
