"""Scripted, in-process fake ACP agent (the peer an ACP client drives)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
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
    InitializeResponse,
    LoadSessionResponse,
    McpCapabilities,
    NewSessionResponse,
    PermissionOption,
    PromptResponse,
    SessionCapabilities,
    SessionCloseCapabilities,
    SessionConfigOptionSelect,
    SetSessionConfigOptionResponse,
    ToolCallUpdate,
    Usage,
)
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from band.integrations.acp.session_config import SessionConfigOption

PromptHandler = Callable[["FakeACPAgent", str], Awaitable[None]]
ConfigOptionHandler = Callable[
    ["FakeACPAgent", str, str, str], Awaitable[Sequence[SessionConfigOption]]
]


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
        # Observability for assertions:
        self.sessions: list[dict[str, Any]] = []
        self._mcp_servers_by_session: dict[str, list[Any]] = {}
        self.prompts: list[dict[str, Any]] = []
        self.session_load_requests: list[str] = []
        self.permission_responses: list[Any] = []
        self.config_option_requests: list[tuple[str, str, str]] = []
        self.closed_sessions: list[str] = []
        self.approved: bool | None = None
        self._usage: Usage | None = None
        self.ext_method_results: list[dict[str, Any]] = []

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

    def will_say(self, text: str) -> FakeACPAgent:
        self._script.append(lambda a, sid: a.say(sid, text))
        return self

    def reset_script(self) -> FakeACPAgent:
        """Clear every scripted action so far.

        A multi-stage test simulating a restart across two adapter
        lifecycles shares one agent, but each phase's script should run
        independently -- otherwise phase 2's prompt would replay phase 1's
        actions too, since ``_script`` accumulates across every ``will_*``
        call for the object's lifetime.
        """
        self._script = []
        return self

    def reports_usage(self, usage: Usage) -> FakeACPAgent:
        """Make every ``session/prompt`` response carry this standard ACP
        ``usage`` value -- unset (the default) mirrors an agent that never
        reports it, so ``SimpleAdapter.emit_usage`` stays a no-op."""
        self._usage = usage
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
    ) -> FakeACPAgent:
        """Call an advertised MCP tool between ACP call and result updates."""

        async def _action(a: FakeACPAgent, sid: str) -> None:
            await a.emit(
                sid, start_tool_call(tool_call_id, tool_name, raw_input=arguments)
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

    def will_call_ext_method(self, method: str, params: dict[str, Any]) -> FakeACPAgent:
        """Send a client-bound extension request and record its response.

        Exercises a client profile's ``ext_method`` handling (e.g. Kiro's
        ``_kiro.dev/mcp/oauth_request``) over a real ACP connection, not just
        by calling the profile object directly.
        """

        async def _action(a: FakeACPAgent, sid: str) -> None:
            a.ext_method_results.append(
                await a._conn_for(sid).ext_method(method, params)
            )

        self._script.append(_action)
        return self

    def will_send_ext_notification(
        self, method: str, params: dict[str, Any]
    ) -> FakeACPAgent:
        """Send a client-bound extension notification (``sessionId`` auto-filled).

        Extension notifications aren't implicitly connection-scoped, so a real
        agent includes the session id in the payload itself; this mirrors that
        without making every call site repeat it.
        """

        async def _action(a: FakeACPAgent, sid: str) -> None:
            await a._conn_for(sid).ext_notification(
                method, {"sessionId": sid, **params}
            )

        self._script.append(_action)
        return self

    def will_plan(self, *steps: str) -> FakeACPAgent:
        self._script.append(
            lambda a, sid: a.emit(sid, update_plan([plan_entry(s) for s in steps]))
        )
        return self

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

    def _conn_for(self, session_id: str) -> AgentSideConnection:
        conn = self._conns_by_session.get(session_id, self._current_conn)
        assert conn is not None, "agent not connected yet"
        return conn

    async def emit(self, session_id: str, update: Any) -> None:
        await self._conn_for(session_id).session_update(session_id, update)

    async def ask_permission(
        self, session_id: str, tool_call: Any, options: list[Any]
    ) -> Any:
        resp = await self._conn_for(session_id).request_permission(
            options=options, session_id=session_id, tool_call=tool_call
        )
        self.permission_responses.append(resp)
        return resp

    async def call_mcp_tool(
        self,
        *,
        session_id: str,
        server: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        """Call a named streamable-HTTP MCP server advertised for this session."""
        server_config = next(
            (
                config
                for config in self._mcp_servers_by_session[session_id]
                if getattr(config, "name", None) == server
            ),
            None,
        )
        if server_config is None:
            raise ValueError(f"MCP server {server!r} was not advertised")
        if getattr(server_config, "type", None) != "http":
            raise ValueError(f"MCP server {server!r} does not use streamable HTTP")

        async with (
            streamable_http_client(server_config.url) as (
                read_stream,
                write_stream,
                _,
            ),
            ClientSession(read_stream, write_stream) as client,
        ):
            await client.initialize()
            result = await client.call_tool(tool_name, arguments)

        if result.isError:
            raise RuntimeError(f"MCP tool {tool_name!r} failed: {result.content}")
        return result.structuredContent or result.content

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

    async def load_session(
        self, cwd: str, session_id: str, mcp_servers: Any = None, **kwargs: Any
    ) -> LoadSessionResponse:
        del cwd, mcp_servers, kwargs
        self.session_load_requests.append(session_id)
        if self._session_load_error is not None:
            raise self._session_load_error
        if session_id not in self._persisted_sessions:
            raise RequestError.resource_not_found()
        self._conns_by_session[session_id] = self._current_conn
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
        if self._config_option_handler is not None:
            self._config_options = list(
                await self._config_option_handler(self, session_id, config_id, value)
            )
            return SetSessionConfigOptionResponse(config_options=self._config_options)

        updated: list[SessionConfigOption] = []
        for option in self._config_options:
            if option.id == config_id and isinstance(option, SessionConfigOptionSelect):
                updated.append(option.model_copy(update={"current_value": value}))
            else:
                updated.append(option)
        self._config_options = updated
        return SetSessionConfigOptionResponse(config_options=self._config_options)

    async def close_session(self, session_id: str, **kwargs: Any) -> None:
        """Record that the client closed a session before prompting it."""
        del kwargs
        self.closed_sessions.append(session_id)

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
        return PromptResponse(stop_reason="end_turn", usage=self._usage)
