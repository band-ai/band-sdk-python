from __future__ import annotations

import asyncio
import json
import socket
from contextlib import suppress
from unittest.mock import AsyncMock, MagicMock

import pytest
from band_rest import ListAgentChatParticipantsResponse
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel

from band.core.exceptions import BandToolError
from band.integrations.mcp.engine import (
    EngineSpec,
    MCPToolRegistration,
    build_band_mcp_tool_registrations,
    build_resolved_band_mcp_tool_registrations,
)
from band.integrations.mcp.local_server import (
    LOCAL_MCP_HOST,
    SERVER_STOP_TIMEOUT_S,
    LocalMCPServer,
)
from band.runtime.custom_tools import get_custom_tool_name
from band.runtime.tools import AgentTools

from tests.lifecycle import elapsed, held_open, running


class EchoInput(BaseModel):
    """Echo text back to the caller."""

    message: str


async def echo_tool(input_data: EchoInput) -> dict[str, str]:
    return {"echo": input_data.message}


def _text_of(result: CallToolResult) -> str:
    """Narrow the first content block to `TextContent` and return its text."""
    block = result.content[0]
    assert isinstance(block, TextContent), block
    return block.text


def _registration_named(
    registrations: list[MCPToolRegistration], name: str
) -> MCPToolRegistration:
    return next(item for item in registrations if item.name == name)


def _echo_tool_registration() -> MCPToolRegistration:
    # A registration's execute() always returns a wire-serialized string: the
    # dynamic handler build_engine() creates always declares -> str, so
    # FastMCP's structured-output validation rejects a raw dict here.
    async def execute(arguments: dict[str, str]) -> str:
        return json.dumps({"echo": arguments["message"]})

    return MCPToolRegistration(
        name="echo",
        description="Echo a message",
        input_model=EchoInput,
        execute=execute,
    )


async def _session_lists_only_echo(session: ClientSession) -> None:
    tools_result = await session.list_tools()
    assert [tool.name for tool in tools_result.tools] == ["echo"]


async def _call_echo(session: ClientSession, message: str) -> None:
    result = await session.call_tool("echo", {"message": message})
    assert not result.isError
    assert json.loads(_text_of(result)) == {"echo": message}


def _assert_fully_stopped(server: LocalMCPServer) -> None:
    assert server._serve_task is None
    assert server._socket is None
    assert server._port is None
    assert server._uvicorn_server is None


class TestBuildBandMcpToolRegistrations:
    def test_includes_builtin_and_custom_tools(self) -> None:
        agent_tools = AgentTools("room-123", MagicMock(), [])

        registrations = build_band_mcp_tool_registrations(
            agent_tools,
            additional_tools=[(EchoInput, echo_tool)],
        )

        tool_names = {registration.name for registration in registrations}
        assert "band_send_message" in tool_names
        assert get_custom_tool_name(EchoInput) in tool_names

    def test_rejects_duplicate_tool_names(self) -> None:
        agent_tools = AgentTools("room-123", MagicMock(), [])

        with pytest.raises(ValueError, match="Duplicate MCP tool names"):
            build_band_mcp_tool_registrations(
                agent_tools,
                additional_tools=[
                    (EchoInput, echo_tool),
                    (EchoInput, echo_tool),
                ],
            )

    @pytest.mark.asyncio
    async def test_resolved_registrations_advertise_chat_id(self) -> None:
        """The embedded door's uniform wrap advertises ``chat_id`` (the
        canonical name); ``room_id`` remains an accepted input alias only --
        see test_resolved_registrations_dispatch_by_room_id below."""
        tools_by_room = {
            "room-123": AgentTools("room-123", MagicMock(), []),
        }
        registrations = build_resolved_band_mcp_tool_registrations(
            get_tools=tools_by_room.get
        )

        registration = _registration_named(registrations, "band_get_participants")
        schema = registration.input_model.model_json_schema()

        assert "chat_id" in schema["properties"]
        assert "chat_id" in schema["required"]
        assert "room_id" not in schema["properties"]

    @pytest.mark.asyncio
    async def test_resolved_registrations_dispatch_by_room_id(self) -> None:
        rest = MagicMock()
        rest.agent_api_participants = MagicMock()
        rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
            return_value=ListAgentChatParticipantsResponse(data=[])
        )

        room_tools = AgentTools("room-123", rest, [])
        registrations = build_resolved_band_mcp_tool_registrations(
            get_tools={"room-123": room_tools}.get
        )
        registration = _registration_named(registrations, "band_get_participants")

        await registration.execute({"room_id": "room-123"})

        rest.agent_api_participants.list_agent_chat_participants.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_resolved_send_message_errors_include_available_handles(
        self,
    ) -> None:
        room_tools = AgentTools(
            "room-123",
            MagicMock(),
            [
                {"id": "user-1", "name": "Alice", "handle": "@alice"},
                {"id": "self", "name": "Self", "handle": "@self"},
            ],
            agent_id="self",
        )
        registrations = build_resolved_band_mcp_tool_registrations(
            get_tools={"room-123": room_tools}.get
        )
        registration = _registration_named(registrations, "band_send_message")

        with pytest.raises(BandToolError) as exc_info:
            await registration.execute(
                {"room_id": "room-123", "content": "hello", "mentions": []}
            )

        message = str(exc_info.value)
        assert "At least one mention is required" in message
        assert "@alice" in message
        assert "@self" not in message
        # The enricher must not re-append handles that the error already carries.
        assert message.count("Available handles:") == 1
        assert message.count("@alice") == 1


class TestLocalMcpServer:
    def test_accepts_explicit_non_loopback_bind_host(self) -> None:
        """An explicit non-loopback bind is a supported opt-in (containerized
        MCP clients reach back over the docker bridge); construction must not
        reject it."""
        server = LocalMCPServer(
            name="test-local-mcp",
            tool_registrations=[],
            host="0.0.0.0",
        )
        assert server._host == "0.0.0.0"

    @pytest.mark.asyncio
    async def test_serves_sse_tools_on_localhost(self) -> None:
        server = LocalMCPServer(
            name="test-local-mcp",
            tool_registrations=[_echo_tool_registration()],
            port_min=0,
            port_max=0,
        )

        async with running(server):
            assert server.url.startswith(f"http://{LOCAL_MCP_HOST}:")

            async with sse_client(server.url) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    await _session_lists_only_echo(session)
                    await _call_echo(session, "hello")

    @pytest.mark.timeout(SERVER_STOP_TIMEOUT_S + 15.0)
    @pytest.mark.asyncio
    async def test_stop_returns_promptly_with_a_still_open_sse_connection(
        self,
    ) -> None:
        """Regression: an MCP client (e.g. OpenCode) holds its `/sse` GET
        open for the life of its session and may never close it -- stop()
        must force it closed rather than hang on uvicorn's unbounded default
        graceful-shutdown wait.

        Measures wall-clock time around a bare ``server.stop()`` (wrapping
        it in ``asyncio.wait_for`` would cancel it externally and mask a
        real hang). ``pytest.mark.timeout`` above is the backstop, matching
        how this hang is actually caught (pytest-timeout, not an asyncio
        timeout).
        """
        server = LocalMCPServer(
            name="test-local-mcp-stop",
            tool_registrations=[],
            port_min=0,
            port_max=0,
        )
        async with running(server):

            async def connect(ready: asyncio.Event) -> None:
                with suppress(Exception):
                    async with sse_client(server.url) as (read_stream, write_stream):
                        async with ClientSession(read_stream, write_stream) as session:
                            await session.initialize()
                            ready.set()
                            await asyncio.sleep(60)  # never closes on its own

            async with held_open(connect):
                stop_elapsed = await elapsed(server.stop())

            assert stop_elapsed < SERVER_STOP_TIMEOUT_S + 5.0, (
                f"stop() took {stop_elapsed:.1f}s -- graceful shutdown is not "
                "bounded by SERVER_STOP_TIMEOUT_S"
            )

    # 30s default barely fits on GitHub Actions Python 3.12 runners — the
    # streamable-HTTP loopback initialization spends most of that on uvicorn
    # startup. Bump to 90s to absorb runner I/O variance (test passes in ~1s
    # locally; this only widens the safety margin on CI).
    @pytest.mark.timeout(90)
    @pytest.mark.asyncio
    async def test_serves_streamable_http_tools_on_localhost(self) -> None:
        server = LocalMCPServer(
            name="test-local-mcp-http",
            tool_registrations=[_echo_tool_registration()],
            port_min=0,
            port_max=0,
        )

        async with running(server):
            assert server.http_url.startswith(f"http://{LOCAL_MCP_HOST}:")

            async with streamablehttp_client(server.http_url) as (
                read_stream,
                write_stream,
                _,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    await _session_lists_only_echo(session)
                    await _call_echo(session, "hello")

    @pytest.mark.asyncio
    async def test_stop_cleans_up_state_even_if_serve_task_crashed(self) -> None:
        """Regression: stop() used to skip socket close and state reset when
        the serve task crashed with anything but CancelledError -- the bare
        ``await self._serve_task`` re-raised past the cleanup code below it,
        leaking the socket and leaving stale state for the next start()."""
        server = LocalMCPServer(
            name="test-crash", tool_registrations=[], port_min=0, port_max=0
        )
        reserved_socket, port = server._reserve_socket()
        server._socket = reserved_socket
        server._port = port

        async def _raise() -> None:
            raise RuntimeError("simulated serve-task crash")

        server._serve_task = asyncio.create_task(_raise())

        await server.stop()  # must not raise, and must still clean up

        _assert_fully_stopped(server)

    @pytest.mark.asyncio
    async def test_is_running_reflects_a_crashed_serve_task(self) -> None:
        """A caller holding a reference to this server (host/port, session
        config) needs a way to notice its serve task died on its own --
        ``is_running`` is that check, independent of ``stop()`` ever running."""
        server = LocalMCPServer(
            name="test-is-running", tool_registrations=[], port_min=0, port_max=0
        )
        reserved_socket, port = server._reserve_socket()
        server._socket = reserved_socket
        server._port = port

        async def _raise() -> None:
            raise RuntimeError("simulated serve-task crash")

        server._serve_task = asyncio.create_task(_raise())
        assert server.is_running  # task created, hasn't run yet

        with suppress(RuntimeError):
            await server._serve_task
        assert server.is_running is False

        await server.stop()

    @pytest.mark.asyncio
    async def test_start_forwards_real_host_to_build_engine(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: build_engine must be told the real bind host, or
        FastMCP wrongly assumes loopback and locks DNS-rebinding protection
        to 127.0.0.1/localhost only -- even for a non-loopback Docker-
        callback bind (see LocalMCPServer's class docstring)."""
        import band.integrations.mcp.local_server as local_server_mod

        seen_hosts: list[str] = []
        real_build_engine = local_server_mod.build_engine

        def spy_build_engine(
            spec: EngineSpec,
            *,
            host: str = "127.0.0.1",
            transport_security: TransportSecuritySettings | None = None,
            sse_path: str = "/sse",
            message_path: str = "/messages/",
            streamable_http_path: str = "/mcp",
        ) -> FastMCP:
            seen_hosts.append(host)
            return real_build_engine(
                spec,
                host=host,
                transport_security=transport_security,
                sse_path=sse_path,
                message_path=message_path,
                streamable_http_path=streamable_http_path,
            )

        monkeypatch.setattr(local_server_mod, "build_engine", spy_build_engine)

        server = LocalMCPServer(
            name="test-host-forwarding",
            tool_registrations=[],
            host="0.0.0.0",
            port_min=0,
            port_max=0,
        )
        async with running(server):
            pass

        assert seen_hosts == ["0.0.0.0"]

    @pytest.mark.asyncio
    async def test_start_closes_socket_when_engine_construction_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: a failure between socket reservation and the uvicorn
        serve task starting (e.g. build_engine raising) must still close the
        reserved socket, not leak a bound-and-listening fd."""
        import band.integrations.mcp.local_server as local_server_mod

        server = LocalMCPServer(
            name="test-engine-failure", tool_registrations=[], port_min=0, port_max=0
        )
        real_reserve_socket = server._reserve_socket
        reserved: list[socket.socket] = []

        def capturing_reserve_socket() -> tuple[socket.socket, int]:
            sock, port = real_reserve_socket()
            reserved.append(sock)
            return sock, port

        server._reserve_socket = capturing_reserve_socket  # type: ignore[method-assign]

        def failing_build_engine(*args: object, **kwargs: object) -> FastMCP:
            raise RuntimeError("simulated engine construction failure")

        monkeypatch.setattr(local_server_mod, "build_engine", failing_build_engine)

        with pytest.raises(RuntimeError, match="simulated engine construction failure"):
            await server.start()

        assert len(reserved) == 1
        assert reserved[0].fileno() == -1  # closed, not leaked
        assert server._socket is None
        assert server._port is None

    @pytest.mark.asyncio
    async def test_concurrent_start_calls_are_serialized(self) -> None:
        """start()/start() must not race: the second call, once it acquires
        the lifecycle lock, sees the first's already-running server and
        no-ops rather than binding a second socket."""
        server = LocalMCPServer(
            name="test-concurrent-start",
            tool_registrations=[],
            port_min=0,
            port_max=0,
        )
        real_reserve_socket = server._reserve_socket
        reserve_calls = []

        def counting_reserve_socket() -> tuple[socket.socket, int]:
            result = real_reserve_socket()
            reserve_calls.append(result)
            return result

        server._reserve_socket = counting_reserve_socket  # type: ignore[method-assign]

        try:
            await asyncio.gather(server.start(), server.start())
            assert server.port is not None
            # A broken lock letting both calls bind would call this twice.
            assert len(reserve_calls) == 1
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_start_stop_start_cycle_rebuilds_engine(self) -> None:
        """Session managers are single-use (mcp.server.streamable_http_manager);
        a second start() must construct a fresh engine, not reuse a stale one."""
        server = LocalMCPServer(
            name="test-start-stop-start",
            tool_registrations=[_echo_tool_registration()],
            port_min=0,
            port_max=0,
        )

        await server.start()
        await server.stop()
        async with running(server):
            async with streamablehttp_client(server.http_url) as (
                read_stream,
                write_stream,
                _,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    await _call_echo(session, "hi")
