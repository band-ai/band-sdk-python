"""Public invocation cancellation keeps settlement and discovery custody."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest

from band.core.exceptions import CleanupIncompleteError
from band.integrations.codex import CodexJsonRpcError
from band.integrations.codex.mcp import CodexMcpServer
from band.integrations.codex.types import CodexRequestMethod, CodexSessionState
from band.testing import FakeAgentTools
from tests.adapters.codex.kit import make_platform_message
from tests.adapters.codex.mcpkit import (
    StatusClient,
    mcp_adapter,
    private_servers,
)


def message_task(adapter) -> asyncio.Task[None]:
    return asyncio.create_task(
        adapter.on_message(
            msg=make_platform_message(room_id="a"),
            tools=FakeAgentTools(),
            history=CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="a",
        )
    )


class TestMcpCancellation:
    @pytest.mark.looptime
    async def test_external_startup_cancel_outlasting_grace_retains_custody(
        self,
        tmp_path,
    ) -> None:
        entered, settling, release, settled = (asyncio.Event() for _ in range(4))

        async def resolve(_workspace: str) -> Mapping[str, CodexMcpServer]:
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                settling.set()
                await release.wait()
                settled.set()
                raise

        adapter, launches = mcp_adapter(
            tmp_path, [StatusClient()], resolve, turn_settle_timeout_s=0.02
        )
        await adapter.on_started("Fixture", "Cancellation custody")
        running = message_task(adapter)
        try:
            await entered.wait()
            room = adapter._room_clients["a"]
            invocation = room.startup_owner.task
            assert invocation is not None
            turn_release = adapter._turn_release["a"]
            invocation.cancel()
            await settling.wait()
            outcome = await asyncio.gather(
                adapter.on_cleanup("a"), return_exceptions=True
            )
            assert isinstance(outcome[0], CleanupIncompleteError)
            assert invocation.cancelling() == 1
            assert not invocation.done()
            assert not settled.is_set()
            assert not turn_release.done()
            assert adapter._turn_release["a"] is turn_release
            with pytest.raises(ValueError):
                adapter._workspaces.claim("b", room.workspace)
            assert launches == []
            release.set()
            await asyncio.gather(running, return_exceptions=True)
            await adapter.on_cleanup("a")
            assert settled.is_set()
            assert "a" not in adapter._workspaces.rooms
        finally:
            release.set()
            await asyncio.gather(running, return_exceptions=True)
            await adapter.cleanup_all()
            adapter._workspaces.release("b")

    @pytest.mark.looptime
    async def test_cleanup_disarms_readiness_deadline_during_settlement(
        self, tmp_path, monkeypatch
    ) -> None:
        entered, settling, release, settled = (asyncio.Event() for _ in range(4))
        monkeypatch.setattr("band.adapters.codex._MCP_READINESS_TIMEOUT_S", 0.1)

        class SettlingClient(StatusClient):
            async def request(
                self, method: str, params: dict[str, Any] | None = None, **kwargs: Any
            ) -> dict[str, Any]:
                if method == "mcpServerStatus/list":
                    self.requests.append((method, params or {}))
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        settling.set()
                        await release.wait()
                        settled.set()
                        raise
                return await super().request(method, params, **kwargs)

        client = SettlingClient()
        adapter, _ = mcp_adapter(
            tmp_path, [client], private_servers, turn_settle_timeout_s=0.02
        )
        await adapter.on_started("Fixture", "Readiness custody")
        running = message_task(adapter)
        cleanup = None
        try:
            await entered.wait()
            room = adapter._room_clients["a"]
            invocation = room.startup_owner.task
            assert invocation is not None
            turn_release = adapter._turn_release["a"]
            cleanup = asyncio.create_task(adapter.on_cleanup("a"))
            await settling.wait()
            await asyncio.sleep(
                0.2
            )  # Cross the original readiness deadline while settling.
            assert invocation.cancelling() == 1
            assert not invocation.done()
            assert not settled.is_set()
            assert not client.closed
            assert room.client is client
            assert not turn_release.done()
            assert adapter._turn_release["a"] is turn_release
            with pytest.raises(ValueError):
                adapter._workspaces.claim("b", room.workspace)
            outcome = await asyncio.gather(cleanup, return_exceptions=True)
            assert isinstance(outcome[0], CleanupIncompleteError)
            release.set()
            await asyncio.gather(running, return_exceptions=True)
            await adapter.on_cleanup("a")
            assert settled.is_set()
            assert client.closed
        finally:
            release.set()
            await asyncio.gather(running, return_exceptions=True)
            if cleanup is not None:
                await asyncio.gather(cleanup, return_exceptions=True)
            await adapter.cleanup_all()
            adapter._workspaces.release("b")

    @pytest.mark.looptime
    async def test_thread_error_after_cleanup_issues_no_discovery(
        self, tmp_path
    ) -> None:
        entered, cancelling, release = (asyncio.Event() for _ in range(3))

        class LateThreadError(StatusClient):
            async def request(
                self, method: str, params: dict[str, Any] | None = None, **kwargs: Any
            ) -> dict[str, Any]:
                if method == CodexRequestMethod.THREAD_START:
                    self.requests.append((method, params or {}))
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        cancelling.set()
                        await release.wait()
                        raise CodexJsonRpcError(code=-32603, message="thread failed")
                return await super().request(method, params, **kwargs)

        client = LateThreadError()
        adapter, _ = mcp_adapter(
            tmp_path, [client], private_servers, turn_settle_timeout_s=0.02
        )
        await adapter.on_started("Fixture", "Retirement discovery fence")
        running = message_task(adapter)
        cleanup = None
        try:
            await entered.wait()
            initial_discovery = client.request_methods.count("mcpServerStatus/list")
            assert initial_discovery == 1
            cleanup = asyncio.create_task(adapter.on_cleanup("a"))
            await cancelling.wait()
            release.set()
            await asyncio.gather(running, cleanup, return_exceptions=True)
            assert (
                client.request_methods.count("mcpServerStatus/list")
                == initial_discovery
            )
            await adapter.on_cleanup("a")
            assert client.closed
        finally:
            release.set()
            await asyncio.gather(running, return_exceptions=True)
            if cleanup is not None:
                await asyncio.gather(cleanup, return_exceptions=True)
            await adapter.cleanup_all()
