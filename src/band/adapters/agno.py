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
from band.converters.agno import AgnoHistoryConverter, AgnoMessages

if TYPE_CHECKING:
    from agno.agent import Agent as AgnoAgent
    from agno.models.message import Message
    from agno.run.agent import RunOutput
    from agno.tools.function import Function

logger = logging.getLogger(__name__)

# The Band tools handle for the room being processed. Wired Band tools read it
# at call time so a single shared Agno agent can serve concurrent rooms — each
# on_message coroutine sets its own value (ContextVars are task-isolated).
_current_tools: ContextVar[AgentToolsProtocol | None] = ContextVar(
    "agno_current_tools", default=None
)


def _make_band_entrypoint(tool_name: str) -> Any:
    """Build an async Agno tool entrypoint that runs a Band platform tool."""

    async def _entrypoint(**kwargs: Any) -> str:
        active = _current_tools.get()
        if active is None:
            return f"Error: no active Band context for tool {tool_name}"
        result = await active.execute_tool_call(tool_name, kwargs)
        return result if isinstance(result, str) else json.dumps(result, default=str)

    _entrypoint.__name__ = tool_name
    return _entrypoint


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

    # Can report the Agno agent's own tool executions to the room.
    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset({Emit.EXECUTION})
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
        # on_started builds a deep copy (`self.agent`) that we wire Band tools
        # into and run. The copy is shared across rooms/messages; Agno keeps
        # per-run state in its run context, so a single instance is safe to reuse.
        self._source_agent = agent
        self.agent: AgnoAgent | None = None

        # Per-room running transcript. Band delivers the rehydrated platform
        # history only on session bootstrap (including after a restart); later
        # messages arrive with empty history, so the adapter accumulates the
        # conversation itself and feeds it to Agno on every run.
        self._message_history: dict[str, list[Message]] = {}

        # Band capability tools (memory/contacts) are wired into the copy once,
        # on the first message, since they are room-agnostic.
        self._band_tools_wired = False

        self._warn_on_memory_collision(agent)

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
        self.agent = self._source_agent.deep_copy()

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
        """Run the developer's Agno agent on the history and reply with text."""
        from agno.models.message import Message

        if self.agent is None:
            raise RuntimeError("Agno agent not initialized; on_started was not called")

        sender = msg.sender_name or msg.sender_type
        logger.info(
            "Room %s msg %s: handling from %s (sender=%s)",
            room_id,
            msg.id,
            sender,
            msg.sender_id,
        )

        # Expose Band memory/contact tools to the agent (once, room-agnostic).
        self._ensure_band_tools(tools)

        # Seed the running transcript from the rehydrated platform history on
        # bootstrap (or restart); otherwise reuse what we have accumulated.
        if is_session_bootstrap:
            self._message_history[room_id] = list(history)
            logger.debug(
                "Room %s msg %s: bootstrap seeded %d message(s) from rehydrated history",
                room_id,
                msg.id,
                len(history),
            )
        elif room_id not in self._message_history:
            self._message_history[room_id] = []
        messages = self._message_history[room_id]

        if participants_msg:
            messages.append(
                Message(role="user", content=f"[System]: {participants_msg}")
            )
        if contacts_msg:
            messages.append(Message(role="user", content=f"[System]: {contacts_msg}"))
        messages.append(Message(role="user", content=msg.format_for_llm()))

        logger.debug(
            "Room %s msg %s: running Agno agent (%d input messages)",
            room_id,
            msg.id,
            len(messages),
        )
        # Bind the room's tools so wired Band tools execute against this room.
        token = _current_tools.set(tools)
        try:
            response = await self.agent.arun(input=messages)
        except Exception as e:
            logger.exception(
                "Room %s msg %s: error running Agno agent: %s", room_id, msg.id, e
            )
            raise
        finally:
            _current_tools.reset(token)

        if response is None:
            logger.debug(
                "Room %s msg %s: Agno agent returned no response", room_id, msg.id
            )
            return

        # Report the agent's own tool executions (happened during the run, so
        # before the final reply) when execution reporting is enabled.
        if Emit.EXECUTION in self.features.emit:
            await self._report_tool_executions(response, tools, room_id, msg.id)

        # Persist the agent's full turn (tool calls/results + reply) so the next
        # message has continuity; Agno's run message list is the source of truth.
        if response.messages:
            self._message_history[room_id] = [
                m for m in response.messages if m.role != "system"
            ]

        # The agent may post via band_send_message itself. If it did, we are
        # done; otherwise fall back to sending its final text so every agent
        # replies regardless of whether it used the tool.
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

    async def on_cleanup(self, room_id: str) -> None:
        """Drop the room's accumulated transcript when the agent leaves."""
        self._message_history.pop(room_id, None)

    def _ensure_band_tools(self, tools: AgentToolsProtocol) -> None:
        """Wire the in-scope Band tools into the Agno agent once.

        These tools are room-agnostic (the active room is supplied via the
        ``_current_tools`` ContextVar at call time), so they are added to the
        shared agent a single time on the first message.
        """
        if self._band_tools_wired or self.agent is None:
            return

        band_tools = self._build_band_tools(tools)
        wired: list[str] = []
        for fn in band_tools:
            try:
                self.agent.add_tool(fn)
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
        from agno.tools.function import Function

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
                Function(
                    name=name,
                    description=fn.get("description", "") or "",
                    parameters=fn.get("parameters")
                    or {"type": "object", "properties": {}},
                    entrypoint=_make_band_entrypoint(name),
                    skip_entrypoint_processing=True,
                )
            )
        return band_tools

    async def _report_tool_executions(
        self,
        response: RunOutput,
        tools: AgentToolsProtocol,
        room_id: str,
        msg_id: str,
    ) -> None:
        """Emit tool_call/tool_result events for the agent's tool executions."""
        executions = list(getattr(response, "tools", None) or [])
        if not executions:
            return

        logger.info(
            "Room %s msg %s: reporting %d tool execution(s)",
            room_id,
            msg_id,
            len(executions),
        )
        for te in executions:
            tool_call_id = getattr(te, "tool_call_id", None) or ""
            tool_name = getattr(te, "tool_name", None) or ""
            tool_args = getattr(te, "tool_args", None) or {}
            is_error = bool(getattr(te, "tool_call_error", False))
            result = str(getattr(te, "result", "") or "")
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
                        {
                            "name": tool_name,
                            "args": tool_args,
                            "tool_call_id": tool_call_id,
                        }
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
