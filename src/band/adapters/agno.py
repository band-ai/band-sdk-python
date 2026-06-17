"""
Agno adapter using the SimpleAdapter pattern.

Agno is model-agnostic: the developer builds and configures their own Agno
``Agent`` (model, instructions, tools, reasoning, ...) and hands it to this
adapter. The adapter simply bridges it to Band — it converts Band history to
Agno messages, runs the developer's agent, and sends the text reply back.

Unlike adapters that run an explicit tool-calling loop, Agno owns its own agent
loop internally: ``Agent.arun(input=...)`` accepts a list of Agno messages and
returns a run output whose ``.content`` is the final text. The Band toolset is
exposed to the agent so it can send messages and act on the platform itself;
tool executions are reported to the room when ``Emit.EXECUTION`` is enabled.
"""

from __future__ import annotations

import json
import logging
import warnings
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, ClassVar

from band.core.protocols import AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.types import (
    AdapterFeatures,
    Capability,
    Emit,
    PlatformMessage,
)
from band.converters.agno import (
    AgnoHistoryConverter,
    AgnoMessages,
    agno_function_class,
    agno_message_class,
)

if TYPE_CHECKING:
    from agno.agent import Agent as AgnoAgent
    from agno.models.message import Message
    from agno.run.agent import RunOutput
    from agno.tools.function import Function

logger = logging.getLogger(__name__)

# Tools whose effect is already a visible room message/event, so their
# execution must not be re-reported as tool_call/tool_result events.
_SELF_REPORTING_TOOLS = frozenset({"band_send_message", "band_send_event"})

# The Band tools handle for the room being processed. Wired Band tools read it
# at call time so a single shared Agno agent can serve concurrent rooms — each
# on_message coroutine sets its own value (ContextVars are task-isolated).
_current_tools: ContextVar[AgentToolsProtocol | None] = ContextVar(
    "agno_current_tools", default=None
)


def _make_band_entrypoint(tool_name: str) -> Callable[..., Awaitable[str]]:
    """Build an async Agno tool entrypoint that runs a Band platform tool."""

    async def _entrypoint(**kwargs: Any) -> str:
        active = _current_tools.get()
        if active is None:
            return f"Error: no active Band context for tool {tool_name}"
        result = await active.execute_tool_call(tool_name, kwargs)
        return result if isinstance(result, str) else json.dumps(result, default=str)

    _entrypoint.__name__ = tool_name
    return _entrypoint


@contextmanager
def _bind_room_tools(tools: AgentToolsProtocol) -> Iterator[None]:
    """Bind the room's Band tools for the duration of an Agno run.

    Wired Band tool entrypoints read ``_current_tools`` at call time; binding it
    here (and always resetting on exit) lets a single shared agent serve
    concurrent rooms without their tool calls crossing over.
    """
    token = _current_tools.set(tools)
    try:
        yield
    finally:
        _current_tools.reset(token)


class AgnoAdapter(SimpleAdapter[AgnoMessages]):
    """
    Agno framework adapter (text output + execution reporting).

    Takes a developer-built Agno ``Agent`` and bridges it to Band. Stateless per
    room: Band history is the source of truth and is passed as input on every
    message.

    The Band toolset is exposed to the agent — chat and participant tools always,
    plus memory/contact tools when the matching capabilities are enabled — so it
    can send messages, invite peers, and act on the platform itself. If the agent
    does not post via ``band_send_message``, its final text is sent as a fallback,
    so simple agents still reply without any Band-specific prompting.

    Tool executions are reported to the room as tool_call/tool_result events when
    ``Emit.EXECUTION`` is enabled.

    Example:
        from agno.agent import Agent as AgnoAgent
        from agno.models.anthropic import Claude

        agno_agent = AgnoAgent(
            model=Claude(id="claude-sonnet-4-6"),
            instructions="You are a helpful assistant.",
        )
        adapter = AgnoAdapter(agno_agent)
        agent = Agent.create(adapter=adapter, agent_id="...", api_key="...")
        await agent.run()
    """

    # Can report the agent's tool executions and reasoning to the room.
    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset(
        {Emit.EXECUTION, Emit.THOUGHTS}
    )
    # Can expose Band memory/contact tools to the Agno agent.
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.MEMORY, Capability.CONTACTS}
    )

    def __init__(
        self,
        agent: AgnoAgent,
        *,
        history_converter: AgnoHistoryConverter | None = None,
        features: AdapterFeatures | None = None,
    ) -> None:
        super().__init__(
            history_converter=history_converter or AgnoHistoryConverter(),
            features=features,
        )

        # The caller's agent is the source of configuration. We never mutate it:
        # on_started builds a deep copy (exposed read-only via ``agent``) that we
        # wire Band tools into and run. The copy is shared across rooms/messages;
        # Agno keeps per-run state in its run context, so reuse is safe.
        self._source_agent = agent
        self._agent: AgnoAgent | None = None

        # Per-room running transcript. Band delivers the rehydrated platform
        # history only on session bootstrap (including after a restart); later
        # messages arrive with empty history, so the adapter accumulates the
        # conversation itself and feeds it to Agno on every run.
        self._message_history: dict[str, list[Message]] = {}

        # Band capability tools (memory/contacts) are wired into the copy once,
        # on the first message, since they are room-agnostic.
        self._band_tools_wired = False

        self._warn_on_memory_collision(agent)

    @property
    def agent(self) -> AgnoAgent | None:
        """The running Agno agent (a deep copy of the caller's), or None until
        on_started. Read-only: the adapter owns and wires this instance."""
        return self._agent

    def _warn_on_memory_collision(self, agent: AgnoAgent) -> None:
        """Warn if Band memory was requested while Agno's own memory is enabled.

        Only relevant when the caller enabled ``Capability.MEMORY``: the adapter
        then exposes Band memory tools to the agent, which collides with Agno's
        built-in memory (``update_memory_on_run`` / ``enable_agentic_memory``).
        """
        if Capability.MEMORY not in self.features.capabilities:
            return

        enabled: list[str] = []
        if agent.update_memory_on_run:
            enabled.append("update_memory_on_run")
        if agent.enable_agentic_memory:
            enabled.append("enable_agentic_memory")

        if enabled:
            warnings.warn(
                "Capability.MEMORY exposes Band memory tools to the agent, but "
                f"this Agno agent also manages its own memory ({', '.join(enabled)}). "
                "The two memory systems collide; disable one of them.",
                UserWarning,
                stacklevel=3,
            )

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Deep-copy the caller's agent and sync the converter identity."""
        await super().on_started(agent_name, agent_description)

        # Run a copy so wiring Band tools never mutates the caller's object.
        self._agent = self._source_agent.deep_copy()

        # Keep the converter's own-agent filtering in sync with our identity.
        if isinstance(self.history_converter, AgnoHistoryConverter):
            self.history_converter.set_agent_name(agent_name)

        logger.info("Agno adapter started for agent: %s", agent_name)
        logger.debug(
            "Agno adapter features: emit=%s capabilities=%s",
            sorted(e.value for e in self.features.emit),
            sorted(c.value for c in self.features.capabilities),
        )

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: AgnoMessages,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        """Run the developer's Agno agent and ensure a reply is sent."""
        if self.agent is None:
            raise RuntimeError("Agno agent not initialized; on_started was not called")

        logger.info(
            "Room %s msg %s: handling from %s (sender=%s, bootstrap=%s)",
            room_id,
            msg.id,
            msg.sender_name or msg.sender_type,
            msg.sender_id,
            is_session_bootstrap,
        )

        self._ensure_band_tools(tools)
        messages = self._build_run_input(
            msg,
            history,
            participants_msg,
            contacts_msg,
            is_session_bootstrap=is_session_bootstrap,
            room_id=room_id,
        )
        response = await self._run_agent(
            messages, tools, room_id=room_id, msg_id=msg.id
        )
        if response is None:
            return

        if Emit.THOUGHTS in self.features.emit:
            await self._report_thoughts(response, tools, room_id=room_id, msg_id=msg.id)
        if Emit.EXECUTION in self.features.emit:
            await self._report_tool_executions(
                response, tools, room_id=room_id, msg_id=msg.id
            )

        self._persist_turn(room_id, response)
        await self._send_reply(msg, tools, response, room_id=room_id)

    async def on_cleanup(self, room_id: str) -> None:
        """Drop the room's accumulated transcript when the agent leaves."""
        self._message_history.pop(room_id, None)

    def _build_run_input(
        self,
        msg: PlatformMessage,
        history: AgnoMessages,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> list[Message]:
        """Seed the per-room transcript and append the new system/user messages.

        Band delivers the rehydrated platform history only on bootstrap (incl.
        after a restart); later messages arrive empty, so the adapter keeps the
        running transcript itself.
        """
        if is_session_bootstrap:
            self._message_history[room_id] = list(history)
        else:
            self._message_history.setdefault(room_id, [])

        message_cls = agno_message_class()
        messages = self._message_history[room_id]
        if participants_msg:
            messages.append(
                message_cls(role="user", content=f"[System]: {participants_msg}")
            )
        if contacts_msg:
            messages.append(
                message_cls(role="user", content=f"[System]: {contacts_msg}")
            )
        messages.append(message_cls(role="user", content=msg.format_for_llm()))
        return messages

    async def _run_agent(
        self,
        messages: list[Message],
        tools: AgentToolsProtocol,
        *,
        room_id: str,
        msg_id: str,
    ) -> RunOutput | None:
        """Run the Agno agent with the room's tools bound for this call."""
        agent = self.agent
        assert agent is not None  # on_message guarantees the agent is initialized

        logger.debug(
            "Room %s msg %s: running Agno agent (%d input messages)",
            room_id,
            msg_id,
            len(messages),
        )
        try:
            with _bind_room_tools(tools):
                response = await agent.arun(input=messages)
        except Exception as e:
            logger.exception(
                "Room %s msg %s: error running Agno agent: %s", room_id, msg_id, e
            )
            raise

        if response is None:
            logger.debug(
                "Room %s msg %s: Agno agent returned no response", room_id, msg_id
            )
        return response

    def _persist_turn(self, room_id: str, response: RunOutput) -> None:
        """Persist the agent's full turn (tool calls/results + reply) for continuity.

        Agno's run message list is the source of truth; the system message is
        dropped because Agno re-injects it from the agent's instructions.
        """
        if response.messages:
            self._message_history[room_id] = [
                m for m in response.messages if m.role != "system"
            ]

    async def _send_reply(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        response: RunOutput,
        *,
        room_id: str,
    ) -> None:
        """Send the agent's text reply unless it already replied via a tool.

        Autonomous agents post through band_send_message themselves; if the agent
        did not, its final text is sent as a fallback so it always responds.
        """
        if any(
            getattr(te, "tool_name", None) == "band_send_message"
            for te in (getattr(response, "tools", None) or [])
        ):
            logger.debug(
                "Room %s msg %s: agent replied via band_send_message", room_id, msg.id
            )
            return

        text = response.get_content_as_string().strip()
        if not text:
            logger.debug("Room %s msg %s: agent produced no reply", room_id, msg.id)
            return

        # mentions accepts handles/names/IDs as strings; the SDK resolves them.
        mentions = [msg.sender_id]
        logger.info(
            "Room %s msg %s: sending reply (%d chars), mentions=%s",
            room_id,
            msg.id,
            len(text),
            mentions,
        )
        await tools.send_message(text, mentions=mentions)

    def _ensure_band_tools(self, tools: AgentToolsProtocol) -> None:
        """Wire the in-scope Band tools into the Agno agent once.

        These tools are room-agnostic (the active room is supplied via the
        ``_current_tools`` ContextVar at call time), so they are added to the
        shared agent a single time on the first message.
        """
        if self._band_tools_wired or self._agent is None:
            return

        band_tools = self._build_band_tools(tools)
        wired: list[str] = []
        for fn in band_tools:
            try:
                self._agent.add_tool(fn)
                wired.append(fn.name)
            except RuntimeError as e:
                # add_tool rejects when the agent's tools is a callable factory.
                logger.warning("Could not wire Band tool %s: %s", fn.name, e)
        if wired:
            logger.info(
                "Wired %d Band tool(s) into Agno agent: %s",
                len(wired),
                ", ".join(wired),
            )
        # Synchronous, no await: safe to mark wired even across concurrent calls.
        self._band_tools_wired = True

    def _build_band_tools(self, tools: AgentToolsProtocol) -> list[Function]:
        """Convert the in-scope Band tool schemas into Agno Functions.

        Chat/participant tools are always exposed; memory/contact tools are added
        when the matching capabilities are enabled.
        """
        function_cls = agno_function_class()
        schemas = tools.get_openai_tool_schemas(
            include_memory=Capability.MEMORY in self.features.capabilities,
            include_contacts=Capability.CONTACTS in self.features.capabilities,
        )

        band_tools: list[Function] = []
        for schema in schemas:
            fn = schema.get("function", {})
            name = fn.get("name")
            if not name:
                continue
            band_tools.append(
                function_cls(
                    name=name,
                    description=fn.get("description", "") or "",
                    parameters=fn.get("parameters")
                    or {"type": "object", "properties": {}},
                    entrypoint=_make_band_entrypoint(name),
                    skip_entrypoint_processing=True,
                )
            )
        return band_tools

    async def _report_thoughts(
        self,
        response: RunOutput,
        tools: AgentToolsProtocol,
        *,
        room_id: str,
        msg_id: str,
    ) -> None:
        """Post the agent's reasoning content as a thought event.

        Only produces output when the developer's Agno agent has reasoning
        enabled (e.g. ``reasoning=True`` or a reasoning model); otherwise
        ``reasoning_content`` is empty and nothing is posted.
        """
        reasoning = getattr(response, "reasoning_content", None)
        text = (reasoning or "").strip() if isinstance(reasoning, str) else ""
        if not text:
            return

        logger.info(
            "Room %s msg %s: reporting reasoning as thought (%d chars)",
            room_id,
            msg_id,
            len(text),
        )
        try:
            await tools.send_event(content=text, message_type="thought")
        except Exception as e:
            logger.warning(
                "Room %s msg %s: failed to report thought: %s", room_id, msg_id, e
            )

    async def _report_tool_executions(
        self,
        response: RunOutput,
        tools: AgentToolsProtocol,
        *,
        room_id: str,
        msg_id: str,
    ) -> None:
        """Emit tool_call/tool_result events for the agent's tool executions.

        Skips band_send_message/band_send_event: their effect is already a
        visible room message/event, so reporting them would double-record the
        reply (and duplicate it on rehydration).
        """
        executions = [
            te
            for te in (getattr(response, "tools", None) or [])
            if (getattr(te, "tool_name", None) or "") not in _SELF_REPORTING_TOOLS
        ]
        if not executions:
            return

        logger.info(
            "Room %s msg %s: reporting %d tool execution(s)",
            room_id,
            msg_id,
            len(executions),
        )
        for execution in executions:
            await self._emit_execution(execution, tools, room_id=room_id, msg_id=msg_id)

    async def _emit_execution(
        self,
        execution: Any,
        tools: AgentToolsProtocol,
        *,
        room_id: str,
        msg_id: str,
    ) -> None:
        """Emit the tool_call + tool_result event pair for one tool execution."""
        tool_call_id = getattr(execution, "tool_call_id", None) or ""
        tool_name = getattr(execution, "tool_name", None) or ""
        tool_args = getattr(execution, "tool_args", None) or {}
        is_error = bool(getattr(execution, "tool_call_error", False))
        result = str(getattr(execution, "result", "") or "")

        logger.debug(
            "Room %s msg %s: tool %s(%s) -> %s%s",
            room_id,
            msg_id,
            tool_name,
            tool_args,
            result[:200],
            " [error]" if is_error else "",
        )
        try:
            await tools.send_event(
                content=json.dumps(
                    {"name": tool_name, "args": tool_args, "tool_call_id": tool_call_id}
                ),
                message_type="tool_call",
            )
            await tools.send_event(
                content=json.dumps(
                    {
                        "name": tool_name,
                        "output": result,
                        "tool_call_id": tool_call_id,
                        "is_error": is_error,
                    }
                ),
                message_type="tool_result",
            )
        except Exception as e:
            logger.warning(
                "Room %s msg %s: failed to report tool execution %s: %s",
                room_id,
                msg_id,
                tool_name,
                e,
            )
