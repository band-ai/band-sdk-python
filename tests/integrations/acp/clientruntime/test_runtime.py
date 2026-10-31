"""ACP runtime connection and session orchestration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from acp.exceptions import RequestError
from acp.schema import (
    ClientCapabilities,
    DeclineElicitationResponse,
    NewSessionResponse,
)

from band.integrations.acp.client_runtime import (
    ACP_STDIO_LIMIT_BYTES,
    ACPCollectingClient,
    ACPRuntime,
)
from band.integrations.acp.session_config import select_ids
from band.integrations.mcp import BandMCPTransport
from tests.integrations.acp.acp_toolkit import FakeSpawn, select_option


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
        assert runtime._agent_mcp_transport is BandMCPTransport.SSE
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

        session_id = await runtime.create_session(cwd="/tmp", mcp_servers=[])
        chunks = await runtime.prompt(session_id=session_id, prompt_text="hello")

        assert session_id == "sess-1"
        assert chunks == []
        mock_conn.new_session.assert_awaited_once_with(cwd="/tmp", mcp_servers=[])
        mock_conn.prompt.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("ended_by", ["stop", "close_session"])
    async def test_an_ended_session_keeps_no_catalog(self, ended_by: str) -> None:
        # Session ids belong to the agent process; a respawn must not reuse
        # what the stopped process advertised.
        spawn = FakeSpawn()
        spawn.conn.new_session = AsyncMock(
            return_value=NewSessionResponse(
                session_id="sess-1",
                config_options=[select_option("model", "small", ["small", "large"])],
            )
        )
        runtime = ACPRuntime(command=["codex"], spawn_process=spawn)
        await runtime.start()
        await runtime.create_session_response(cwd="/tmp", mcp_servers=[])
        advertised = runtime.config_options("sess-1")

        match ended_by:
            case "stop":
                await runtime.stop()
            case "close_session":
                await runtime.close_session("sess-1")

        assert select_ids(advertised) == ("model",)
        assert runtime.config_options("sess-1") == ()

    @pytest.mark.asyncio
    async def test_close_session_uses_the_active_connection_when_supported(
        self,
    ) -> None:
        mock_conn = AsyncMock()
        runtime = ACPRuntime(command=["codex"])
        runtime._conn = mock_conn
        runtime._agent_supports_session_close = True

        await runtime.close_session("sess-1")

        mock_conn.close_session.assert_awaited_once_with("sess-1")

    @pytest.mark.asyncio
    async def test_close_session_requires_a_declared_capability(self) -> None:
        mock_conn = AsyncMock()
        runtime = ACPRuntime(command=["codex"])
        runtime._conn = mock_conn

        await runtime.close_session("sess-1")

        mock_conn.close_session.assert_not_awaited()

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
            "band.integrations.acp.sessions.ACP_SESSION_LOAD_TIMEOUT_SECONDS",
            0.01,
        ):
            assert not await runtime.load_session(
                cwd="/tmp", session_id="sess-1", mcp_servers=[]
            )

    @pytest.mark.asyncio
    async def test_load_session_treats_non_session_errors_as_miss(self) -> None:
        """Any protocol error on session/load is a recoverable load-miss: the
        caller falls back to a fresh session instead of the turn dying."""
        mock_conn = AsyncMock()
        mock_conn.load_session = AsyncMock(side_effect=RequestError.invalid_params())
        runtime = ACPRuntime(command=["codex"])
        runtime._conn = mock_conn
        runtime._agent_supports_session_load = True

        loaded = await runtime.load_session(
            cwd="/tmp", session_id="sess-1", mcp_servers=[]
        )

        assert loaded is False

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
    async def test_set_elicitation_handler_delegates_to_client(self) -> None:
        runtime = ACPRuntime(command=["codex"])
        runtime._client = ACPCollectingClient()
        handler = AsyncMock(return_value=DeclineElicitationResponse(action="decline"))

        runtime.set_elicitation_handler("sess-1", handler)
        assert runtime._client._elicitation_handlers["sess-1"] is handler
        runtime.reset_session("sess-1")
        assert "sess-1" not in runtime._client._elicitation_handlers

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
        runtime = ACPRuntime(command=[], cwd="/tmp/acp-room", spawn_process=transport)

        await runtime.start()

        args, kwargs = transport.last_call
        assert args == ()  # no executable/args splatted for a connect-only transport
        assert kwargs["cwd"] == "/tmp/acp-room"
        assert kwargs["transport_kwargs"] == {"limit": ACP_STDIO_LIMIT_BYTES}

    @pytest.mark.asyncio
    async def test_start_passes_stdio_limit_only_when_builtin_transport(
        self, make_acp_transport
    ) -> None:
        """Builtin transports get the raised stdio limit; injected ones do not."""

        injected = make_acp_transport()
        runtime = ACPRuntime(
            command=["omp", "acp"],
            spawn_process=injected,
            pass_builtin_transport_options=False,
        )
        await runtime.start()
        assert "transport_kwargs" not in injected.last_kwargs

        builtin = make_acp_transport()
        runtime = ACPRuntime(
            command=["omp", "acp"],
            spawn_process=builtin,
            pass_builtin_transport_options=True,
        )
        await runtime.start()
        assert builtin.last_kwargs["transport_kwargs"] == {
            "limit": ACP_STDIO_LIMIT_BYTES
        }

    @pytest.mark.asyncio
    async def test_start_forwards_client_capabilities_to_initialize(
        self, make_acp_transport
    ) -> None:
        caps = ClientCapabilities()
        transport = make_acp_transport()
        runtime = ACPRuntime(
            command=["omp", "acp"],
            spawn_process=transport,
            client_capabilities=caps,
        )
        await runtime.start()
        transport.conn.initialize.assert_awaited_once()
        assert (
            transport.conn.initialize.await_args.kwargs["client_capabilities"] is caps
        )

    @pytest.mark.asyncio
    async def test_start_passes_use_unstable_protocol_when_builtin(
        self, make_acp_transport
    ) -> None:
        transport = make_acp_transport()
        runtime = ACPRuntime(
            command=["omp", "acp"],
            spawn_process=transport,
            use_unstable_protocol=True,
            pass_builtin_transport_options=True,
        )
        await runtime.start()
        assert transport.last_kwargs.get("use_unstable_protocol") is True
