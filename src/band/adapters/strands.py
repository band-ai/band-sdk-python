"""Band adapter for Strands Agents."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, ClassVar, cast

from band_sdk_core import AgentFailure
from pydantic import BaseModel

try:
    from strands import Agent
    from strands.agent import AgentResult
    from strands.hooks import HookProvider, HookRegistry
    from strands.hooks.events import AfterToolCallEvent, BeforeToolCallEvent
    from strands.models import Model
    from strands.models.openai import OpenAIModel
    from strands.types.media import ImageFormat
    from strands.types.tools import (
        AgentTool,
        ToolGenerator,
        ToolResult,
        ToolResultContent,
        ToolSpec,
        ToolUse,
    )
except ImportError as error:
    raise ImportError(
        "Strands Agents dependencies not installed. "
        "Install with: uv add band-sdk[strands]"
    ) from error

from typing_extensions import Unpack

from band.converters.strands import StrandsHistoryConverter, StrandsMessages
from band.core.adapterconfig import BaseAdapterConfig
from band.core.protocols import (
    GENERIC_PROVIDER_FAILURE_MESSAGE,
    AgentToolsProtocol,
)
from band.core.redaction import redact_credentials_deep
from band.core.simple_adapter import SimpleAdapter
from band.core.tool_filter import filter_tool_schemas
from band.core.turn import Turn
from band.core.types import (
    Capability,
    Emit,
    FeatureKwargs,
    MessageType,
    PlatformMessage,
    ToolEventKey,
    TurnUsage,
)
from band.runtime.custom_tools import (
    CustomToolDef,
    declared_effects,
    execute_custom_tool,
    get_custom_tool_name,
    reject_conflicting_tool_names,
)
from band.runtime.prompts import render_system_prompt
from band.runtime.tools import (
    ToolCallOutcome,
    ToolDefinition,
    TurnEffect,
    band_tool_errored,
    decode_image_block,
    get_band_tool_category,
    image_block_placeholder,
    is_image_passthrough_result,
    iter_tool_definitions,
    redact_tool_call_args,
    serialize_tool_result,
    validate_tool_arguments,
)
from band.runtime.tools.schema import is_failed_tool_output

logger = logging.getLogger(__name__)

_PROVIDER = "strands"
TURN_DIAGNOSTICS_LOG = "Room %s: Strands turn diagnostics: %s"


def _format_tool_output(value: object) -> str:
    """Return a stable text representation accepted by Strands tool results."""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


def _tool_result(tool_use: ToolUse, *, value: object, ok: bool) -> ToolResult:
    """Build the framework's typed result envelope at the Strands boundary."""
    content: list[ToolResultContent]
    status = "success" if ok else "error"
    if ok and is_image_passthrough_result(tool_use["name"], value):
        try:
            content = []
            for block in cast(dict[str, Any], value)["content"]:
                data, mime_type = decode_image_block(block)
                content.append(
                    {
                        "image": {
                            "format": cast(
                                ImageFormat, mime_type.removeprefix("image/")
                            ),
                            "source": {"bytes": data},
                        }
                    }
                )
        except Exception as error:  # noqa: BLE001 -- tool calls may raise any exception type; must surface to the LLM as an error string, not crash the turn
            # A malformed or future-extended image block (see
            # is_mcp_content_result's docstring) must degrade to the
            # adapter's normal failure result, not raise uncaught out of
            # stream()'s async generator -- this runs after _execute's own
            # try/except already succeeded, so it needs its own boundary.
            logger.error(
                "Failed to decode image content for %s: %s",
                tool_use["name"],
                error,
            )
            status = "error"
            content = [{"text": f"Error: {error}"}]
    else:
        content = [{"text": _format_tool_output(value)}]
    return {
        "toolUseId": tool_use["toolUseId"],
        "status": status,
        "content": content,
    }


def _result_text(result: ToolResult) -> str:
    """Flatten a tool result for execution events and failure detection."""
    parts: list[str] = []
    image_count = 0
    image_index: int | None = None
    for block in result.get("content", []):
        match block:
            case {"text": str() as text}:
                parts.append(text)
            case {"json": value}:
                parts.append(_format_tool_output(value))
            case {"image": _}:
                if image_index is None:
                    image_index = len(parts)
                    parts.append("")
                image_count += 1
    if image_index is not None:
        parts[image_index] = image_block_placeholder(image_count)
    return "\n".join(parts)


def _custom_tool_failed(result: ToolResult) -> bool:
    for block in result.get("content", []):
        match block:
            case {"json": value}:
                pass
            case {"text": str() as text}:
                # Native Strands tools serialize ordinary dict returns as text.
                try:
                    value = json.loads(text)
                except ValueError:
                    value = text
            case _:
                continue
        if is_failed_tool_output(value):
            return True
    return False


def _openai_history(messages: StrandsMessages) -> StrandsMessages:
    """Keep tool results ahead of text in Strands' OpenAI serialization."""
    normalized: StrandsMessages = []
    for message in messages:
        tool_results = [block for block in message["content"] if "toolResult" in block]
        other = [block for block in message["content"] if "toolResult" not in block]
        if tool_results and other:
            normalized.append({"role": message["role"], "content": tool_results})
            normalized.append({"role": message["role"], "content": other})
        else:
            normalized.append(message)
    return normalized


def _input_schema(input_model: type[BaseModel]) -> dict[str, Any]:
    """The JSON schema Strands advertises for a tool's input model.

    Built fresh per bridge, never shared: Strands normalizes a tool spec by
    writing defaults into the schema's nested ``properties`` in place, so a
    cached schema would be mutated by the framework it was handed to.
    """
    schema = input_model.model_json_schema()
    schema.pop("title", None)
    return schema


StrandsCustomTool = Callable[..., Any] | CustomToolDef


def _registered_name(tool: StrandsCustomTool) -> str:
    """Return the name Strands registers this tool under."""
    if isinstance(tool, tuple):
        return get_custom_tool_name(tool[0])
    if isinstance(tool, AgentTool):
        return tool.tool_name
    name = getattr(tool, "__name__", "")
    if not name:
        raise ValueError(
            f"Custom tool {tool!r} has no name. Pass a named function, a "
            "@strands.tool-decorated tool, or a (InputModel, handler) pair."
        )
    return name


def _custom_tool_effects(
    custom_tools: list[StrandsCustomTool],
) -> dict[str, TurnEffect]:
    """Validate custom tool names and collect the native tools' declared effects.

    A portable ``(InputModel, handler)`` tool records its own effect through
    ``execute_custom_tool``.
    """
    names = [_registered_name(tool) for tool in custom_tools]
    # Strands' registry is last-wins.
    reject_conflicting_tool_names(names)

    return declared_effects(
        (name, tool)
        for name, tool in zip(names, custom_tools, strict=True)
        if not isinstance(tool, tuple)
    )


def _bind_custom_tools(
    custom_tools: list[StrandsCustomTool], turn: Turn
) -> list[AgentTool | Callable[..., Any]]:
    """Adapt portable custom tools to Strands for one room turn."""
    return [
        CustomToolBridge(tool, turn) if isinstance(tool, tuple) else tool
        for tool in custom_tools
    ]


class StrandsToolBridge(AgentTool):
    """Base class for native Strands tools backed by a Pydantic input model."""

    def __init__(
        self,
        name: str,
        input_model: type[BaseModel],
        description: str,
    ) -> None:
        super().__init__()
        self._name = name
        self._spec: ToolSpec = {
            "name": name,
            "description": description,
            "inputSchema": {"json": _input_schema(input_model)},
        }

    @property
    def tool_name(self) -> str:
        return self._name

    @property
    def tool_spec(self) -> ToolSpec:
        return self._spec

    @property
    def tool_type(self) -> str:
        return "function"


class CustomToolBridge(StrandsToolBridge):
    """Expose a portable custom tool through Strands' native tool protocol."""

    def __init__(self, tool_def: CustomToolDef, turn: Turn):
        self._tool_def = tool_def
        self._turn = turn
        input_model, _ = tool_def
        name = get_custom_tool_name(input_model)
        super().__init__(name, input_model, input_model.__doc__ or name)

    async def stream(
        self,
        tool_use: ToolUse,
        invocation_state: dict[str, Any],
        **kwargs: Any,
    ) -> ToolGenerator:
        del invocation_state, kwargs
        try:
            result = await execute_custom_tool(
                self._tool_def, dict(tool_use["input"] or {}), turn=self._turn
            )
        except Exception as error:  # noqa: BLE001 -- tool calls may raise any exception type; must surface to the LLM as an error string, not crash the turn
            yield _tool_result(
                tool_use,
                value=f"Error executing tool '{self.tool_name}': {error}",
                ok=False,
            )
            return
        yield _tool_result(tool_use, value=result, ok=True)


class PlatformToolBridge(StrandsToolBridge):
    """Execute one registered Band tool against a turn-scoped capability.

    Strands calls the typed ``AgentToolsProtocol`` method directly. This is
    intentional: its framework-conformance contract observes those operations,
    and the shared registry still supplies the method name, schema, validation,
    and result serialization.
    """

    def __init__(self, definition: ToolDefinition, tools: AgentToolsProtocol):
        self._definition = definition
        self._tools = tools
        super().__init__(
            definition.name,
            definition.input_model,
            definition.input_model.__doc__ or definition.name,
        )

    async def stream(
        self,
        tool_use: ToolUse,
        invocation_state: dict[str, Any],
        **kwargs: Any,
    ) -> ToolGenerator:
        del invocation_state, kwargs
        outcome = await self._execute(dict(tool_use["input"] or {}))
        yield _tool_result(tool_use, value=outcome.value, ok=outcome.ok)

    async def _execute(self, arguments: dict[str, Any]) -> ToolCallOutcome:
        """Validate and dispatch through the registered typed protocol method."""
        try:
            validated = validate_tool_arguments(
                self.tool_name, self._definition.input_model, arguments
            )
        except ValueError as error:
            return ToolCallOutcome(value=str(error), ok=False, error_message=str(error))

        try:
            method = cast(
                Callable[..., Awaitable[object]],
                getattr(self._tools, self._definition.method_name),
            )
            value = await method(**validated)
        except Exception as error:
            logger.exception("Platform tool %s failed", self.tool_name)
            message = f"Error executing {self.tool_name}: {error}"
            return ToolCallOutcome(value=message, ok=False, error_message=message)
        return ToolCallOutcome(value=serialize_tool_result(value), ok=True)


class BandTurnHooks(HookProvider):
    """Emit execution events and record native tools' declared turn effects."""

    def __init__(
        self,
        tools: AgentToolsProtocol,
        *,
        emit_execution: bool,
        custom_effects: dict[str, TurnEffect],
    ) -> None:
        self._tools = tools
        self._emit_execution = emit_execution
        self._custom_effects = custom_effects
        self._calls: list[dict[ToolEventKey, object]] = []

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        del kwargs
        registry.add_callback(BeforeToolCallEvent, self._on_before_tool)
        registry.add_callback(AfterToolCallEvent, self._on_after_tool)

    async def _on_before_tool(self, event: BeforeToolCallEvent) -> None:
        call: dict[ToolEventKey, object] = {
            ToolEventKey.NAME: event.tool_use["name"],
            ToolEventKey.ARGS: redact_tool_call_args(
                event.tool_use["name"], event.tool_use["input"]
            ),
            ToolEventKey.TOOL_CALL_ID: event.tool_use["toolUseId"],
        }
        self._calls.append(call)
        if not self._emit_execution:
            return
        await self._emit_event(
            MessageType.TOOL_CALL,
            call,
        )

    async def _on_after_tool(self, event: AfterToolCallEvent) -> None:
        name = event.tool_use["name"]
        output = _result_text(event.result)
        succeeded = event.result.get("status") == "success" and not band_tool_errored(
            name, output
        )
        call = next(
            call
            for call in reversed(self._calls)
            if call[ToolEventKey.TOOL_CALL_ID] == event.tool_use["toolUseId"]
        )
        result_payload = {
            ToolEventKey.NAME: name,
            ToolEventKey.OUTPUT: output,
            ToolEventKey.TOOL_CALL_ID: event.tool_use["toolUseId"],
            # A restart must not replay a failed operation as a success.
            ToolEventKey.IS_ERROR: not succeeded,
        }
        call.update(result_payload)
        # Native Strands tools run outside execute_custom_tool, so their
        # declared effect is recorded here.
        if (
            succeeded
            and (effect := self._custom_effects.get(name))
            and not _custom_tool_failed(event.result)
        ):
            self._tools.turn.record(effect)
        if not self._emit_execution:
            return
        await self._emit_event(
            MessageType.TOOL_RESULT,
            result_payload,
        )

    def log_failure(self, room_id: str, result: AgentResult | None) -> None:
        """Keep the failed attempt's tool outcomes even when event emission is off."""
        calls = []
        for call in self._calls:
            output = call.get(ToolEventKey.OUTPUT)
            if isinstance(output, str):
                try:
                    output = json.loads(output)
                except ValueError:
                    pass
            calls.append({**call, ToolEventKey.OUTPUT: output})
        payload = redact_credentials_deep(
            {
                "tool_calls": calls,
                "stop_reason": result.stop_reason if result is not None else None,
                "final_text": str(result) if result is not None else None,
            }
        )
        logger.warning(TURN_DIAGNOSTICS_LOG, room_id, json.dumps(payload, default=str))

    async def _emit_event(
        self,
        message_type: MessageType,
        payload: Mapping[ToolEventKey, object],
    ) -> None:
        try:
            await self._tools.send_event(
                content=json.dumps(payload, default=str),
                message_type=message_type,
            )
        except Exception as error:  # noqa: BLE001 -- tool calls may raise any exception type; must surface to the LLM as an error string, not crash the turn
            logger.warning("Failed to send %s event: %s", message_type, error)


class StrandsAdapterConfig(BaseAdapterConfig):
    """Settings for a Strands agent.

    Attributes:
        model: Model id Strands resolves to a Bedrock model. Leave it ``None``
            only when the adapter is given a live ``llm``.
        system_prompt: Replaces the rendered Band system prompt entirely.
        custom_section: Extra instructions appended to the rendered prompt.
    """

    model: str | None = None
    system_prompt: str | None = None
    custom_section: str | None = None


class StrandsAdapter(SimpleAdapter[StrandsMessages]):
    """Run a Strands model in a Band room."""

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset({Emit.TOOL_CALLS, Emit.USAGE})
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.MEMORY, Capability.CONTACTS, Capability.TASKS, Capability.FILES}
    )

    def __init__(
        self,
        config: StrandsAdapterConfig | None = None,
        *,
        history_converter: StrandsHistoryConverter | None = None,
        additional_tools: list[StrandsCustomTool] | None = None,
        llm: Model | None = None,
        **features: Unpack[FeatureKwargs],
    ) -> None:
        """Create an adapter around a Strands model or Bedrock model id.

        Args:
            config: Model id and prompt settings; see :class:`StrandsAdapterConfig`.
            history_converter: Optional custom history converter.
            additional_tools: Strands tools and/or portable ``CustomToolDef``
                (InputModel, handler) tuples.
            llm: A constructed Strands ``Model`` (e.g. ``OpenAIModel``,
                ``BedrockModel``), used instead of ``config.model``. Exactly one
                of the two must be set.
        """
        super().__init__(
            history_converter=history_converter or StrandsHistoryConverter(),
            **features,
        )
        self.config = config or StrandsAdapterConfig()
        model = llm or self.config.model
        if model is None or (llm is not None and self.config.model is not None):
            raise ValueError("Set exactly one of config.model or llm")
        self._model: Model | str = model
        self._system_prompt: str | None = None
        self._message_history: dict[str, StrandsMessages] = {}
        self._custom_tools = additional_tools or []
        self._custom_effects = _custom_tool_effects(self._custom_tools)

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Render the prompt after the platform supplies agent metadata."""
        await super().on_started(agent_name, agent_description)
        self._system_prompt = self.config.system_prompt or render_system_prompt(
            agent_name=self.agent_name,
            agent_description=self.agent_description or "An AI assistant",
            custom_section=self.config.custom_section or "",
            features=self.features,
        )
        logger.info("Strands adapter started for agent: %s", agent_name)

    def _build_agent(
        self,
        messages: StrandsMessages,
        tools: AgentToolsProtocol,
        hooks: BandTurnHooks,
    ) -> Agent:
        """Create an isolated agent whose tools own one turn's capability.

        Strands' default conversation manager trims the oldest messages once the
        transcript passes its window (40 messages) and again on context
        overflow, keeping toolUse/toolResult pairs intact. The persisted room
        transcript is therefore capped by the framework, not unbounded.
        """
        framework_tools = self._build_platform_tools(tools) + _bind_custom_tools(
            self._custom_tools, tools.turn
        )
        return Agent(
            model=self._model,
            messages=messages,
            # Strands accepts functions, dict specs, providers, and AgentTools,
            # but its public annotation cannot express that mixed collection.
            tools=cast(list[Any], framework_tools),
            system_prompt=self._system_prompt,
            hooks=[hooks],
            callback_handler=None,
            name=self.agent_name or None,
        )

    def _build_platform_tools(self, tools: AgentToolsProtocol) -> list[AgentTool]:
        """Adapt the central Band tool registry to Strands for this turn.

        The caller's include/exclude/category filters decide the surface: a tool
        the features exclude must never reach the model, since reaching it is
        enough to execute it.
        """
        definitions = filter_tool_schemas(
            list(
                iter_tool_definitions(
                    capabilities=self.features.capabilities,
                )
            ),
            self.features,
            get_name=lambda definition: definition.name,
            get_category=lambda definition: get_band_tool_category(definition.name),
        )
        return [PlatformToolBridge(definition, tools) for definition in definitions]

    def _history_for_turn(
        self,
        room_id: str,
        history: StrandsMessages,
        *,
        is_session_bootstrap: bool,
    ) -> StrandsMessages:
        """Get the room transcript, using platform history only at session start."""
        if is_session_bootstrap:
            if isinstance(self._model, OpenAIModel):
                history = _openai_history(history)
            self._message_history[room_id] = list(history)
            if history:
                logger.debug("Room %s: rehydrated %s message(s)", room_id, len(history))
        return self._message_history.setdefault(room_id, [])

    @staticmethod
    def _with_system_context(
        user_message: str,
        participants_msg: str | None,
        contacts_msg: str | None,
    ) -> str:
        """Lead the turn's prompt with any changed platform context.

        Strands appends the prompt as its own user message, so context posted
        as a separate message would leave two user turns in a row — which
        Bedrock's Converse rejects.
        """
        notices = [
            f"[System]: {message}"
            for message in (participants_msg, contacts_msg)
            if message
        ]
        return "\n\n".join([*notices, user_message])

    async def _run_turn(
        self,
        *,
        message: str,
        room_id: str,
        history: StrandsMessages,
        tools: AgentToolsProtocol,
        hooks: BandTurnHooks,
    ) -> None:
        """Run the framework loop while preserving transcript and usage on failure."""
        agent: Agent | None = None
        result: AgentResult | None = None
        try:
            agent = self._build_agent(history, tools, hooks)
            result = await agent.invoke_async(message)
        except Exception:
            logger.exception("Room %s: Strands turn failed", room_id)
            await tools.send_failure(
                AgentFailure(_PROVIDER, GENERIC_PROVIDER_FAILURE_MESSAGE)
            )
            raise
        finally:
            # Unjudged turns (contact hub) often end without a reply; those are
            # not failures, so quiet completion still skips diagnostics.
            if result is None or (tools.turn.judged and not tools.turn.complete):
                hooks.log_failure(room_id, result)
            if agent is not None:
                self._message_history[room_id] = agent.messages
                await self.emit_usage(tools, self._usage_from_agent(agent))

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: StrandsMessages,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        """Run one room turn through the Strands agent loop."""
        room_history = self._history_for_turn(
            room_id, history, is_session_bootstrap=is_session_bootstrap
        )
        user_message = self._with_system_context(
            msg.format_for_llm(), participants_msg, contacts_msg
        )
        logger.debug(
            "Room %s: Running Strands agent (history: %s msgs, prompt: %s...)",
            room_id,
            len(room_history),
            user_message[:80],
        )

        hooks = BandTurnHooks(
            tools,
            emit_execution=Emit.TOOL_CALLS in self.features.emit,
            custom_effects=self._custom_effects,
        )
        await self._run_turn(
            message=user_message,
            room_id=room_id,
            history=room_history,
            tools=tools,
            hooks=hooks,
        )
        logger.debug(
            "Room %s: Strands agent completed (history now has %s messages)",
            room_id,
            len(self._message_history[room_id]),
        )

    @staticmethod
    def _usage_from_agent(agent: Agent) -> TurnUsage:
        """Map Strands' accumulated turn usage into the SDK value object."""
        try:
            usage = dict(agent.event_loop_metrics.accumulated_usage)
        except Exception:  # pragma: no cover - usage reporting is best-effort  # noqa: BLE001 -- tool calls may raise any exception type; must surface to the LLM as an error string, not crash the turn
            return TurnUsage()
        return TurnUsage.from_mapping(
            usage,
            input="inputTokens",
            output="outputTokens",
            cache_read="cacheReadInputTokens",
            cache_write="cacheWriteInputTokens",
        )

    async def on_cleanup(self, room_id: str) -> None:
        """Discard the transcript when Band removes the adapter from a room."""
        if self._message_history.pop(room_id, None) is not None:
            logger.debug("Room %s: Cleaned up message history", room_id)
