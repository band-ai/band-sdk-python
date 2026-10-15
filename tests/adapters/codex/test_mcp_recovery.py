"""Recovery, settlement and deadline regressions at the actual Codex seams."""

from __future__ import annotations

import asyncio
import tomllib
from collections.abc import Mapping
from typing import Any

import pytest

from band.core.retention import Residency
from band.integrations.codex import CodexJsonRpcError
from band.integrations.codex.mcp import (
    CodexMcpHttpServer,
    CodexMcpLaunch,
    CodexMcpServer,
    CodexMcpStartupError,
)
from band.integrations.codex.types import CodexRequestMethod, CodexSessionState
from band.testing import FakeAgentTools
from tests.adapters.codex.kit import make_platform_message
from tests.adapters.codex.mcpkit import (
    StatusClient,
    mcp_adapter,
    private_servers,
    ready_row,
    start,
)
from tests.adapters.codexturns import tool_call_request, turn_completed


class TestMcpRecovery:
    @pytest.mark.parametrize(
        "method", [CodexRequestMethod.THREAD_START, CodexRequestMethod.THREAD_RESUME]
    )
    async def test_thread_failure_next_turn_sends_rotated_grant(
        self, tmp_path, method
    ) -> None:
        grant = {"value": "grant-A"}
        receipts: list[tuple[str, int]] = []

        async def endpoint(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                request = await reader.readuntil(b"\r\n\r\n")
                headers = dict(
                    line.split(b": ", 1)
                    for line in request.split(b"\r\n")[1:]
                    if b": " in line
                )
                supplied = (
                    headers.get(b"Authorization", b"").decode().removeprefix("Bearer ")
                )
                status = 200 if supplied == grant["value"] else 401
                receipts.append((supplied, status))
                writer.write(
                    f"HTTP/1.1 {status} Fixture\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode()
                )
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(endpoint, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        resolved: list[str] = []
        clients: list[StatusClient] = []

        async def resolve(_workspace: str) -> Mapping[str, CodexMcpServer]:
            resolved.append(grant["value"])
            return {
                "private": CodexMcpHttpServer(
                    f"http://127.0.0.1:{port}/mcp", bearer_token=grant["value"]
                )
            }

        class EndpointClient(StatusClient):
            def __init__(self, token: str) -> None:
                super().__init__()
                self.token = token
                self._events.extend(
                    (
                        tool_call_request(
                            1,
                            "band_send_message",
                            {"content": "recovered", "mentions": ["user"]},
                        ),
                        turn_completed(),
                    )
                )

            async def authorized(self) -> bool:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                try:
                    writer.write(
                        f"GET /mcp HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer {self.token}\r\n\r\n".encode()
                    )
                    await writer.drain()
                    status = await reader.readline()
                    await reader.read()
                    return status.split()[1] == b"200"
                finally:
                    writer.close()
                    await writer.wait_closed()

            async def request(
                self, sent: str, params: dict[str, Any] | None = None, **kwargs: Any
            ) -> dict[str, Any]:
                if sent == "mcpServerStatus/list":
                    self.requests.append((sent, params or {}))
                    row = ready_row()
                    if not await self.authorized():
                        row.update(
                            serverInfo=None,
                            serverCapabilities=None,
                            runtimeStatus="authenticationRequired",
                        )
                    return {"data": [row]}
                if sent == method:
                    grant["value"] = "grant-B"
                    if not await self.authorized():
                        self.requests.append((sent, params or {}))
                        raise CodexJsonRpcError(
                            code=-32603, message="Required MCP connection failed"
                        )
                return await super().request(sent, params, **kwargs)

        adapter, _ = mcp_adapter(tmp_path, [], resolve, model="fixture")

        def build(_room: object, launch: CodexMcpLaunch | None) -> EndpointClient:
            assert launch is not None
            overrides = [
                launch.command[index + 1]
                for index, item in enumerate(launch.command)
                if item == "-c"
            ]
            settings = tomllib.loads("\n".join(overrides))["mcp_servers"]["private"]
            client = EndpointClient(launch.env[settings["bearer_token_env_var"]])
            clients.append(client)
            return client

        adapter._build_client = build
        tools = FakeAgentTools()
        history = (
            CodexSessionState(thread_id="saved")
            if method == CodexRequestMethod.THREAD_RESUME
            else CodexSessionState()
        )
        arguments = {
            "msg": make_platform_message(room_id="a"),
            "tools": tools,
            "history": history,
            "participants_msg": None,
            "contacts_msg": None,
            "is_session_bootstrap": True,
            "room_id": "a",
        }
        try:
            await adapter.on_started("Fixture", "Grant recovery fixture")
            with pytest.raises(CodexMcpStartupError):
                await adapter.on_message(**arguments)
            second_failure = None
            try:
                await adapter.on_message(**arguments)
            except CodexMcpStartupError as error:
                second_failure = error
            assert resolved == ["grant-A", "grant-B"]
            assert len(clients) == 2
            assert clients[0].closed
            assert second_failure is None
            assert ("grant-A", 401) in receipts
            assert receipts[-1] == ("grant-B", 200)
        finally:
            await adapter.cleanup_all()
            server.close()
            await server.wait_closed()

    @pytest.mark.parametrize("public_turn", [False, True])
    async def test_cleanup_joins_already_cancelling_resolver(
        self, tmp_path, public_turn: bool
    ) -> None:
        entered, settling, release, settled = (asyncio.Event() for _ in range(4))

        async def resolve(workspace: str) -> Mapping[str, CodexMcpServer]:
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                settling.set()
                await release.wait()
                settled.set()
                raise

        adapter, launches = mcp_adapter(
            tmp_path, [StatusClient()], resolve, turn_settle_timeout_s=2
        )
        await adapter.on_started("Fixture", "Cancellation settlement fixture")
        if public_turn:
            running = asyncio.create_task(
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
        else:
            running = asyncio.create_task(start(adapter))
        cleanup = None
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
            room = adapter._room_clients["a"]
            invocation = room.startup_owner.task
            assert invocation is not None
            running.cancel()
            await asyncio.wait_for(settling.wait(), timeout=1)
            cleanup = asyncio.create_task(adapter.on_cleanup("a"))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert invocation.cancelling() == 1
            assert not cleanup.done()
            assert not settled.is_set()
            assert not (room.startup_owner is None)
            with pytest.raises(ValueError):
                adapter._workspaces.claim("b", room.workspace)
            release.set()
            await asyncio.gather(running, return_exceptions=True)
            await asyncio.wait_for(cleanup, timeout=1)
            assert settled.is_set()
            assert launches == []
            assert "a" not in adapter._room_clients
            assert "a" not in adapter._workspaces.rooms
        finally:
            release.set()
            await asyncio.gather(running, return_exceptions=True)
            if cleanup is not None:
                await asyncio.gather(cleanup, return_exceptions=True)
            await adapter.cleanup_all()
            adapter._workspaces.release("b")

    async def test_resume_failure_without_mcp_word_checks_readiness_before_fallback(
        self,
        tmp_path,
    ) -> None:
        class LostRequiredServer(StatusClient):
            failed = False

            async def request(
                self, sent: str, params: dict[str, Any] | None = None, **kwargs: Any
            ) -> dict[str, Any]:
                if sent == CodexRequestMethod.THREAD_RESUME:
                    self.failed = True
                    self.requests.append((sent, params or {}))
                    raise CodexJsonRpcError(
                        code=-32603,
                        message="error resuming thread: connection failed",
                        data=None,
                    )
                if sent == "mcpServerStatus/list":
                    self.requests.append((sent, params or {}))
                    return {
                        "data": [
                            {**ready_row(), "runtimeStatus": "failed"}
                            if self.failed
                            else ready_row()
                        ]
                    }
                return await super().request(sent, params, **kwargs)

        client = LostRequiredServer()
        adapter, _ = mcp_adapter(tmp_path, [client], private_servers)
        try:
            await start(adapter)
            with pytest.raises(CodexMcpStartupError) as error:
                await adapter._ensure_thread(
                    room=adapter._room_clients["a"],
                    history=CodexSessionState(thread_id="saved"),
                    tools=FakeAgentTools(),
                    is_session_bootstrap=True,
                )
            assert error.value.stage == "thread/resume"
            assert CodexRequestMethod.THREAD_START not in client.request_methods
            assert "a" not in adapter._needs_history_injection
            assert adapter._room_clients["a"].retained.snapshot.pending_cleanup
        finally:
            await adapter.cleanup_all()

    @pytest.mark.parametrize(
        "method", [CodexRequestMethod.THREAD_START, CodexRequestMethod.THREAD_RESUME]
    )
    async def test_unrelated_error_mentioning_mcp_is_not_startup_failure(
        self, tmp_path, method
    ) -> None:
        class ModelFailure(StatusClient):
            async def request(
                self, sent: str, params: dict[str, Any] | None = None, **kwargs: Any
            ) -> dict[str, Any]:
                if sent == method:
                    self.requests.append((sent, params or {}))
                    raise CodexJsonRpcError(
                        code=-32603,
                        message="MCP was mentioned in an unrelated model validation error",
                    )
                if sent == "mcpServerStatus/list":
                    self.requests.append((sent, params or {}))
                    return {"data": [ready_row()]}
                return await super().request(sent, params, **kwargs)

        client = ModelFailure()
        adapter, _ = mcp_adapter(tmp_path, [client], private_servers)
        try:
            await start(adapter)
            kwargs = {
                "room": adapter._room_clients["a"],
                "history": CodexSessionState(thread_id="saved")
                if method == CodexRequestMethod.THREAD_RESUME
                else CodexSessionState(),
                "tools": FakeAgentTools(),
                "is_session_bootstrap": True,
            }
            if method == CodexRequestMethod.THREAD_RESUME:
                assert await adapter._ensure_thread(**kwargs) == "thr-1"
                assert "a" in adapter._needs_history_injection
            else:
                with pytest.raises(CodexJsonRpcError):
                    await adapter._ensure_thread(**kwargs)
            assert not adapter._room_clients["a"].retained.snapshot.pending_cleanup
        finally:
            await adapter.cleanup_all()

    @pytest.mark.looptime
    async def test_consumed_readiness_timeout_cannot_publish_initialized(
        self, tmp_path
    ) -> None:
        class ConsumingClient(StatusClient):
            async def request(
                self, sent: str, params: dict[str, Any] | None = None, **kwargs: Any
            ) -> dict[str, Any]:
                if sent == "mcpServerStatus/list":
                    self.requests.append((sent, params or {}))
                    try:
                        await asyncio.sleep(31)
                    except asyncio.CancelledError:
                        task = asyncio.current_task()
                        assert task is not None
                        task.uncancel()
                    return {"data": [ready_row()]}
                return await super().request(sent, params, **kwargs)

        client = ConsumingClient()
        adapter, _ = mcp_adapter(tmp_path, [client], private_servers)
        try:
            with pytest.raises(CodexMcpStartupError) as error:
                await start(adapter)
            assert error.value.stage == "readiness"
            assert (
                adapter._room_clients["a"].retained.snapshot.state != Residency.RESIDENT
            )
            assert client.closed
            assert adapter._room_threads == {}
        finally:
            await adapter.cleanup_all()
