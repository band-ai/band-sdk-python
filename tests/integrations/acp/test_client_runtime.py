"""Tests for ACP runtime and client profiles."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from thenvoi.integrations.acp.client_profiles import (
    CursorACPClientProfile,
    NoopACPClientProfile,
)
from thenvoi.integrations.acp.client_runtime import ACPCollectingClient, ACPRuntime


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


class TestACPRuntime:
    """Tests for ACP runtime subprocess orchestration."""

    @pytest.mark.asyncio
    async def test_start_initializes_connection_and_authenticates(self) -> None:
        mock_conn = AsyncMock()
        mock_conn.initialize = AsyncMock(
            return_value=MagicMock(
                agent_capabilities=MagicMock(
                    mcp_capabilities=MagicMock(http=False, sse=True)
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
        handler = AsyncMock(return_value={"outcome": {"outcome": "allowed"}})

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
