"""Agno adapter using the SimpleAdapter pattern."""

from __future__ import annotations

import json
import logging
import warnings
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import TYPE_CHECKING, Any, ClassVar, Concatenate, ParamSpec, TypeVar

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

P = ParamSpec("P")
R = TypeVar("R")

# These tools already produce visible room output.
_SELF_REPORTING_TOOLS = frozenset({"band_send_message", "band_send_event"})

# Current room tools for wired Agno tool entrypoints.
_current_tools: ContextVar[AgentToolsProtocol | None] = ContextVar(
    "agno_current_tools", default=None
)


def _tool_executions(response: RunOutput) -> list[Any]:
    return list(getattr(response, "tools", None) or [])


def _tool_name(execution: Any) -> str:
    return getattr(execution, "tool_name", None) or ""


def _with_agent(
    fn: Callable[Concatenate[Any, AgnoAgent, P], Awaitable[R]],
) -> Callable[Concatenate[Any, P], Awaitable[R]]:
    @wraps(fn)
    async def wrapper(self: Any, *args: P.args, **kwargs: P.kwargs) -> R:
        agent = getattr(self, "_agent", None)
        if agent is None:
            raise RuntimeError("AgnoAdapter was used before on_started()")
        return await fn(self, agent, *args, **kwargs)

    return wrapper


def _make_band_entrypoint(tool_name: str) -> Callable[..., Awaitable[str]]:
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
    """Bind room tools for one Agno run."""
    token = _current_tools.set(tools)
    try:
        yield
    finally:
        _current_tools.reset(token)


class AgnoAdapter(SimpleAdapter[AgnoMessages]):
    """Bridge a developer-built Agno agent to Band."""

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset(
        {Emit.EXECUTION, Emit.THOUGHTS}
    )
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

        # Keep caller configuration immutable; runtime wiring happens on the copy.
        self._source_agent = agent
        self._agent: AgnoAgent | None = None

        # Running per-room transcripts; bootstrap history seeds each room.
        self._message_history: dict[str, list[Message]] = {}
        self._band_tools_wired = False

        self._warn_on_memory_collision(agent)

    @property
    def agent(self) -> AgnoAgent | None:
        """The running Agno agent, initialized in on_started."""
        return self._agent

    def _warn_on_memory_collision(self, agent: AgnoAgent) -> None:
        """Warn when Band and Agno memory are both enabled."""
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

        self._agent = self._source_agent.deep_copy()

        # Keep the converter's own-agent filtering in sync with our identity, so
        # rehydrated history maps this agent's past messages to the assistant role.
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
        """Build Agno input for this turn."""
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

    @_with_agent
    async def _run_agent(
        self,
        agent: AgnoAgent,
        messages: list[Message],
        tools: AgentToolsProtocol,
        *,
        room_id: str,
        msg_id: str,
    ) -> RunOutput | None:
        """Run the Agno agent with the room's tools bound for this call."""
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
        """Persist Agno's transcript, excluding generated system messages."""
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
        """Send final text unless the agent already posted through Band."""
        if any(
            _tool_name(execution) == "band_send_message"
            for execution in _tool_executions(response)
        ):
            logger.debug(
                "Room %s msg %s: agent replied via band_send_message", room_id, msg.id
            )
            return

        text = response.get_content_as_string().strip()
        if not text:
            logger.debug("Room %s msg %s: agent produced no reply", room_id, msg.id)
            return

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
        """Wire Band tools into the copied Agno agent once."""
        if self._band_tools_wired or self._agent is None:
            return

        band_tools = self._build_band_tools(tools)
        wired: list[str] = []
        for fn in band_tools:
            try:
                self._agent.add_tool(fn)
                wired.append(fn.name)
            except RuntimeError as e:
                logger.warning("Could not wire Band tool %s: %s", fn.name, e)
        if wired:
            logger.info(
                "Wired %d Band tool(s) into Agno agent: %s",
                len(wired),
                ", ".join(wired),
            )
        self._band_tools_wired = True

    def _build_band_tools(self, tools: AgentToolsProtocol) -> list[Function]:
        """Convert Band tool schemas into Agno Functions."""
        function_cls = agno_function_class()
        schemas = tools.get_openai_tool_schemas(
            include_memory=Capability.MEMORY in self.features.capabilities,
            include_contacts=Capability.CONTACTS in self.features.capabilities,
        )

        band_tools: list[Function] = []
        for schema in schemas:
            fn = schema.get("function", {})
            if name := fn.get("name"):
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
        """Post Agno reasoning as a thought event."""
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
        """Emit tool_call/tool_result events for reportable executions."""
        executions = [
            execution
            for execution in _tool_executions(response)
            if _tool_name(execution) not in _SELF_REPORTING_TOOLS
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
