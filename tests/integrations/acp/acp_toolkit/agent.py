"""Scripted, in-process fake ACP agent (the peer an ACP client drives)."""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from acp import RequestError
from acp.agent.connection import AgentSideConnection
from acp.helpers import (
    plan_entry,
    start_tool_call,
    text_block,
    tool_content,
    update_agent_message_text,
    update_agent_thought_text,
    update_plan,
    update_tool_call,
)
from acp.schema import (
    AgentCapabilities,
    ConfigOptionUpdate,
    InitializeResponse,
    LoadSessionResponse,
    McpCapabilities,
    NewSessionResponse,
    PermissionOption,
    PromptResponse,
    SessionCapabilities,
    SessionCloseCapabilities,
    SessionConfigOptionSelect,
    SessionConfigSelectOption,
    SetSessionConfigOptionResponse,
    ToolCallUpdate,
)
from mcp.types import Tool

from band.integrations.acp.model_selection import (
    MODEL_CATEGORY,
    THOUGHT_LEVEL_CATEGORY,
)
from band.integrations.acp.session_config import SessionConfigOption, find_select
from band.integrations.mcp import BandMCPTransport
from tests.mcpclient import mcp_session

PromptHandler = Callable[["FakeACPAgent", str], Awaitable[None]]
ConfigOptionHandler = Callable[
    ["FakeACPAgent", str, str, str], Awaitable[Sequence[SessionConfigOption]]
]

# The option ids Copilot CLI publishes its model and effort selects under.
MODEL_OPTION_ID = "model"
EFFORT_OPTION_ID = "reasoning_effort"


@dataclass
class Gate:
    """A pause point: ``received`` is set on arrival, then it waits for ``release``."""

    received: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)


class FakeACPAgent:
    """A scripted, in-process ACP agent (the peer an ACP client drives).

    Implements the ``acp.Agent`` protocol methods the client flow calls and pushes
    canned ``session_update`` chunks back over the real connection. Script it with
    the fluent ``will_*`` builders (emitted, in order, on every prompt) or override
    entirely with the ``@agent.on_prompt`` decorator.
    """

    def __init__(
        self,
        *,
        http: bool = True,
        sse: bool = False,
        supports_session_load: bool = False,
        config_options: Sequence[SessionConfigOption] = (),
    ) -> None:
        self._http = http
        self._sse = sse
        self._supports_session_load = supports_session_load
        self._persisted_sessions: set[str] = set()
        self._session_load_error: RequestError | None = None
        # A room-owned ACPRuntime opens its own connection, so a genuinely
        # concurrent multi-room turn (see test_independent_rooms_configure_
        # without_waiting_for_each_other) can have more than one live
        # connection at once. `_current_conn` is only the most-recently-
        # connected one; sends for an existing session go over the
        # connection that created it instead, via `_conns_by_session`.
        self._current_conn: AgentSideConnection | None = None
        self._conns_by_session: dict[str, AgentSideConnection | None] = {}
        self._script: list[PromptHandler] = []
        self._custom: PromptHandler | None = None
        self._config_options = list(config_options)
        self._config_option_handler: ConfigOptionHandler | None = None
        self._reply_gate: Gate | None = None
        self._hangs_up_on_config = False
        # Closes this agent's end of the transport; the harness binds it.
        self.hang_up: Callable[[], None] = lambda: None
        # Holds the connection's shutdown, as a subprocess slow to exit.
        self.exit_gate: Gate | None = None
        # Observability for assertions:
        self.sessions: list[dict[str, Any]] = []
        self._mcp_servers_by_session: dict[str, list[Any]] = {}
        self.prompts: list[dict[str, Any]] = []
        self.session_load_requests: list[str] = []
        self.permission_responses: list[Any] = []
        self.auth_methods: list[str] = []
        self.config_option_requests: list[tuple[str, str, str]] = []
        self.closed_sessions: list[str] = []
        self.approved: bool | None = None

    # -- scripting ---------------------------------------------------------------

    def on_prompt(self, handler: PromptHandler) -> PromptHandler:
        """Decorator: register a full-control async handler ``(agent, session_id)``.

        Wins over any ``will_*`` script. Returns the handler unchanged so it reads as
        a normal decorator.
        """
        self._custom = handler
        return handler

    def on_config_option(self, handler: ConfigOptionHandler) -> ConfigOptionHandler:
        """Set dynamic behavior for every ``session/set_config_option`` call."""
        self._config_option_handler = handler
        return handler

    def hangs_up_on_next_config_option(self) -> None:
        """Drop the connection on the next ``set_config_option``, unanswered,
        as a crashed agent process does."""
        self._hangs_up_on_config = True

    def holds_config_replies(self) -> Gate:
        """Apply each ``set_config_option`` at once but reply only on release."""
        self._reply_gate = Gate()
        return self._reply_gate

    def exits_slowly(self) -> Gate:
        """Hold every connection's shutdown until release."""
        self.exit_gate = Gate()
        return self.exit_gate

    def advertises_models(
        self,
        efforts_by_model: Mapping[str, Sequence[str]],
        *,
        current: str,
        default_effort: str = "medium",
        pushes_updates: bool = True,
    ) -> FakeACPAgent:
        """Advertise a model select whose effort select follows the chosen model.

        Mirrors Copilot CLI 1.0.89 (probed live): selecting a model replaces
        the effort select with that model's levels, or drops it for a model
        with none; the current effort carries over when the new model offers
        it, else falls back to ``default_effort``; an unoffered effort is
        silently ignored; and every change is also pushed as a
        ``config_option_update``. ``pushes_updates=False`` models a spec agent
        whose set replies are the only record of a change.
        """
        active = {"model": current, "effort": default_effort}

        def catalog() -> list[SessionConfigOption]:
            options: list[SessionConfigOption] = [
                select_option(
                    MODEL_OPTION_ID,
                    active["model"],
                    efforts_by_model,
                    category=MODEL_CATEGORY,
                )
            ]
            efforts = efforts_by_model[active["model"]]
            if efforts:
                if active["effort"] not in efforts:
                    active["effort"] = default_effort
                options.append(
                    select_option(
                        EFFORT_OPTION_ID,
                        active["effort"],
                        efforts,
                        category=THOUGHT_LEVEL_CATEGORY,
                    )
                )
            return options

        @self.on_config_option
        async def select(
            fake: FakeACPAgent, session_id: str, option_id: str, value: str
        ) -> list[SessionConfigOption]:
            if option_id == MODEL_OPTION_ID:
                active["model"] = value
            elif value in efforts_by_model[active["model"]]:
                active["effort"] = value
            options = catalog()
            if pushes_updates:
                await fake.push_config_options(session_id, options)
            return options

        self._config_options = catalog()
        return self

    def will_say(self, text: str) -> FakeACPAgent:
        self._script.append(lambda a, sid: a.say(sid, text))
        return self

    def knows_session(self, session_id: str) -> FakeACPAgent:
        """Register a persisted session id that ``session/load`` will restore."""
        self._persisted_sessions.add(session_id)
        return self

    def breaks_session_load(self, error: RequestError | None = None) -> FakeACPAgent:
        """Make every ``session/load`` fail with a non-missing-session error."""
        self._session_load_error = error or RequestError.internal_error()
        return self

    def will_stream(self, *parts: str) -> FakeACPAgent:
        """Emit several agent_message_chunk deltas in one turn (streaming reply)."""

        async def _action(a: FakeACPAgent, sid: str) -> None:
            for part in parts:
                await a.emit(sid, update_agent_message_text(part))

        self._script.append(_action)
        return self

    def will_think(self, text: str) -> FakeACPAgent:
        self._script.append(lambda a, sid: a.emit(sid, update_agent_thought_text(text)))
        return self

    def will_call_tool(
        self,
        tool_call_id: str,
        title: str,
        *,
        raw_input: Any = None,
        result: Any = None,
        status: str = "completed",
    ) -> FakeACPAgent:
        async def _action(a: FakeACPAgent, sid: str) -> None:
            await a.emit(sid, start_tool_call(tool_call_id, title, raw_input=raw_input))
            if result is not None:
                await a.emit(
                    sid,
                    update_tool_call(tool_call_id, raw_output=result, status=status),
                )

        self._script.append(_action)
        return self

    def will_call_mcp_tool_named_late(
        self,
        tool_call_id: str,
        server: str,
        tool: str,
        *,
        arguments: Mapping[str, Any],
        result: Any,
    ) -> FakeACPAgent:
        """Cursor's MCP call: a pending ``MCP: tool`` placeholder with no input,
        then a statusless update carrying the real title and ``rawInput``."""

        async def _action(a: FakeACPAgent, sid: str) -> None:
            await a.emit(
                sid,
                start_tool_call(
                    tool_call_id, "MCP: tool", status="pending", raw_input={}
                ),
            )
            await a.emit(
                sid,
                update_tool_call(
                    tool_call_id,
                    title=f"{server}: {tool}",
                    raw_input={
                        "providerIdentifier": server,
                        "toolName": tool,
                        "args": dict(arguments),
                    },
                ),
            )
            await a.emit(sid, update_tool_call(tool_call_id, status="in_progress"))
            await a.emit(
                sid,
                update_tool_call(tool_call_id, raw_output=result, status="completed"),
            )

        self._script.append(_action)
        return self

    def will_call_tool_then_trailing_update(
        self,
        tool_call_id: str,
        title: str,
        *,
        result: Any,
    ) -> FakeACPAgent:
        """A call that reports ``completed``, then sends a later update with no status.

        ACP ``status`` is optional, so an agent may emit a trailing
        ``tool_call_update`` (e.g. a bookkeeping frame) that omits it after the
        terminal ``completed`` frame. The bridge must keep the recorded
        ``completed`` — a "last status wins" fold would regress it to ``None``.
        """

        async def _action(a: FakeACPAgent, sid: str) -> None:
            await a.emit(sid, start_tool_call(tool_call_id, title))
            await a.emit(
                sid,
                update_tool_call(tool_call_id, raw_output=result, status="completed"),
            )
            await a.emit(sid, update_tool_call(tool_call_id, raw_output=result))

        self._script.append(_action)
        return self

    def will_stream_tool_result(
        self,
        tool_call_id: str,
        title: str,
        *,
        text: str,
        raw_output: Any,
    ) -> FakeACPAgent:
        """Report one tool call across several tool_call_updates, as real agents do.

        Emits a start, two in-progress frames carrying the readable output as
        content blocks, then a terminal frame carrying only the structured
        ``rawOutput`` (which stringifies to an unreadable dict). This is the shape
        that makes a naive bridge post the same result several times — the last one
        raw — so it exercises the client's collapse-and-prefer-clean behavior.
        """
        blocks = [tool_content(text_block(text))]

        async def _action(a: FakeACPAgent, sid: str) -> None:
            await a.emit(sid, start_tool_call(tool_call_id, title))
            await a.emit(
                sid,
                update_tool_call(tool_call_id, content=blocks, status="in_progress"),
            )
            await a.emit(
                sid,
                update_tool_call(tool_call_id, content=blocks, status="in_progress"),
            )
            await a.emit(
                sid,
                update_tool_call(
                    tool_call_id, raw_output=raw_output, status="completed"
                ),
            )

        self._script.append(_action)
        return self

    def will_call_mcp_tool(
        self,
        tool_call_id: str,
        tool_name: str,
        *,
        arguments: dict[str, Any],
        server: str = "band",
        title: str | None = None,
    ) -> FakeACPAgent:
        """Call an advertised MCP tool between ACP call and result updates.

        ``title`` is what the ACP ``tool_call`` update reports; it defaults to
        the tool name, but a harness may write something else there (OMP uses
        the model's intent phrase), so tests can model that divergence.
        """

        async def _action(a: FakeACPAgent, sid: str) -> None:
            await a.emit(
                sid,
                start_tool_call(
                    tool_call_id=tool_call_id,
                    title=title or tool_name,
                    raw_input=arguments,
                ),
            )
            result = await a.call_mcp_tool(
                session_id=sid,
                server=server,
                tool_name=tool_name,
                arguments=arguments,
            )
            await a.emit(sid, update_tool_call(tool_call_id, raw_output=result))

        self._script.append(_action)
        return self

    def will_plan(self, *steps: str) -> FakeACPAgent:
        self._script.append(
            lambda a, sid: a.emit(sid, update_plan([plan_entry(s) for s in steps]))
        )
        return self

    def will_update_cursor_todos(self, *contents: str) -> FakeACPAgent:
        self._script.append(lambda a, sid: a.update_cursor_todos(sid, *contents))
        return self

    def keeps_updating_cursor_todos(self) -> asyncio.Event:
        """Update Cursor's todos on every prompt until the connection drops;
        the returned event is set once the first update is sent."""
        updating = asyncio.Event()

        async def _action(a: FakeACPAgent, sid: str) -> None:
            for index in itertools.count():
                try:
                    await a.update_cursor_todos(sid, f"todo {index}")
                except Exception:  # noqa: BLE001 -- the client hung up
                    return
                updating.set()
                await asyncio.sleep(0)

        self._script.append(_action)
        return updating

    def will_ask_permission(
        self,
        *,
        tool_call_id: str = "tc-1",
        title: str | None = None,
        allow_option_id: str = "allow-1",
    ) -> FakeACPAgent:
        async def _action(a: FakeACPAgent, sid: str) -> None:
            resp = await a.ask_permission(
                sid,
                ToolCallUpdate(tool_call_id=tool_call_id, title=title),
                [
                    PermissionOption(
                        kind="allow_once", name="Allow", optionId=allow_option_id
                    ),
                    PermissionOption(
                        kind="reject_once", name="Reject", optionId="reject-1"
                    ),
                ],
            )
            a.approved = allow_option_id in str(resp)

        self._script.append(_action)
        return self

    # -- agent-side emit helpers -------------------------------------------------

    async def say(self, session_id: str, text: str) -> None:
        await self.emit(session_id, update_agent_message_text(text))

    async def update_cursor_todos(self, session_id: str, *contents: str) -> None:
        """Push Cursor's ``cursor/update_todos`` extension notification."""
        todos = [
            {"id": f"t{index}", "content": content, "status": "pending"}
            for index, content in enumerate(contents)
        ]
        await self._conn_for(session_id).ext_notification(
            "cursor/update_todos", {"sessionId": session_id, "todos": todos}
        )

    def _conn_for(self, session_id: str) -> AgentSideConnection:
        conn = self._conns_by_session.get(session_id, self._current_conn)
        assert conn is not None, "agent not connected yet"
        return conn

    async def emit(self, session_id: str, update: Any) -> None:
        await self._conn_for(session_id).session_update(session_id, update)

    async def selects_on_its_own(
        self, session_id: str, option_id: str, value: str
    ) -> None:
        """Change an option agent-side and push ``config_option_update``, as
        Copilot's in-session ``/model`` does."""
        self._config_options = await self._select(session_id, option_id, value)
        await self.push_config_options(session_id, self._config_options)

    async def push_config_options(
        self, session_id: str, options: Sequence[SessionConfigOption]
    ) -> None:
        await self.emit(
            session_id,
            ConfigOptionUpdate(
                session_update="config_option_update", config_options=list(options)
            ),
        )

    async def ask_permission(
        self, session_id: str, tool_call: Any, options: list[Any]
    ) -> Any:
        resp = await self._conn_for(session_id).request_permission(
            options=options, session_id=session_id, tool_call=tool_call
        )
        self.permission_responses.append(resp)
        return resp

    def mcp_server(self, session_id: str, server: str) -> Any:
        """The MCP server config named ``server`` advertised for this session."""
        config = next(
            (
                config
                for config in self._mcp_servers_by_session[session_id]
                if getattr(config, "name", None) == server
            ),
            None,
        )
        if config is None:
            raise ValueError(f"MCP server {server!r} was not advertised")
        return config

    async def call_mcp_tool(
        self,
        *,
        session_id: str,
        server: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        """Call a tool on an MCP server advertised for this session."""
        config = self.mcp_server(session_id, server)
        async with mcp_session(config.url, BandMCPTransport(config.type)) as client:
            result = await client.call_tool(tool_name, arguments)

        if result.isError:
            raise RuntimeError(f"MCP tool {tool_name!r} failed: {result.content}")
        return result.content

    async def list_mcp_tools(self, *, session_id: str, server: str) -> list[Tool]:
        """The tools a session's MCP server lists."""
        config = self.mcp_server(session_id, server)
        async with mcp_session(config.url, BandMCPTransport(config.type)) as client:
            return (await client.list_tools()).tools

    # -- acp.Agent protocol ------------------------------------------------------

    def on_connect(self, conn: AgentSideConnection) -> None:
        self._current_conn = conn

    async def initialize(
        self, protocol_version: int, client_capabilities: Any = None, **kwargs: Any
    ) -> InitializeResponse:
        del client_capabilities, kwargs
        return InitializeResponse(
            protocol_version=protocol_version,
            agent_capabilities=AgentCapabilities(
                load_session=self._supports_session_load,
                mcp_capabilities=McpCapabilities(http=self._http, sse=self._sse),
                session_capabilities=SessionCapabilities(
                    close=SessionCloseCapabilities()
                ),
            ),
        )

    async def authenticate(self, method_id: str, **kwargs: Any) -> None:
        """Accept any auth method (Cursor's ``cursor_login``, for one)."""
        del kwargs
        self.auth_methods.append(method_id)

    async def load_session(
        self, cwd: str, session_id: str, mcp_servers: Any = None, **kwargs: Any
    ) -> LoadSessionResponse:
        del cwd, kwargs
        self.session_load_requests.append(session_id)
        if self._session_load_error is not None:
            raise self._session_load_error
        if session_id not in self._persisted_sessions:
            raise RequestError.resource_not_found()
        self._conns_by_session[session_id] = self._current_conn
        self._mcp_servers_by_session[session_id] = list(mcp_servers or [])
        return LoadSessionResponse(config_options=self._config_options)

    async def new_session(
        self, cwd: str, mcp_servers: Any = None, **kwargs: Any
    ) -> NewSessionResponse:
        del kwargs
        sid = f"fake-session-{len(self.sessions) + 1}"
        self.sessions.append(
            {"session_id": sid, "cwd": cwd, "mcp_servers": list(mcp_servers or [])}
        )
        self._mcp_servers_by_session[sid] = list(mcp_servers or [])
        self._conns_by_session[sid] = self._current_conn
        return NewSessionResponse(session_id=sid, config_options=self._config_options)

    async def set_config_option(
        self,
        config_id: str,
        session_id: str,
        value: str,
        **kwargs: Any,
    ) -> SetSessionConfigOptionResponse:
        """Apply one advertised select option and return the full live catalog."""
        del kwargs
        self.config_option_requests.append((session_id, config_id, value))
        if self._hangs_up_on_config:
            self._hangs_up_on_config = False
            self.hang_up()
            await asyncio.Event().wait()
        self._config_options = await self._select(session_id, config_id, value)
        if self._reply_gate is not None:
            self._reply_gate.received.set()
            await self._reply_gate.release.wait()
        return SetSessionConfigOptionResponse(config_options=self._config_options)

    async def _select(
        self, session_id: str, option_id: str, value: str
    ) -> list[SessionConfigOption]:
        if self._config_option_handler is not None:
            return list(
                await self._config_option_handler(self, session_id, option_id, value)
            )
        return [
            option.model_copy(update={"current_value": value})
            if option.id == option_id and isinstance(option, SessionConfigOptionSelect)
            else option
            for option in self._config_options
        ]

    def current_value(self, option_id: str) -> str | None:
        """The value the agent currently has selected for ``option_id``."""
        option = find_select(self._config_options, option_id)
        return option.current_value if option is not None else None

    async def close_session(self, session_id: str, **kwargs: Any) -> None:
        """Record that the client closed a session before prompting it."""
        del kwargs
        self.closed_sessions.append(session_id)

    def config_selections(self, session_id: str | None = None) -> list[tuple[str, str]]:
        """Each ``(option, value)`` the client set, in order, on ``session_id``
        or across sessions."""
        return [
            (option, value)
            for sid, option, value in self.config_option_requests
            if session_id in (None, sid)
        ]

    def prompt_texts(self) -> list[str]:
        """Each received prompt's text, one string per prompt, in arrival order."""
        return [
            "\n".join(
                block.text
                for block in received["prompt"]
                if getattr(block, "text", None)
            )
            for received in self.prompts
        ]

    def session_ids(self) -> list[str]:
        """Each created session id, in creation order."""
        return [session["session_id"] for session in self.sessions]

    async def prompt(
        self, prompt: Any, session_id: str, message_id: str | None = None, **kwargs: Any
    ) -> PromptResponse:
        del message_id, kwargs
        self.prompts.append({"session_id": session_id, "prompt": prompt})
        if self._custom is not None:
            await self._custom(self, session_id)
        else:
            for action in self._script:
                await action(self, session_id)
        return PromptResponse(stop_reason="end_turn")


def select_option(
    option_id: str,
    current_value: str,
    values: Iterable[str],
    *,
    category: str | None = None,
) -> SessionConfigOptionSelect:
    """A concise ACP select catalog entry, optionally in a spec category."""
    return SessionConfigOptionSelect(
        id=option_id,
        name=option_id.replace("_", " ").title(),
        type="select",
        category=category,
        current_value=current_value,
        options=[
            SessionConfigSelectOption(value=value, name=value.title())
            for value in values
        ],
    )
