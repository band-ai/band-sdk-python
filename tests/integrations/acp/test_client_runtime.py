"""Tests for ACP runtime and client profiles."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from acp.client.connection import ClientSideConnection
from acp.exceptions import RequestError

from band.integrations.acp.client_profiles import (
    CursorACPClientProfile,
    NoopACPClientProfile,
)
from band.integrations.acp.client_runtime import (
    ACP_STDIO_LIMIT_BYTES,
    ACPCollectingClient,
    ACPRuntime,
    select_allow_option_id,
    tcp_spawn_process,
)


class TestSelectAllowOptionId:
    def test_prefers_allow_once_over_allow_always(self) -> None:
        options = [
            {"kind": "allow_always", "optionId": "always"},
            {"kind": "allow_once", "optionId": "once"},
        ]
        assert select_allow_option_id(options) == "once"

    def test_no_allow_option_returns_none(self) -> None:
        assert (
            select_allow_option_id([{"kind": "reject_once", "optionId": "no"}]) is None
        )

    def test_present_but_empty_option_id_is_not_dropped(self) -> None:
        """An explicit (if empty) optionId must not fall through to the snake_case
        alias and get dropped — coalesce on absence, not falsiness."""
        assert select_allow_option_id([{"kind": "allow_once", "optionId": ""}]) == ""


class TestACPCollectingClientProfiles:
    """Tests for ACP collecting client profile delegation."""

    @pytest.mark.asyncio
    async def test_noop_profile_ignores_extensions(self) -> None:
        client = ACPCollectingClient(profile=NoopACPClientProfile())

        method_result = await client.ext_method("unknown/method", {})
        await client.ext_notification("unknown/notification", {"sessionId": "sess-1"})

        assert method_result == {}
        assert client.get_collected_chunks("sess-1") == []

    @pytest.mark.asyncio
    async def test_cursor_profile_handles_methods_and_notifications(self) -> None:
        client = ACPCollectingClient(profile=CursorACPClientProfile())

        ask_result = await client.ext_method(
            "cursor/ask_question",
            {
                "options": [
                    {"optionId": "a", "name": "Option A"},
                    {"optionId": "b", "name": "Option B"},
                ]
            },
        )
        plan_result = await client.ext_method("cursor/create_plan", {"plan": "x"})
        await client.ext_notification(
            "cursor/update_todos",
            {
                "sessionId": "sess-1",
                "todos": [
                    {"content": "Read code", "completed": True},
                    {"content": "Write tests", "completed": False},
                ],
            },
        )
        await client.ext_notification(
            "cursor/task",
            {"sessionId": "sess-1", "result": "Refactored the module"},
        )

        chunks = client.get_collected_chunks("sess-1")
        assert ask_result == {"outcome": {"type": "selected", "optionId": "a"}}
        assert plan_result == {"outcome": {"type": "approved"}}
        assert [chunk.chunk_type for chunk in chunks] == ["plan", "text"]
        assert "[x] Read code" in chunks[0].content
        assert "Refactored the module" in chunks[1].content


class TestACPCollectingClientCoalescing:
    """Streamed text/thought deltas are coalesced into one chunk per run."""

    @staticmethod
    def _update(kind: str, text: str) -> MagicMock:
        u = MagicMock(session_update=kind)
        u.content = MagicMock(text=text)
        return u

    @pytest.mark.asyncio
    async def test_consecutive_text_deltas_merge_into_one_chunk(self) -> None:
        client = ACPCollectingClient()
        for part in ("The weather ", "is ", "sunny."):
            await client.session_update("s1", self._update("agent_message_chunk", part))

        chunks = client.get_collected_chunks("s1")
        assert [c.chunk_type for c in chunks] == ["text"]  # one chunk, not three
        assert chunks[0].content == "The weather is sunny."
        assert client.get_collected_text("s1") == "The weather is sunny."

    @pytest.mark.asyncio
    async def test_a_tool_call_splits_text_runs(self) -> None:
        client = ACPCollectingClient()
        await client.session_update(
            "s1", self._update("agent_message_chunk", "before ")
        )
        await client.session_update("s1", MagicMock(session_update="tool_call"))
        await client.session_update("s1", self._update("agent_message_chunk", "after"))

        kinds = [c.chunk_type for c in client.get_collected_chunks("s1")]
        assert kinds == [
            "text",
            "tool_call",
            "text",
        ]  # runs on either side stay distinct


class TestACPRuntime:
    """Tests for ACP runtime subprocess orchestration."""

    @pytest.mark.asyncio
    async def test_start_initializes_connection_and_authenticates(self) -> None:
        mock_conn = AsyncMock()
        mock_conn.initialize = AsyncMock(
            return_value=MagicMock(
                agent_capabilities=MagicMock(
                    load_session=True,
                    mcp_capabilities=MagicMock(http=False, sse=True),
                )
            )
        )
        mock_conn.authenticate = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=(mock_conn, MagicMock()))
        runtime = ACPRuntime(
            command=["codex"],
            auth_method="cursor_login",
            spawn_process=lambda *args, **kwargs: mock_ctx,
        )

        await runtime.start()

        assert runtime._conn is mock_conn
        assert runtime._agent_mcp_transport == "sse"
        assert runtime._agent_supports_session_load
        mock_conn.initialize.assert_awaited_once_with(protocol_version=1)
        mock_conn.authenticate.assert_awaited_once_with(method_id="cursor_login")

    @pytest.mark.asyncio
    async def test_create_session_and_prompt_use_active_connection(self) -> None:
        mock_conn = AsyncMock()
        mock_conn.new_session = AsyncMock(return_value=MagicMock(session_id="sess-1"))
        mock_conn.prompt = AsyncMock()
        runtime = ACPRuntime(command=["codex"])
        runtime._conn = mock_conn
        runtime._client = ACPCollectingClient()
        runtime._client._session_chunks["sess-1"] = []

        session_id = await runtime.create_session(cwd="/tmp", mcp_servers=[])
        chunks = await runtime.prompt(session_id=session_id, prompt_text="hello")

        assert session_id == "sess-1"
        assert chunks == []
        mock_conn.new_session.assert_awaited_once_with(cwd="/tmp", mcp_servers=[])
        mock_conn.prompt.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_load_session_uses_only_a_declared_capability(self) -> None:
        mock_conn = AsyncMock()
        mock_conn.load_session = AsyncMock(return_value=MagicMock())
        runtime = ACPRuntime(command=["codex"])
        runtime._conn = mock_conn

        assert not await runtime.load_session(
            cwd="/tmp", session_id="sess-1", mcp_servers=[]
        )
        mock_conn.load_session.assert_not_awaited()

        runtime._agent_supports_session_load = True
        assert await runtime.load_session(
            cwd="/tmp", session_id="sess-1", mcp_servers=[]
        )
        mock_conn.load_session.assert_awaited_once_with(
            cwd="/tmp", session_id="sess-1", mcp_servers=[]
        )

    @pytest.mark.asyncio
    async def test_load_session_handles_an_unavailable_persisted_session(self) -> None:
        mock_conn = AsyncMock()
        mock_conn.load_session = AsyncMock(
            side_effect=RequestError(-32002, "Session sess-1 not found")
        )
        runtime = ACPRuntime(command=["codex"])
        runtime._conn = mock_conn
        runtime._agent_supports_session_load = True

        assert not await runtime.load_session(
            cwd="/tmp", session_id="sess-1", mcp_servers=[]
        )

    @pytest.mark.asyncio
    async def test_load_session_timeout_is_treated_as_unavailable(self) -> None:
        mock_conn = AsyncMock()
        mock_conn.load_session = AsyncMock(side_effect=TimeoutError)
        runtime = ACPRuntime(command=["codex"])
        runtime._conn = mock_conn
        runtime._agent_supports_session_load = True

        with patch(
            "band.integrations.acp.client_runtime.ACP_SESSION_LOAD_TIMEOUT_SECONDS",
            0.01,
        ):
            assert not await runtime.load_session(
                cwd="/tmp", session_id="sess-1", mcp_servers=[]
            )

    @pytest.mark.asyncio
    async def test_load_session_propagates_non_session_errors(self) -> None:
        mock_conn = AsyncMock()
        mock_conn.load_session = AsyncMock(side_effect=RequestError.invalid_params())
        runtime = ACPRuntime(command=["codex"])
        runtime._conn = mock_conn
        runtime._agent_supports_session_load = True

        with pytest.raises(RequestError, match="Invalid params"):
            await runtime.load_session(cwd="/tmp", session_id="sess-1", mcp_servers=[])

    @pytest.mark.asyncio
    async def test_start_cleans_up_failed_initialize(self) -> None:
        mock_conn = AsyncMock()
        mock_conn.initialize = AsyncMock(side_effect=RuntimeError("boom"))
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=(mock_conn, MagicMock()))
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        runtime = ACPRuntime(
            command=["codex"],
            spawn_process=lambda *args, **kwargs: mock_ctx,
        )

        with pytest.raises(RuntimeError, match="boom"):
            await runtime.start()

        assert runtime._conn is None
        assert runtime._ctx is None
        mock_ctx.__aexit__.assert_awaited_once_with(None, None, None)

    @pytest.mark.asyncio
    async def test_start_cleans_up_failed_authenticate(self) -> None:
        mock_conn = AsyncMock()
        mock_conn.initialize = AsyncMock(return_value=MagicMock())
        mock_conn.authenticate = AsyncMock(side_effect=RuntimeError("auth failed"))
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=(mock_conn, MagicMock()))
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        runtime = ACPRuntime(
            command=["codex"],
            auth_method="cursor_login",
            spawn_process=lambda *args, **kwargs: mock_ctx,
        )

        with pytest.raises(RuntimeError, match="auth failed"):
            await runtime.start()

        assert runtime._conn is None
        assert runtime._ctx is None
        mock_ctx.__aexit__.assert_awaited_once_with(None, None, None)

    @pytest.mark.asyncio
    async def test_ensure_connection_respawns_when_allowed(self) -> None:
        mock_conn = AsyncMock()
        mock_conn.initialize = AsyncMock(return_value=MagicMock())
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=(mock_conn, MagicMock()))
        runtime = ACPRuntime(
            command=["codex"],
            spawn_process=lambda *args, **kwargs: mock_ctx,
        )

        conn = await runtime.ensure_connection(can_respawn=True)

        assert conn is mock_conn
        mock_conn.initialize.assert_awaited_once_with(protocol_version=1)

    @pytest.mark.asyncio
    async def test_set_permission_handler_delegates_to_client(self) -> None:
        runtime = ACPRuntime(command=["codex"])
        runtime._client = ACPCollectingClient()
        handler = AsyncMock(
            return_value={"outcome": {"outcome": "selected", "optionId": "p-once"}}
        )

        runtime.set_permission_handler("sess-1", handler)
        runtime.reset_session("sess-2")

        assert runtime._client._permission_handlers["sess-1"] is handler
        assert "sess-2" not in runtime._client._permission_handlers

    @pytest.mark.asyncio
    async def test_stop_exits_context_and_clears_state(self) -> None:
        mock_ctx = AsyncMock()
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        runtime = ACPRuntime(command=["codex"])
        runtime._ctx = mock_ctx
        runtime._conn = AsyncMock()
        runtime._client = ACPCollectingClient()

        await runtime.stop()

        assert runtime._ctx is None
        assert runtime._conn is None
        assert runtime._client is None
        mock_ctx.__aexit__.assert_awaited_once_with(None, None, None)

    @pytest.mark.asyncio
    async def test_start_with_empty_command_forwards_no_positional_command(
        self, make_acp_transport
    ) -> None:
        """TCP transports pass command=[] — the runtime must forward no positional
        command args (host/port live in the injected spawn_process)."""
        transport = make_acp_transport()
        runtime = ACPRuntime(command=[], spawn_process=transport)

        await runtime.start()

        args, kwargs = transport.last_call
        assert args == ()  # no executable/args splatted for a connect-only transport
        assert kwargs["transport_kwargs"] == {"limit": ACP_STDIO_LIMIT_BYTES}


class TestTCPSpawnProcess:
    """Tests for the TCP connect-only spawn_process seam.

    Uses a real loopback server (no patching): the factory must open a socket,
    build a live ACP connection over it, yield ``(conn, writer)``, and close both
    on exit — ignoring the subprocess-shaped args the runtime forwards.
    """

    @pytest.mark.asyncio
    async def test_connects_and_cleans_up(self) -> None:
        async def _handle(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                await reader.read()  # hold the connection until the client closes
            finally:
                writer.close()

        server = await asyncio.start_server(_handle, "127.0.0.1", 0)
        host, port = server.sockets[0].getsockname()[:2]

        async with server:
            spawn = tcp_spawn_process(host, port)
            client = ACPCollectingClient()

            # Forward subprocess-shaped args the runtime would pass; TCP ignores them.
            cm = spawn(
                client,
                "ignored-executable",
                "ignored-arg",
                env=None,
                transport_kwargs={"limit": 1024},
            )
            conn, writer = await cm.__aenter__()
            try:
                assert isinstance(writer, asyncio.StreamWriter)
                # A real ClientSideConnection built from the socket, not a mock.
                assert isinstance(conn, ClientSideConnection)
            finally:
                await cm.__aexit__(None, None, None)

            assert writer.is_closing()

    @pytest.mark.asyncio
    async def test_cleans_up_when_body_raises(self) -> None:
        """The connect CM must close the transport even if the caller raises
        mid-session (e.g. initialize fails) — the `finally` path, not just happy exit."""

        async def _handle(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                await reader.read()
            finally:
                writer.close()

        server = await asyncio.start_server(_handle, "127.0.0.1", 0)
        host, port = server.sockets[0].getsockname()[:2]

        async with server:
            spawn = tcp_spawn_process(host, port)
            captured: dict[str, asyncio.StreamWriter] = {}

            with pytest.raises(RuntimeError, match="boom"):
                async with spawn(ACPCollectingClient()) as (_conn, writer):
                    captured["writer"] = writer
                    raise RuntimeError("boom")

            assert captured["writer"].is_closing()
