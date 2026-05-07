"""LangGraph adapter."""

from __future__ import annotations

import json
import logging
import warnings
from collections import OrderedDict
from typing import ClassVar, TYPE_CHECKING, Any, Callable

from langgraph.pregel import Pregel

from thenvoi.core.exceptions import ThenvoiConfigError
from thenvoi.core.protocols import AgentToolsProtocol
from thenvoi.core.simple_adapter import SimpleAdapter
from thenvoi.core.types import AdapterFeatures, Capability, Emit, PlatformMessage
from thenvoi.converters.langchain import LangChainHistoryConverter, LangChainMessages
from thenvoi.runtime.prompts import render_system_prompt

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from langgraph.checkpoint.base import BaseCheckpointSaver

logger = logging.getLogger(__name__)


_BOOTSTRAP_TRACKING_WARN_THRESHOLD = 1000


class LangGraphAdapter(SimpleAdapter[LangChainMessages]):
    """
    LangGraph adapter using SimpleAdapter pattern.

    Two usage patterns:

    1. Simple (recommended for most users):
        adapter = LangGraphAdapter(
            llm=ChatOpenAI(model="gpt-4o"),
            checkpointer=InMemorySaver(),
            custom_section="You are a helpful assistant.",
        )

    2. Advanced (custom graph):
        def graph_factory(tools):
            return create_agent(llm, tools, checkpointer=checkpointer)

        adapter = LangGraphAdapter(graph_factory=graph_factory)

    System prompt:
        The adapter renders a system prompt from ``prompt_template`` /
        ``custom_section`` / agent metadata in :meth:`on_started`, then
        prepends it as the first ``("system", ...)`` message on session
        bootstrap. The LangGraph checkpointer carries it forward across
        turns, so every model call sees exactly one SystemMessage.

        - Simple pattern: nothing special to do; ``create_agent`` reads
          ``state["messages"]`` directly.
        - Advanced pattern (``graph=`` / ``graph_factory=``): your graph
          should also read ``state["messages"]`` (or whatever your state
          schema names them). See
          ``examples/langgraph/09_research_ops_orchestrator.py``.

    Example:
        from langchain_openai import ChatOpenAI
        from langgraph.checkpoint.memory import InMemorySaver

        adapter = LangGraphAdapter(
            llm=ChatOpenAI(model="gpt-4o"),
            checkpointer=InMemorySaver(),
        )
        agent = Agent.create(adapter=adapter, agent_id="...", api_key="...")
        await agent.run()
    """

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset({Emit.EXECUTION})
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.MEMORY, Capability.CONTACTS}
    )

    def __init__(
        self,
        # Simple pattern: just provide llm and checkpointer
        llm: "BaseChatModel | None" = None,
        checkpointer: "BaseCheckpointSaver | None" = None,
        # Advanced pattern: provide a graph factory or static graph
        graph_factory: Callable[[list[Any]], Pregel] | None = None,
        graph: Pregel | None = None,
        # Common options
        prompt_template: str = "default",
        custom_section: str = "",
        additional_tools: list[Any] | None = None,
        enable_memory_tools: bool = False,
        enable_execution_reporting: bool = False,
        history_converter: LangChainHistoryConverter | None = None,
        recursion_limit: int = 50,
        features: AdapterFeatures | None = None,
    ):
        # --- Deprecation shim: boolean → features migration ---
        if (enable_memory_tools or enable_execution_reporting) and features is not None:
            raise ThenvoiConfigError(
                "Cannot pass both 'features' and legacy boolean params "
                "(enable_memory_tools, enable_execution_reporting)."
            )

        if enable_memory_tools or enable_execution_reporting:
            warnings.warn(
                "enable_memory_tools/enable_execution_reporting are deprecated. "
                "Use features=AdapterFeatures(...) instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            capabilities = (
                frozenset({Capability.MEMORY}) if enable_memory_tools else frozenset()
            )
            emit = (
                frozenset({Emit.EXECUTION})
                if enable_execution_reporting
                else frozenset()
            )
            features = AdapterFeatures(capabilities=capabilities, emit=emit)

        # Use default LangChain converter if not provided
        super().__init__(
            history_converter=history_converter or LangChainHistoryConverter(),
            features=features,
        )

        # Simple pattern: build a graph_factory that delegates to create_agent.
        # We do NOT pass system_prompt= here; the adapter prepends a single
        # ("system", ...) message on bootstrap and the checkpointer carries it
        # forward, matching the pattern used by every other Band adapter.
        if llm is not None and graph_factory is None and graph is None:
            from langchain.agents import create_agent

            additional = additional_tools or []

            def factory(band_tools: list[Any]) -> Pregel:
                all_tools = band_tools + additional
                return create_agent(
                    model=llm,
                    tools=all_tools,
                    checkpointer=checkpointer,
                )

            graph_factory = factory
            # Clear additional_tools since they're now baked into the factory
            additional_tools = []

        if not graph_factory and not graph:
            raise ValueError(
                "Must provide either llm (simple pattern) or graph_factory/graph (advanced pattern)"
            )

        self.graph_factory = graph_factory
        self._static_graph = graph
        self.prompt_template = prompt_template
        self.custom_section = custom_section
        self.additional_tools = additional_tools or []
        self.recursion_limit = recursion_limit
        self._system_prompt: str = ""
        # Track rooms that have already had hydrated history pushed in, so
        # reconnects that re-deliver bootstrap don't duplicate messages on
        # top of the checkpointer's already-stored state.
        self._bootstrapped_rooms: OrderedDict[str, None] = OrderedDict()

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Render system prompt after agent metadata is fetched."""
        await super().on_started(agent_name, agent_description)
        self._system_prompt = render_system_prompt(
            template=self.prompt_template,
            agent_name=agent_name,
            agent_description=agent_description,
            custom_section=self.custom_section,
            features=self.features,
        )
        logger.info("LangGraph adapter started for agent: %s", agent_name)

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: LangChainMessages,  # Fully typed!
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        """Handle message with LangGraph."""
        from thenvoi.integrations.langgraph.langchain_tools import (
            agent_tools_to_langchain,
        )

        logger.info("[HANDLE] Message %s in room %s", msg.id, room_id)

        # Get LangChain tools
        langchain_tools = (
            agent_tools_to_langchain(
                tools,
                include_memory_tools=Capability.MEMORY in self.features.capabilities,
                include_contacts=Capability.CONTACTS in self.features.capabilities,
            )
            + self.additional_tools
        )

        # Build or get graph
        if self.graph_factory:
            graph = self.graph_factory(langchain_tools)
        else:
            graph = self._static_graph

        if not graph:
            raise RuntimeError("No graph available")

        # Build messages
        messages: list[Any] = []

        # Session bootstrap: prepend the rendered system prompt and hydrate
        # platform history exactly once per room. After that, the LangGraph
        # checkpointer carries the system message and prior turns forward and
        # we just append the new user turn.
        should_mark_bootstrapped = False
        if is_session_bootstrap and room_id not in self._bootstrapped_rooms:
            if self._system_prompt:
                messages.append(("system", self._system_prompt))
            if history:
                messages.extend(history)  # Already converted by history_converter
            should_mark_bootstrapped = True

        # Inject metadata updates as user messages with [System]: prefix.
        # Many LLM providers (including Anthropic) require a single system
        # message at the start; additional system messages scattered through
        # the conversation cause errors and kill provider cache savings.
        if participants_msg:
            messages.append(("user", f"[System]: {participants_msg}"))

        if contacts_msg:
            messages.append(("user", f"[System]: {contacts_msg}"))

        messages.append(("user", msg.format_for_llm()))

        graph_input = {"messages": messages}

        try:
            async for event in graph.astream_events(
                graph_input,
                config={
                    "configurable": {
                        "thread_id": room_id,
                    },
                    "recursion_limit": self.recursion_limit,
                },
                version="v2",
            ):
                await self._handle_stream_event(event, room_id, tools)

            if should_mark_bootstrapped:
                self._bootstrapped_rooms[room_id] = None
                if len(self._bootstrapped_rooms) > _BOOTSTRAP_TRACKING_WARN_THRESHOLD:
                    oldest_room_id, _ = self._bootstrapped_rooms.popitem(last=False)
                    logger.warning(
                        "Bootstrap tracking reached %d rooms; evicting oldest room %s",
                        _BOOTSTRAP_TRACKING_WARN_THRESHOLD,
                        oldest_room_id,
                    )

            logger.info("[DONE] Message %s processed successfully", msg.id)

        except Exception:
            logger.exception("Error processing message %s", msg.id)
            try:
                # Keep the user-facing payload generic; the full traceback is
                # in the agent log via logger.exception above. Tool/error
                # internals can include DB strings, paths, and tokens that
                # should not surface in chat.
                await tools.send_event(
                    content="Internal error while processing message; see agent logs.",
                    message_type="error",
                )
            except Exception:
                logger.exception("Failed to report error event for message %s", msg.id)
            raise

    async def _handle_stream_event(
        self,
        event: Any,
        room_id: str,
        tools: AgentToolsProtocol,
    ) -> None:
        """Handle streaming events from LangGraph."""
        event_type = event.get("event")

        if event_type == "on_tool_start":
            if Emit.EXECUTION not in self.features.emit:
                return

            tool_name = event.get("name", "unknown")
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            payload = {
                "name": tool_name,
                "args": data.get("input", {}),
                "tool_call_id": event.get("run_id", "unknown"),
            }
            logger.info("[STREAM] on_tool_start: %s", tool_name)
            try:
                await tools.send_event(
                    content=json.dumps(payload, default=str),
                    message_type="tool_call",
                )
            except Exception as e:
                logger.warning("Failed to send tool_call event: %s", e)

        elif event_type == "on_tool_end":
            if Emit.EXECUTION not in self.features.emit:
                return

            tool_name = event.get("name", "unknown")
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            payload = {
                "name": tool_name,
                "output": data.get("output", ""),
                "tool_call_id": event.get("run_id", "unknown"),
                "is_error": False,
            }
            logger.info("[STREAM] on_tool_end: %s", tool_name)
            try:
                await tools.send_event(
                    content=json.dumps(payload, default=str),
                    message_type="tool_result",
                )
            except Exception as e:
                logger.warning("Failed to send tool_result event: %s", e)

    async def on_cleanup(self, room_id: str) -> None:
        """Clean up LangGraph state for a room."""
        self._bootstrapped_rooms.pop(room_id, None)
        if not self.graph_factory:
            return
        # Future graph_factory-specific cleanup (e.g. checkpointer) goes here
