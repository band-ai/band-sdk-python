"""
Pydantic AI adapter using SimpleAdapter pattern.

Owns the agent lifecycle only — prompt, history, streaming, custom tools. The
built-in Band tool surface is built by
``band.integrations.pydantic_ai.tools.build_band_pydantic_ai_tools``.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import Callable
from contextvars import ContextVar
from time import monotonic
from typing import Any, ClassVar, get_origin, get_type_hints

from band_sdk_core import AgentFailure
from pydantic_ai import (
    Agent,
    AgentRunResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    InstrumentationSettings,
    ModelRequestNode,
    RunContext,
    UnexpectedModelBehavior,
    capture_run_messages,
)
from pydantic_ai.capabilities import Hooks, ProcessHistory
from pydantic_ai.capabilities.abstract import AgentNode, NodeResult
from pydantic_ai.exceptions import SkipModelRequest
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import (
    Model,
    ModelRequestContext,
)
from pydantic_ai.result import FinalResult
from pydantic_graph import End
from typing_extensions import Unpack

from band.converters.pydantic_ai import (
    PydanticAIHistoryConverter,
    PydanticAIMessages,
)
from band.core.adapterconfig import BaseAdapterConfig
from band.core.protocols import (
    GENERIC_PROVIDER_FAILURE_MESSAGE,
    AgentToolsProtocol,
)
from band.core.simple_adapter import SimpleAdapter
from band.core.types import (
    Capability,
    Emit,
    FeatureKwargs,
    PlatformMessage,
    ToolEventKey,
    TurnUsage,
)
from band.integrations.pydantic_ai.tools import build_band_pydantic_ai_tools
from band.runtime.custom_tools import (
    CustomToolDef,
    declared_effects,
    get_custom_tool_name,
    invoke_validated_custom_tool,
)
from band.runtime.prompts import render_system_prompt
from band.runtime.tools import (
    BandTool,
    image_block_placeholder,
    redact_tool_call_args,
)
from band.runtime.tools.schema import is_failed_tool_output
from band.runtime.working_state import end_tool_activity, report_tool_activity

logger = logging.getLogger(__name__)

_PROVIDER = "pydantic_ai"
DIAGNOSTIC_TOOL_NAME_LIMIT = 80

_native_turn: ContextVar[tuple[str, str, float] | None] = ContextVar(
    "pydantic_native_turn", default=None
)


def _log_native_progress(
    phase: str,
    *,
    request: int | None = None,
    events: int | None = None,
    tool: str | None = None,
    error: BaseException | None = None,
) -> None:
    turn = _native_turn.get()
    if turn is None:
        return
    room_id, message_id, started = turn
    logger.debug(
        "Pydantic native room=%s message=%s phase=%s elapsed=%.3f "
        "request=%s events=%s tool=%r error=%s",
        room_id,
        message_id,
        phase,
        monotonic() - started,
        request,
        events,
        tool[:DIAGNOSTIC_TOOL_NAME_LIMIT] if tool is not None else None,
        type(error).__name__ if error is not None else None,
    )


def _before_model_request(
    ctx: RunContext[AgentToolsProtocol], request_context: ModelRequestContext
) -> ModelRequestContext:
    _log_native_progress("request_attempt", request=ctx.run_step)
    return request_context


def _after_model_request(
    ctx: RunContext[AgentToolsProtocol],
    *,
    request_context: ModelRequestContext,
    response: ModelResponse,
) -> ModelResponse:
    # With streaming, this hook sees the assembled response after consumption.
    _log_native_progress("response_ready", request=ctx.run_step)
    return _drop_blank_text(ctx, request_context=request_context, response=response)


def _model_request_error(
    ctx: RunContext[AgentToolsProtocol],
    *,
    request_context: ModelRequestContext,
    error: Exception,
) -> ModelResponse:
    _log_native_progress("request_error", request=ctx.run_step, error=error)
    raise error


def _explicit_decline_is_terminal(request: ModelRequest) -> bool:
    """A current-run decline ends the loop when every tool result in its batch succeeded."""
    declined = False
    for part in request.parts:
        if isinstance(part, RetryPromptPart):
            return False
        if not isinstance(part, ToolReturnPart):
            continue
        if part.outcome != "success" or is_failed_tool_output(part.content):
            return False
        if part.tool_name == BandTool.NO_REPLY:
            declined = True
    return declined


async def _finish_explicit_decline_after_tools(
    ctx: RunContext[AgentToolsProtocol],
    *,
    node: AgentNode[Any],
    result: NodeResult[Any],
) -> NodeResult[Any]:
    """End the run after a terminal decline without scheduling another model step.

    pydantic-ai 2.55+ enforces ``request_limit`` before ``before_model_request`` hooks,
    so a follow-up ``ModelRequestNode`` after tools can trip the limit even when the
    hook would have skipped the call. Converting that node to ``End`` avoids the extra
    step entirely.
    """
    if isinstance(result, ModelRequestNode) and _explicit_decline_is_terminal(
        result.request
    ):
        ctx.messages.append(result.request)
        return End(FinalResult(None))
    return result


def _finish_explicit_decline(
    ctx: RunContext[AgentToolsProtocol], request_context: ModelRequestContext
) -> ModelRequestContext:
    """Finish after a successful current-run decline whose tool batch succeeded."""
    if not request_context.messages:
        return request_context
    request = request_context.messages[-1]
    if (
        isinstance(request, ModelRequest)
        and request.run_id == ctx.run_id
        and _explicit_decline_is_terminal(request)
    ):
        raise SkipModelRequest(ModelResponse(parts=[]))
    return request_context


OUTPUT_RETRIES_EXHAUSTED = "exceeded maximum output retries"
"""pydantic-ai's wording when a run burns its output-retry budget.

It exposes no structured code for this, so the message text is the only signal —
matched case-insensitively. A guard test asserts pydantic-ai still raises this
exact phrase, because a silent reword would disable the swallow below rather than
announce itself.
"""


def _is_output_retries_exhausted(exc: UnexpectedModelBehavior) -> bool:
    """Whether ``exc`` is pydantic-ai's exhausted output-retry budget.

    The coupling to pydantic-ai's wording is deliberate and **fail-safe**: on a
    reword a benign post-tool turn propagates as an error, never the reverse.
    """
    return OUTPUT_RETRIES_EXHAUSTED in str(exc).lower()


# A response made up only of these (or with no parts at all) serializes to
# content:null, which providers reject when it's replayed as history.
_NON_REPLAYABLE_RESPONSE_PARTS = (ThinkingPart,)


def _is_replayable_history_message(message: Any) -> bool:
    """Drop assistant responses that would replay as content:null.

    Keep any response with at least one content-bearing part (text, tool
    calls, native tool calls/returns, files). Drop thinking-only and empty
    responses, which providers reject when sent back as history.
    """
    if isinstance(message, ModelResponse):
        return any(
            not isinstance(part, _NON_REPLAYABLE_RESPONSE_PARTS)
            for part in message.parts
        )
    return True


def _drop_non_replayable_messages(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Pydantic AI history processor: strip responses that replay as content:null.

    Runs before *every* model request — including the extra requests pydantic-ai
    makes mid-run after tool returns. The model can emit an empty or thinking-only
    response within a single turn; replaying it to the provider sends an assistant
    message with ``content: null`` and no tool calls, which providers reject (e.g.
    OpenAI 400 "Invalid value for 'content': expected a string, got null"). The
    storage filter only sanitizes history persisted *between* turns, so this closes
    the within-run gap.
    """
    return [m for m in messages if _is_replayable_history_message(m)]


def _drop_blank_text(
    ctx: RunContext[AgentToolsProtocol],
    *,
    request_context: ModelRequestContext,
    response: ModelResponse,
) -> ModelResponse:
    """Treat blank text as what it is: no output at all.

    An agent that answers through tools has nothing left to say once it has acted,
    and providers render that as an empty text part rather than a partless response.
    pydantic-ai recognizes only a partless (or thinking-only) response as "no
    actionable output" — the ``None`` outcome ``output_type`` allows — so a blank
    part would instead be met with a retry prompt, spend the refused output budget,
    and fail the turn. Dropping it also keeps the blank part out of history, where
    it replays as content:null.
    """
    response.parts = [
        part
        for part in response.parts
        if not (isinstance(part, TextPart) and not part.content.strip())
    ]
    return response


def _custom_tool_def_to_callable(tool_def: CustomToolDef) -> Callable[..., Any]:
    """Adapt a portable ``CustomToolDef`` to a native pydantic-ai tool callable.

    The wrapper takes ``RunContext`` (whose deps are the room's tools), then
    ``args: InputModel``, which pydantic-ai flattens into the tool's arguments
    and validates. ``invoke_validated_custom_tool`` runs the handler exactly as
    every other adapter does and records its declared effect on the room's turn.
    The validated instance is passed through as is, since a dump/re-validate
    round-trip would break aliased fields.
    """
    input_model, _ = tool_def

    async def native(ctx: RunContext[AgentToolsProtocol], args: Any) -> Any:
        return await invoke_validated_custom_tool(tool_def, args, turn=ctx.deps.turn)

    native.__name__ = get_custom_tool_name(input_model)
    native.__doc__ = input_model.__doc__ or native.__name__
    native.__annotations__ = {
        "ctx": RunContext[AgentToolsProtocol],
        "args": input_model,
        "return": str,
    }
    return native


def _takes_run_context(fn: Callable[..., Any]) -> bool:
    """Whether ``fn`` takes pydantic-ai's ``RunContext`` as its first parameter.

    Decides the registration path: ``agent.tool`` handles RunContext-first
    callables (including what ``_custom_tool_def_to_callable`` produces), while
    ``agent.tool_plain`` handles context-free ones. pydantic-ai injects an unannotated
    first parameter as context; a non-RunContext annotation is invalid on the
    former path.
    Annotations are resolved, so a caller using ``from __future__ import
    annotations`` is classified on the real type rather than the string.
    """
    first = next(iter(inspect.signature(fn).parameters), None)
    if first is None:
        return False
    try:
        annotation = get_type_hints(fn).get(first)
    except (NameError, TypeError):  # unresolvable annotation: treat as plain
        return False
    if annotation is None:
        return True
    return annotation is RunContext or get_origin(annotation) is RunContext


class PydanticAIAdapterConfig(BaseAdapterConfig):
    """Settings for a Pydantic AI agent.

    Attributes:
        model: Pydantic AI model string (e.g. ``"openai:gpt-6-luna"``,
            ``"anthropic:claude-sonnet-5-5"``). Since pydantic-ai 2.0 the bare
            ``openai:`` prefix routes to OpenAI's Responses API; use
            ``openai-chat:`` for Chat Completions. Leave it ``None`` only when
            the adapter is given a live ``llm``.
        system_prompt: Replaces the rendered Band system prompt entirely.
        custom_section: Extra instructions appended to the rendered prompt.
    """

    model: str | None = None
    system_prompt: str | None = None
    custom_section: str | None = None


class PydanticAIAdapter(SimpleAdapter[PydanticAIMessages]):
    """
    Pydantic AI adapter using SimpleAdapter pattern.

    Uses Pydantic AI's Agent for LLM interactions, built with the platform
    tools ``band.integrations.pydantic_ai.tools`` derives from the registry.

    Example:
        adapter = PydanticAIAdapter(
            PydanticAIAdapterConfig(
                model="openai:gpt-6-luna",
                custom_section="You are a helpful assistant.",
            )
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
        config: PydanticAIAdapterConfig | None = None,
        *,
        history_converter: PydanticAIHistoryConverter | None = None,
        additional_tools: list[Callable[..., Any] | CustomToolDef] | None = None,
        llm: Model | None = None,
        instrument: bool | InstrumentationSettings | None = None,
        **features: Unpack[FeatureKwargs],
    ):
        """
        Initialize the Pydantic AI adapter.

        Args:
            config: Model name and prompt settings; see
                :class:`PydanticAIAdapterConfig`.
            history_converter: Optional custom history converter
            additional_tools: Optional list of PydanticAI-compatible tool functions
                and/or portable ``CustomToolDef`` (InputModel, handler) tuples.
                Each function should follow PydanticAI's tool signature:
                `def my_tool(ctx: RunContext[AgentToolsProtocol], arg1: str, ...) -> T`
                and is registered via agent.tool() alongside platform tools. A
                context-free callable (no leading ``RunContext``) goes to
                agent.tool_plain() instead — pydantic-ai rejects it on the other path.
            llm: A constructed pydantic-ai ``Model``, used instead of
                ``config.model``. Exactly one of the two must be set.
            instrument: OpenTelemetry instrumentation for the pydantic-ai agent.
                ``None`` (default) inherits whatever ``Agent.instrument_all()`` the
                host set, ``False`` opts this agent out of it, ``True`` enables
                pydantic-ai's defaults, and an ``InstrumentationSettings`` customizes
                them (for example a specific ``tracer_provider``). Band never creates
                a provider or exporter — the host owns the telemetry pipeline; see
                ``examples/opentelemetry/``.
            **features: emit, capabilities, include_tools, exclude_tools,
                include_categories -- see FeatureKwargs.
        """
        super().__init__(
            history_converter=history_converter or PydanticAIHistoryConverter(),
            **features,
        )

        self.config = config or PydanticAIAdapterConfig()
        model = llm or self.config.model
        if model is None or (llm is not None and self.config.model is not None):
            raise ValueError("Set exactly one of config.model or llm")
        self._model: Model | str = model
        self._instrument = instrument
        self._system_prompt: str | None = None

        self._agent: Agent[AgentToolsProtocol, str | None] | None = None
        # Conversation history per room (Pydantic AI is stateless, we maintain state)
        self._message_history: dict[str, list] = {}
        # Custom tools: accept both native callables and the portable CustomToolDef
        # (InputModel, handler) form the other adapters take — tuples are converted to
        # native pydantic-ai callables; plain callables pass through unchanged.
        self._custom_tools: list[Callable[..., Any]] = [
            _custom_tool_def_to_callable(tool) if isinstance(tool, tuple) else tool
            for tool in (additional_tools or [])
        ]
        # Effects the native callables declared; a converted CustomToolDef
        # declares none, since invoke_validated_custom_tool records its effect.
        self._custom_effects = declared_effects(
            (fn.__name__, fn) for fn in self._custom_tools
        )

    # --- Adapted from BandPydanticAgent._on_started ---
    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Create the Pydantic AI agent after metadata is fetched."""
        await super().on_started(agent_name, agent_description)
        self._agent = self._create_agent()
        logger.info("Pydantic AI adapter started for agent: %s", agent_name)

    def _create_agent(self) -> Agent[AgentToolsProtocol, str | None]:
        """Create the Pydantic AI Agent: prompt, run policy, and tools."""
        system = self.config.system_prompt or render_system_prompt(
            agent_name=self.agent_name,
            agent_description=self.agent_description or "An AI assistant",
            custom_section=self.config.custom_section or "",
            features=self.features,
        )
        self._system_prompt = system

        agent: Agent[AgentToolsProtocol, str | None] = Agent(
            self._model,
            # Pass the rendered prompt as `instructions`, not `system_prompt`.
            # pydantic-ai materializes `system_prompt` as a single SystemPromptPart
            # only on the first request, after which it ages into buried history;
            # by the time the model composes the post-tool reply it sits beneath the
            # user prompt, tool call, and tool return, so a custom rule that must ride
            # *every* reply (e.g. a required marker word) gets dropped. `instructions`
            # are re-attached to each ModelRequest — including the post-tool-return
            # request that generates the reply — mirroring how the anthropic adapter
            # re-sends `system=` on every call, so the contract stays in force.
            instructions=system,
            deps_type=AgentToolsProtocol,
            # `str | None`: this agent replies *through* tools, so once it has acted
            # it has nothing left to say and answers with an empty (or thinking-only)
            # response. Allowing `None` makes that a valid outcome — pydantic-ai ends
            # the run instead of sending a retry prompt asking it to "return text or
            # call a tool", which an agent told to answer only through tools obliges
            # by calling one, re-posting the reply to the room once per attempt.
            # (Plain `None` is rejected: at least one non-`None` output type is
            # required.)
            output_type=str | None,
            # Two budgets, deliberately different — a bare int would set both.
            #
            # tools=3: one retry is too tight for a small model, which occasionally
            # needs another attempt to emit a valid tool call (e.g.
            # band_create_chatroom) before pydantic-ai gives up.
            #
            # output=0: with `None` allowed the ordinary end-of-turn response no
            # longer spends this budget, so what is left to retry is a response the
            # model cannot fix by trying again — and every attempt risks the extra
            # room post described above. The resulting UnexpectedModelBehavior is
            # handled where the run is driven.
            retries={"tools": 3, "output": 0},
            capabilities=[
                Hooks(
                    before_model_request=_finish_explicit_decline,
                    after_node_run=_finish_explicit_decline_after_tools,
                ),
                # Strip content:null responses on every request, including mid-run
                # ones the storage filter can't reach (see the function docstring).
                ProcessHistory(_drop_non_replayable_messages),
                Hooks(
                    before_model_request=_before_model_request,
                    after_model_request=_after_model_request,
                    model_request_error=_model_request_error,
                ),
            ],
            # The built-in Band surface, selected and built from the tool
            # registry. Custom tools keep their own registration paths below.
            tools=build_band_pydantic_ai_tools(self.features),
        )

        # Instrumentation is a property, not a constructor argument, so it is
        # assigned rather than passed. Always assigned: the tri-state is meaningful
        # end to end — None is pydantic-ai's own "inherit Agent.instrument_all()",
        # which is exactly what a caller who passed nothing wants.
        agent.instrument = self._instrument

        # Register custom tools (user-provided PydanticAI-compatible functions) on
        # the path their signature calls for — pydantic-ai keeps the two apart.
        for custom_tool in self._custom_tools:
            if _takes_run_context(custom_tool):
                agent.tool(custom_tool)
            else:
                agent.tool_plain(custom_tool)
            logger.debug("Registered custom tool: %s", custom_tool.__name__)

        return agent

    # --- Adapted from BandPydanticAgent._handle_message ---
    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: PydanticAIMessages,  # Already converted by SimpleAdapter
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        """Handle incoming platform message."""
        if self._agent is None:
            # Safety: create agent if not yet created (should be done in on_started)
            self._agent = self._create_agent()

        # Initialize message history for this room on first message
        # Note: history is already converted by SimpleAdapter via history_converter
        if is_session_bootstrap:
            if history:
                self._message_history[room_id] = list(history)
                logger.debug(
                    "Room %s: rehydrated %s message(s) from platform history",
                    room_id,
                    len(history),
                )
            else:
                self._message_history[room_id] = []
        elif room_id not in self._message_history:
            # Safety: ensure history exists even if not first message
            self._message_history[room_id] = []

        # Inject participants message if changed
        if participants_msg:
            self._message_history[room_id].append(
                ModelRequest(
                    parts=[UserPromptPart(content=f"[System]: {participants_msg}")]
                )
            )
            logger.debug("Room %s: Injected participant update into history", room_id)

        # Inject contacts message if present
        if contacts_msg:
            self._message_history[room_id].append(
                ModelRequest(
                    parts=[UserPromptPart(content=f"[System]: {contacts_msg}")]
                )
            )
            logger.debug("Room %s: Injected contacts broadcast into history", room_id)

        # Build user message with sender prefix
        user_message = msg.format_for_llm()

        logger.debug(
            "Room %s: Running Pydantic AI agent (history: %s msgs)",
            room_id,
            len(self._message_history[room_id]),
        )

        # Run agent with streaming to capture tool events.
        # pydantic-ai's result.usage is already summed across the run's model
        # calls, so it's set once (on the result event), not accumulated.
        turn_usage = TurnUsage()
        tool_call_count = 0
        tool_result_count = 0
        failed_tool_result_count = 0
        native_output_type: str | None = None
        native_output_chars = 0
        # Snapshot the prior messages' identities so the fallback usage path
        # (any run that raises) can sum only THIS run's responses: capture_run_messages()
        # records the passed message_history + the new turn, so summing the whole
        # list would double-count every prior turn. Identity (not a positional
        # slice) because pydantic-ai runs _clean_message_history() on the passed
        # history — merging adjacent same-type messages (e.g. the injected
        # participants + contacts requests) — so a length-based boundary would
        # slip; real API ModelResponses are never merged, so they keep identity.
        # Built only when usage emission is on: the gated fallback in the
        # finally is its sole consumer, and the set is O(history) per turn.
        usage_enabled = Emit.USAGE in self.features.emit
        prior_message_ids: set[int] = (
            {id(m) for m in self._message_history[room_id]} if usage_enabled else set()
        )
        # Capture the run's messages so a benign empty-final response — which raises
        # before the AgentRunResultEvent that normally records history — can still
        # persist the *full* turn (user prompt + the agent's tool calls/results), not
        # just the user prompt. This is pydantic-ai's documented hook for a run that
        # may raise; entered manually so the `finally` below can still read what the
        # failed run captured.
        capture_cm = capture_run_messages()
        captured = capture_cm.__enter__()
        trace_token = _native_turn.set((room_id, msg.id, monotonic()))
        event_count = 0
        try:
            _log_native_progress("run_started")
            # run_stream_events is an async context manager: it starts the run on the
            # first iteration and tears the background task down on exit.
            async with self._agent.run_stream_events(
                user_message,
                deps=tools,
                message_history=self._message_history[room_id],
            ) as events:
                async for event in events:
                    event_count += 1
                    # Logarithmic sampling bounds output during long token streams.
                    if event_count & (event_count - 1) == 0:
                        _log_native_progress("stream_progress", events=event_count)
                    if isinstance(event, FunctionToolCallEvent):
                        await report_tool_activity(tools, event.part.tool_name)
                        _log_native_progress(
                            "tool_started",
                            events=event_count,
                            tool=event.part.tool_name,
                        )
                        tool_call_count += 1
                        if Emit.TOOL_CALLS in self.features.emit:
                            try:
                                await tools.send_event(
                                    content=json.dumps(
                                        {
                                            ToolEventKey.NAME: event.part.tool_name,
                                            ToolEventKey.ARGS: redact_tool_call_args(
                                                event.part.tool_name,
                                                event.part.args_as_dict(),
                                            ),
                                            ToolEventKey.TOOL_CALL_ID: event.part.tool_call_id,
                                        }
                                    ),
                                    message_type="tool_call",
                                )
                            except Exception as e:  # noqa: BLE001 -- tool calls may raise any exception type; must surface to the LLM as an error string, not crash the turn
                                logger.warning("Failed to send tool_call event: %s", e)
                    elif isinstance(event, FunctionToolResultEvent):
                        await end_tool_activity(tools)
                        tool_succeeded = isinstance(
                            event.part, ToolReturnPart
                        ) and not is_failed_tool_output(event.part.content)
                        _log_native_progress(
                            "tool_succeeded" if tool_succeeded else "tool_failed",
                            events=event_count,
                            tool=event.part.tool_name,
                        )
                        tool_result_count += 1
                        if isinstance(event.part, RetryPromptPart) or (
                            isinstance(event.part, ToolReturnPart)
                            and (
                                event.part.outcome != "success"
                                or is_failed_tool_output(event.part.content)
                            )
                        ):
                            failed_tool_result_count += 1
                        # Native custom tools run outside execute_custom_tool, so
                        # their declared effect is recorded here.
                        if (
                            tool_succeeded
                            and isinstance(event.part, ToolReturnPart)
                            and (
                                effect := self._custom_effects.get(event.part.tool_name)
                            )
                        ):
                            tools.turn.record(effect)
                        if Emit.TOOL_CALLS in self.features.emit:
                            output = event.part.content
                            if (
                                isinstance(output, list)
                                and output
                                and all(
                                    isinstance(item, BinaryContent) for item in output
                                )
                            ):
                                # str() on BinaryContent embeds its raw `data`
                                # bytes -- band_read_room_file's image result
                                # would otherwise dump the full file into this
                                # event instead of a bounded placeholder.
                                output = image_block_placeholder(len(output))
                            try:
                                await tools.send_event(
                                    content=json.dumps(
                                        {
                                            ToolEventKey.NAME: event.part.tool_name,
                                            ToolEventKey.OUTPUT: str(output),
                                            ToolEventKey.TOOL_CALL_ID: event.tool_call_id,
                                        }
                                    ),
                                    message_type="tool_result",
                                )
                            except Exception as e:  # noqa: BLE001 -- tool calls may raise any exception type; must surface to the LLM as an error string, not crash the turn
                                logger.warning(
                                    "Failed to send tool_result event: %s", e
                                )
                    elif isinstance(event, AgentRunResultEvent):
                        _log_native_progress("result_received", events=event_count)
                        native_output_type = type(event.result.output).__name__
                        if isinstance(event.result.output, str):
                            native_output_chars = len(event.result.output)
                        turn_usage = self._usage_from_result(event.result)
                        # Keep native run history, but drop responses that replay as
                        # content:null (e.g. thinking-only) — providers reject them
                        # on the next request.
                        run_messages = list(event.result.all_messages())
                        self._message_history[room_id] = _drop_non_replayable_messages(
                            run_messages
                        )
                        dropped = len(run_messages) - len(
                            self._message_history[room_id]
                        )
                        if dropped:
                            logger.debug(
                                "Room %s: dropped %s content:null response(s) from "
                                "history",
                                room_id,
                                dropped,
                            )
                _log_native_progress("iterator_completed", events=event_count)
            _log_native_progress("run_completed", events=event_count)
        except asyncio.CancelledError as error:
            _log_native_progress("run_cancelled", events=event_count, error=error)
            raise
        except Exception as e:
            _log_native_progress("run_error", events=event_count, error=e)
            # A turn that already did its work must not fail over the reply the model
            # owes pydantic-ai. Allowing `None` — and normalizing blank text into it
            # — ends the ordinary nothing-left-to-say response cleanly, but some
            # other response the run cannot turn into output can still spend the
            # refused output budget. Once the turn is complete (a
            # band_send_message reply, a band_store_memory, ...) the work already went
            # out, so that exhaustion is benign — swallow it. Every other exception —
            # a different UnexpectedModelBehavior, or any other type now that this
            # catches broadly for send_failure reporting — still surfaces and
            # propagates. Unlike the crewai adapter, which cannot tell a genuine
            # failure apart from the empty completion that ends its every turn,
            # pydantic-ai raises the exhausted-retries case as its own distinct type,
            # so the isinstance check (not just the message match) is load-bearing.
            if (
                tools.turn.complete
                and isinstance(e, UnexpectedModelBehavior)
                and _is_output_retries_exhausted(e)
            ):
                logger.warning(
                    "Room %s: Pydantic AI exhausted its output retries after "
                    "the turn was already complete; treating as "
                    "non-fatal: %s",
                    room_id,
                    e,
                )
                # No AgentRunResultEvent fired, so history was never recorded this
                # turn — and it reloads from the platform only on bootstrap. Persist
                # the full turn from the captured run messages (user prompt + the
                # agent's tool calls/results, replay-filtered) so a later "what did
                # you just say?" has context; fall back to just the user prompt if
                # nothing was captured.
                replayable = _drop_non_replayable_messages(list(captured))
                self._message_history[room_id] = replayable or [
                    *self._message_history[room_id],
                    ModelRequest(parts=[UserPromptPart(content=user_message)]),
                ]
                return
            logger.error(
                "Room %s: Pydantic AI turn failed (%s)", room_id, type(e).__name__
            )
            await tools.send_failure(
                AgentFailure(_PROVIDER, GENERIC_PROVIDER_FAILURE_MESSAGE)
            )
            raise
        finally:
            _native_turn.reset(trace_token)
            if native_output_type is not None and not tools.turn.complete:
                logger.warning(
                    "Room %s message %s: native run ended without a turn outcome "
                    "(output_type=%s, output_chars=%s, tool_calls=%s, "
                    "tool_results=%s, failed_tool_results=%s)",
                    room_id,
                    msg.id,
                    native_output_type,
                    native_output_chars,
                    tool_call_count,
                    tool_result_count,
                    failed_tool_result_count,
                )
            capture_cm.__exit__(None, None, None)
            # Single emit point for every exit. A run that raised (benign
            # empty-final or a hard failure) never saw the result event, so
            # fall back to summing only THIS run's captured responses by
            # identity (captured also holds the prior history). Feature-gated
            # here so the fallback's history scan never runs when usage
            # emission is off.
            if usage_enabled:
                if turn_usage.is_empty:
                    this_run = self._new_run_messages(captured, prior_message_ids)
                    turn_usage = self._usage_from_messages(this_run)
                await self.emit_usage(tools, turn_usage)

        logger.debug(
            "Room %s: Pydantic AI agent completed (history now has %s messages)",
            room_id,
            len(self._message_history[room_id]),
        )

    @staticmethod
    def _usage_from_usage_obj(usage: Any) -> TurnUsage:
        """Map a pydantic-ai usage object (RunUsage / RequestUsage) onto TurnUsage.

        Read defensively: both classes carry these fields, but a missing one costs
        the turn's usage report, never the turn.
        """

        def _int(name: str) -> int:
            value = getattr(usage, name, None)
            return value if isinstance(value, int) else 0

        return TurnUsage(
            input_tokens=_int("input_tokens"),
            output_tokens=_int("output_tokens"),
            cache_read_tokens=_int("cache_read_tokens"),
            cache_write_tokens=_int("cache_write_tokens"),
        )

    @staticmethod
    def _usage_from_result(result: Any) -> TurnUsage:
        """The run's total usage across all model requests (happy path).

        Usage is telemetry, so a shape change must not fail the turn — but it must
        not vanish silently either (pydantic-ai 2.x turned ``usage()`` into the
        ``usage`` property, and a silent catch would just report zeros).
        """
        try:
            usage = result.usage
        except Exception as e:  # pragma: no cover - defensive; usage is best-effort  # noqa: BLE001 -- tool calls may raise any exception type; must surface to the LLM as an error string, not crash the turn
            logger.warning("Could not read pydantic-ai run usage: %s", e)
            return TurnUsage()
        return PydanticAIAdapter._usage_from_usage_obj(usage)

    @staticmethod
    def _new_run_messages(
        captured: list[ModelMessage], prior_message_ids: set[int]
    ) -> list[ModelMessage]:
        """This run's messages: those in ``captured`` not in the prior history.

        Identity, not a positional boundary: pydantic-ai runs
        ``_clean_message_history`` on the passed history, merging adjacent
        same-type messages (e.g. the injected participants + contacts requests),
        which shifts positions and shortens the list — so a ``len(prior)`` slice
        would drop this turn's leading response(s). Real API ``ModelResponse``s
        are never merged, so they keep their identity and survive this filter;
        any newly-merged prior request lands here too but carries no usage, so the
        ``ModelResponse``-only sum in :meth:`_usage_from_messages` ignores it.
        """
        return [m for m in captured if id(m) not in prior_message_ids]

    @staticmethod
    def _usage_from_messages(messages: list[ModelMessage]) -> TurnUsage:
        """Sum per-response usage across the given run messages.

        The fallback for the benign empty-final-response path, where no
        ``AgentRunResultEvent`` fires (so ``result.usage`` is unavailable) yet
        the turn still spent tokens — each ``ModelResponse`` carries its own
        ``usage``. Pass only the *current run's* messages (the caller filters out
        the prior history by identity); summing the full captured list would
        double-count every prior turn.
        """
        total = TurnUsage()
        for message in messages:
            if isinstance(message, ModelResponse):
                total = total + PydanticAIAdapter._usage_from_usage_obj(message.usage)
        return total

    # --- Copied from BandPydanticAgent._cleanup_session ---
    async def on_cleanup(self, room_id: str) -> None:
        """Clean up message history when agent leaves a room."""
        if room_id in self._message_history:
            del self._message_history[room_id]
            logger.debug("Room %s: Cleaned up message history", room_id)
