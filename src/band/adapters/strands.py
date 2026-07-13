"""
Strands Agents adapter using SimpleAdapter pattern.

Modeled on the pydantic-ai adapter: the framework owns the agent loop, the
model object is injectable at the public ``Agent(model=...)`` seam, and Band
platform tools are registered as native framework tools.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, ClassVar

import httpx

try:
    from strands import Agent, ToolContext, tool
    from strands.hooks import HookProvider, HookRegistry
    from strands.hooks.events import AfterToolCallEvent, BeforeToolCallEvent
    from strands.models import Model
    from strands.types.tools import (
        AgentTool,
        ToolGenerator,
        ToolResult,
        ToolSpec,
        ToolUse,
    )
except ImportError as e:
    raise ImportError(
        "Strands Agents dependencies not installed. "
        "Install with: uv add band-sdk[strands]"
    ) from e

from band_rest.core.api_error import ApiError

from band.core.protocols import AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.types import (
    AdapterFeatures,
    Capability,
    Emit,
    PlatformMessage,
    TurnUsage,
)
from band.converters.strands import StrandsHistoryConverter, StrandsMessages
from band.runtime.custom_tools import (
    CustomToolDef,
    execute_custom_tool,
    get_custom_tool_name,
    is_marked_terminal,
)
from band.runtime.prompts import render_system_prompt
from band.runtime.tools import (
    band_tool_errored,
    get_tool_description,
    is_terminal_success,
)

logger = logging.getLogger(__name__)

# invocation_state key carrying the current turn's AgentToolsProtocol handle.
# Strands threads invocation_state from Agent.invoke_async(...) into every tool
# body (via ToolContext) and hook event, which is what lets the tool functions
# be built once while each turn binds its own tools handle.
_BAND_TOOLS_STATE_KEY = "band_tools"


def _tools_from_context(tool_context: ToolContext) -> AgentToolsProtocol:
    """The current turn's platform tools handle, bound via invocation_state."""
    handle = tool_context.invocation_state.get(_BAND_TOOLS_STATE_KEY)
    if handle is None:
        raise RuntimeError(
            "Band tools handle missing from invocation_state; platform tools "
            "must be invoked through StrandsAdapter.on_message"
        )
    return handle


def _result_text(result: ToolResult) -> str:
    """Flatten a ToolResult's content blocks to text for error detection/reporting."""
    parts: list[str] = []
    for block in result.get("content", []):
        if "text" in block:
            parts.append(block["text"])
        elif "json" in block:
            try:
                parts.append(json.dumps(block["json"]))
            except (TypeError, ValueError):
                parts.append(str(block["json"]))
    return "\n".join(parts)


class _CustomToolBridge(AgentTool):
    """A portable ``CustomToolDef`` (InputModel, handler) as a native Strands tool.

    The tool spec is derived from the Pydantic input model (same name/schema the
    other adapters expose), and dispatch validates + executes via
    ``execute_custom_tool`` so the argument-validation contract matches the
    tuple form everywhere else.
    """

    def __init__(self, tool_def: CustomToolDef):
        super().__init__()
        self._tool_def = tool_def
        input_model, _ = tool_def
        schema = input_model.model_json_schema()
        schema.pop("title", None)
        self._name = get_custom_tool_name(input_model)
        self._spec: ToolSpec = {
            "name": self._name,
            "description": input_model.__doc__ or self._name,
            "inputSchema": {"json": schema},
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

    async def stream(
        self, tool_use: ToolUse, invocation_state: dict[str, Any], **kwargs: Any
    ) -> ToolGenerator:
        try:
            result = await execute_custom_tool(self._tool_def, tool_use["input"] or {})
            yield {
                "toolUseId": tool_use["toolUseId"],
                "status": "success",
                "content": [{"text": str(result)}],
            }
        except Exception as e:
            yield {
                "toolUseId": tool_use["toolUseId"],
                "status": "error",
                "content": [{"text": f"Error executing tool '{self._name}': {e}"}],
            }


class _BandTurnHooks(HookProvider):
    """Per-turn hook provider: L6 execution events + terminal-action tracking.

    A fresh instance is registered on each turn's Agent, so per-turn state
    (whether a terminal Band action fired) needs no cross-turn bookkeeping.
    Event emission is best-effort and never crashes the turn.
    """

    def __init__(
        self,
        tools: AgentToolsProtocol,
        *,
        emit_execution: bool,
        custom_terminal_names: frozenset[str],
    ):
        self._tools = tools
        self._emit_execution = emit_execution
        self._custom_terminal_names = custom_terminal_names
        self.terminal_fired = False

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self._on_before_tool)
        registry.add_callback(AfterToolCallEvent, self._on_after_tool)

    async def _on_before_tool(self, event: BeforeToolCallEvent) -> None:
        if not self._emit_execution:
            return
        try:
            await self._tools.send_event(
                content=json.dumps(
                    {
                        "name": event.tool_use["name"],
                        "args": event.tool_use["input"],
                        "tool_call_id": event.tool_use["toolUseId"],
                    }
                ),
                message_type="tool_call",
            )
        except Exception as e:
            logger.warning("Failed to send tool_call event: %s", e)

    async def _on_after_tool(self, event: AfterToolCallEvent) -> None:
        # Custom tools count as terminal only if they opted in (band_terminal);
        # undeclared customs fail loud. A failed Band tool (its wrapper returns
        # an "Error " string, or Strands recorded an error status) is not terminal.
        name = event.tool_use["name"]
        output = _result_text(event.result)
        succeeded = event.result.get("status") == "success" and not band_tool_errored(
            name, output
        )
        if is_terminal_success(
            name,
            succeeded=succeeded,
            custom_terminal=name in self._custom_terminal_names,
        ):
            self.terminal_fired = True
        if not self._emit_execution:
            return
        try:
            await self._tools.send_event(
                content=json.dumps(
                    {
                        "name": name,
                        "output": output,
                        "tool_call_id": event.tool_use["toolUseId"],
                    }
                ),
                message_type="tool_result",
            )
        except Exception as e:
            logger.warning("Failed to send tool_result event: %s", e)


class StrandsAdapter(SimpleAdapter[StrandsMessages]):
    """
    Strands Agents adapter using SimpleAdapter pattern.

    Uses a Strands Agent for LLM interactions, with platform tools registered
    as native Strands ``@tool`` functions.

    Example:
        from strands.models.openai import OpenAIModel

        adapter = StrandsAdapter(
            model=OpenAIModel(model_id="gpt-5.4-mini"),
            custom_section="You are a helpful assistant.",
        )
        agent = Agent.create(adapter=adapter, agent_id="...", api_key="...")
        await agent.run()
    """

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset({Emit.EXECUTION, Emit.USAGE})
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.MEMORY, Capability.CONTACTS}
    )

    def __init__(
        self,
        model: str | Model,
        system_prompt: str | None = None,
        custom_section: str | None = None,
        history_converter: StrandsHistoryConverter | None = None,
        additional_tools: list[Callable[..., Any] | CustomToolDef] | None = None,
        features: AdapterFeatures | None = None,
    ):
        """
        Initialize the Strands adapter.

        Args:
            model: A Strands ``Model`` instance (e.g.
                ``strands.models.openai.OpenAIModel(model_id="gpt-5.4-mini")``).
                A plain string is passed through to Strands, which treats it as
                a **Bedrock** model id (Strands has no provider-prefix shorthand).
            system_prompt: Optional custom system prompt (overrides default)
            custom_section: Optional custom section added to default system prompt
            history_converter: Optional custom history converter
            additional_tools: Optional list of Strands-compatible tools (plain
                callables or ``@strands.tool``-decorated functions) and/or
                portable ``CustomToolDef`` (InputModel, handler) tuples.
            features: Shared adapter feature settings (capabilities, emit, tool filters).
        """
        super().__init__(
            history_converter=history_converter or StrandsHistoryConverter(),
            features=features,
        )

        self.model = model
        self.system_prompt = system_prompt
        self.custom_section = custom_section
        self._system_prompt: str | None = None

        # Platform + custom tools, built once in on_started (tool bodies bind the
        # per-turn tools handle via invocation_state, so they carry no turn state).
        self._strands_tools: list[Any] = []

        # Conversation history per room (Converse-shaped Messages). Band owns
        # this state; Strands sessions/conversation managers are not used for
        # cross-turn persistence.
        self._message_history: dict[str, StrandsMessages] = {}

        # Custom tools: accept native Strands forms and the portable CustomToolDef
        # (InputModel, handler) tuple the other adapters take — tuples are bridged
        # to native Strands AgentTools; callables pass through unchanged.
        self._custom_tools: list[Any] = [
            _CustomToolBridge(t) if isinstance(t, tuple) else t
            for t in (additional_tools or [])
        ]
        # Custom tools that opt in as terminal actions (band_terminal=True on the
        # handler/function). Only these let a turn with no Band-tool action count
        # as productive; an undeclared custom tool does not (fail-loud — see
        # is_terminal_success).
        terminal_names: set[str] = set()
        for raw, converted in zip(additional_tools or [], self._custom_tools):
            handler = raw[1] if isinstance(raw, tuple) else raw
            if is_marked_terminal(handler):
                terminal_names.add(
                    converted.tool_name
                    if isinstance(converted, AgentTool)
                    else getattr(converted, "__name__", str(converted))
                )
        self._custom_terminal_names: frozenset[str] = frozenset(terminal_names)

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Render the system prompt and build the tool set after metadata is fetched."""
        await super().on_started(agent_name, agent_description)
        self._system_prompt = self.system_prompt or render_system_prompt(
            agent_name=self.agent_name,
            agent_description=self.agent_description or "An AI assistant",
            custom_section=self.custom_section or "",
            features=self.features,
        )
        self._strands_tools = self._build_platform_tools() + self._custom_tools
        logger.info("Strands adapter started for agent: %s", agent_name)

    def _build_agent(self, messages: StrandsMessages, hooks: _BandTurnHooks) -> Agent:
        """Construct the Strands Agent for one turn, over the room's history.

        A Strands Agent is stateful (it owns ``messages`` and per-agent metrics)
        and raises on concurrent invocation, while the Band runtime runs one
        asyncio task per room — so a single shared Agent would break under
        multi-room concurrency. Instead the heavy pieces (prompt, tool set) are
        built once in on_started and a lightweight Agent shell is constructed
        per turn over the room's messages (mirrors the google_adk adapter's
        fresh-runner-per-message pattern). Bonus: a fresh Agent has fresh
        metrics, so accumulated usage is exactly this turn's usage.
        """
        return Agent(
            model=self.model,
            messages=messages,
            tools=self._strands_tools,
            system_prompt=self._system_prompt,
            hooks=[hooks],
            callback_handler=None,
            name=self.agent_name or None,
        )

    def _build_platform_tools(self) -> list[Any]:
        """Build the Band platform tools as native Strands ``@tool`` functions.

        Descriptions come from the centralized tool definitions. All tools catch
        exceptions and return "Error ..." strings so the LLM can see failures
        (and so terminal detection can tell a failed Band tool from a success).
        """

        def band(name: str, fn: Callable[..., Any]) -> Any:
            return tool(description=get_tool_description(name), context=True)(fn)

        async def band_send_message(
            content: str,
            mentions: list[str],
            tool_context: ToolContext,
        ) -> Any:
            try:
                return await _tools_from_context(tool_context).send_message(
                    content, mentions
                )
            except Exception as e:
                return f"Error sending message: {e}"

        async def band_send_event(
            content: str,
            message_type: str,
            tool_context: ToolContext,
            metadata: dict[str, Any] | None = None,
        ) -> Any:
            try:
                return await _tools_from_context(tool_context).send_event(
                    content, message_type, metadata
                )
            except Exception as e:
                return f"Error sending event: {e}"

        async def band_add_participant(
            identifier: str,
            tool_context: ToolContext,
            role: str = "member",
        ) -> Any:
            try:
                return await _tools_from_context(tool_context).add_participant(
                    identifier, role
                )
            except Exception as e:
                return f"Error adding participant '{identifier}': {e}"

        async def band_remove_participant(
            identifier: str,
            tool_context: ToolContext,
        ) -> Any:
            try:
                return await _tools_from_context(tool_context).remove_participant(
                    identifier
                )
            except Exception as e:
                return f"Error removing participant '{identifier}': {e}"

        async def band_lookup_peers(
            tool_context: ToolContext,
            page: int = 1,
            page_size: int = 50,
        ) -> Any:
            try:
                return await _tools_from_context(tool_context).lookup_peers(
                    page, page_size
                )
            except Exception as e:
                return f"Error looking up peers: {e}"

        async def band_get_participants(tool_context: ToolContext) -> Any:
            try:
                return await _tools_from_context(tool_context).get_participants()
            except Exception as e:
                return f"Error getting participants: {e}"

        async def band_create_chatroom(
            tool_context: ToolContext,
            task_id: str | None = None,
        ) -> Any:
            try:
                return await _tools_from_context(tool_context).create_chatroom(task_id)
            except Exception as e:
                return f"Error creating chatroom (task_id={task_id}): {e}"

        strands_tools = [
            band("band_send_message", band_send_message),
            band("band_send_event", band_send_event),
            band("band_add_participant", band_add_participant),
            band("band_remove_participant", band_remove_participant),
            band("band_lookup_peers", band_lookup_peers),
            band("band_get_participants", band_get_participants),
            band("band_create_chatroom", band_create_chatroom),
        ]

        # Contact management tools (opt-in via Capability.CONTACTS)
        if Capability.CONTACTS in self.features.capabilities:

            async def band_list_contacts(
                tool_context: ToolContext,
                page: int = 1,
                page_size: int = 50,
            ) -> Any:
                try:
                    return await _tools_from_context(tool_context).list_contacts(
                        page, page_size
                    )
                except Exception as e:
                    return f"Error listing contacts: {e}"

            async def band_add_contact(
                handle: str,
                tool_context: ToolContext,
                message: str | None = None,
            ) -> Any:
                try:
                    return await _tools_from_context(tool_context).add_contact(
                        handle, message
                    )
                except Exception as e:
                    return f"Error adding contact '{handle}': {e}"

            async def band_remove_contact(
                tool_context: ToolContext,
                handle: str | None = None,
                contact_id: str | None = None,
            ) -> Any:
                try:
                    return await _tools_from_context(tool_context).remove_contact(
                        handle, contact_id
                    )
                except Exception as e:
                    return f"Error removing contact: {e}"

            async def band_list_contact_requests(
                tool_context: ToolContext,
                page: int = 1,
                page_size: int = 50,
                sent_status: str = "pending",
            ) -> Any:
                try:
                    return await _tools_from_context(
                        tool_context
                    ).list_contact_requests(page, page_size, sent_status)
                except Exception as e:
                    return f"Error listing contact requests: {e}"

            async def band_respond_contact_request(
                action: str,
                tool_context: ToolContext,
                handle: str | None = None,
                request_id: str | None = None,
            ) -> Any:
                tools = _tools_from_context(tool_context)
                try:
                    return await tools.respond_contact_request(
                        action, handle, request_id
                    )
                except Exception as e:
                    error_msg = f"Error responding to contact request: {e}"
                    # Auto-send error event so it's visible in the room
                    try:
                        await tools.send_event(error_msg, "error")
                    except Exception:
                        pass  # Don't fail if error reporting fails
                    return error_msg

            strands_tools.extend(
                [
                    band("band_list_contacts", band_list_contacts),
                    band("band_add_contact", band_add_contact),
                    band("band_remove_contact", band_remove_contact),
                    band("band_list_contact_requests", band_list_contact_requests),
                    band("band_respond_contact_request", band_respond_contact_request),
                ]
            )

        # Memory management tools (enterprise only - opt-in)
        if Capability.MEMORY in self.features.capabilities:

            async def band_list_memories(
                tool_context: ToolContext,
                subject_id: str | None = None,
                scope: str | None = None,
                system: str | None = None,
                type: str | None = None,
                segment: str | None = None,
                content_query: str | None = None,
                page_size: int = 50,
                status: str | None = None,
            ) -> Any:
                try:
                    return await _tools_from_context(tool_context).list_memories(
                        subject_id=subject_id,
                        scope=scope,
                        system=system,
                        type=type,
                        segment=segment,
                        content_query=content_query,
                        page_size=page_size,
                        status=status,
                    )
                except Exception as e:
                    return f"Error listing memories: {e}"

            async def band_store_memory(
                content: str,
                system: str,
                type: str,
                segment: str,
                thought: str,
                scope: str,
                tool_context: ToolContext,
                subject_id: str | None = None,
                metadata: dict[str, Any] | None = None,
            ) -> Any:
                try:
                    return await _tools_from_context(tool_context).store_memory(
                        content=content,
                        system=system,
                        type=type,
                        segment=segment,
                        thought=thought,
                        scope=scope,
                        subject_id=subject_id,
                        metadata=metadata,
                    )
                except Exception as e:
                    return f"Error storing memory: {e}"

            async def band_get_memory(
                memory_id: str,
                tool_context: ToolContext,
            ) -> Any:
                try:
                    return await _tools_from_context(tool_context).get_memory(memory_id)
                except Exception as e:
                    return f"Error getting memory: {e}"

            async def band_supersede_memory(
                memory_id: str,
                tool_context: ToolContext,
            ) -> Any:
                try:
                    return await _tools_from_context(tool_context).supersede_memory(
                        memory_id
                    )
                except Exception as e:
                    return f"Error superseding memory: {e}"

            async def band_archive_memory(
                memory_id: str,
                tool_context: ToolContext,
            ) -> Any:
                try:
                    return await _tools_from_context(tool_context).archive_memory(
                        memory_id
                    )
                except Exception as e:
                    return f"Error archiving memory: {e}"

            strands_tools.extend(
                [
                    band("band_list_memories", band_list_memories),
                    band("band_store_memory", band_store_memory),
                    band("band_get_memory", band_get_memory),
                    band("band_supersede_memory", band_supersede_memory),
                    band("band_archive_memory", band_archive_memory),
                ]
            )

        return strands_tools

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: StrandsMessages,  # Already converted by SimpleAdapter
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        """Handle incoming platform message."""
        if not self._strands_tools:
            # Safety: build tools if not yet built (should be done in on_started)
            self._strands_tools = self._build_platform_tools() + self._custom_tools

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
                {"role": "user", "content": [{"text": f"[System]: {participants_msg}"}]}
            )
            logger.debug("Room %s: Injected participant update into history", room_id)

        # Inject contacts message if present
        if contacts_msg:
            self._message_history[room_id].append(
                {"role": "user", "content": [{"text": f"[System]: {contacts_msg}"}]}
            )
            logger.debug("Room %s: Injected contacts broadcast into history", room_id)

        # Build user message with sender prefix
        user_message = msg.format_for_llm()

        logger.debug(
            "Room %s: Running Strands agent (history: %s msgs, prompt: %s...)",
            room_id,
            len(self._message_history[room_id]),
            user_message[:80],
        )

        turn_hooks = _BandTurnHooks(
            tools,
            emit_execution=Emit.EXECUTION in self.features.emit,
            custom_terminal_names=self._custom_terminal_names,
        )
        agent = self._build_agent(self._message_history[room_id], turn_hooks)
        try:
            await agent.invoke_async(
                user_message,
                invocation_state={_BAND_TOOLS_STATE_KEY: tools},
            )
        finally:
            # Persist whatever the run recorded (also on a failed turn, so the
            # room keeps the user prompt + any completed tool work), and emit
            # this turn's usage: the per-turn Agent has per-turn metrics, so
            # accumulated_usage is exactly this run's total across its model
            # calls. emit_usage itself gates on Emit.USAGE and never raises.
            self._message_history[room_id] = agent.messages
            await self.emit_usage(tools, self._usage_from_agent(agent))

        # A clean run with no terminal work means the model answered in plain text
        # without calling band_send_message — a silently dropped reply. Surface it
        # as an error (mirrors the pydantic-ai/crewai adapters).
        if not turn_hooks.terminal_fired:
            await self._report_error(
                tools,
                "Strands agent completed without sending a Band message. This "
                "usually means the agent returned a final answer as plain text "
                "instead of using the band_send_message tool.",
            )

        logger.debug(
            "Room %s: Strands agent completed (history now has %s messages)",
            room_id,
            len(self._message_history[room_id]),
        )

    @staticmethod
    def _usage_from_agent(agent: Agent) -> TurnUsage:
        """Map the turn's accumulated token usage onto TurnUsage.

        Strands accumulates usage on the agent's EventLoopMetrics across all of
        the run's model calls; reading it from the agent (not the AgentResult)
        also covers turns that raised mid-run. totalTokens is derived
        (input + output) and deliberately not mapped.
        """
        try:
            usage = dict(agent.event_loop_metrics.accumulated_usage)
        except Exception:  # pragma: no cover - defensive; usage is best-effort
            return TurnUsage()
        return TurnUsage.from_mapping(
            usage,
            input="inputTokens",
            output="outputTokens",
            cache_read="cacheReadInputTokens",
            cache_write="cacheWriteInputTokens",
        )

    async def _report_error(self, tools: AgentToolsProtocol, error: str) -> None:
        """Send an error event to the room (best effort).

        Narrowed to the REST call's real failure modes (ApiError = HTTP status,
        httpx = transport) so a failed error-report never crashes the turn —
        while a real bug still raises.
        """
        try:
            await tools.send_event(content=f"Error: {error}", message_type="error")
        except (ApiError, httpx.HTTPError) as e:
            logger.warning("Failed to send error event: %s", e)

    async def on_cleanup(self, room_id: str) -> None:
        """Clean up message history when agent leaves a room."""
        if room_id in self._message_history:
            del self._message_history[room_id]
            logger.debug("Room %s: Cleaned up message history", room_id)
