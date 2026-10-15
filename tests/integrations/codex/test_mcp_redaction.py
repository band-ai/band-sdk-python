"""Secret-safe Codex transport diagnostics and forwarded RPC failures."""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from typing import Any

import pytest

from band.integrations.codex import CodexJsonRpcError, CodexStdioClient, stdio_client
from band.integrations.codex.mcp import CodexMcpHttpServer, freeze_mcp_servers
from band.integrations.codex.rpc_base import BaseJsonRpcClient

SECRET = 'sentinel-"quoted"-\\escaped-credential'
_SYNTHETIC_PGID = 123_456_789


@pytest.fixture
def exited_process_group(monkeypatch: pytest.MonkeyPatch) -> int:
    def group_ended(pgid: int, _signum: int) -> None:
        if pgid != _SYNTHETIC_PGID:
            raise AssertionError("redaction fixture cannot settle another group")
        raise ProcessLookupError("the synthetic owned group has exited")

    monkeypatch.setattr(stdio_client.os, "killpg", group_ended, raising=False)
    return _SYNTHETIC_PGID


def redactor() -> Any:
    return freeze_mcp_servers(
        {"private": CodexMcpHttpServer("http://localhost/mcp", bearer_token=SECRET)}
    ).redactor


class TestMcpRedaction:
    async def test_stderr_and_malformed_rpc_log_metadata_without_payloads(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.DEBUG, logger="band.integrations.codex.stdio_client")
        caplog.set_level(logging.DEBUG, logger="band.integrations.codex.rpc_base")
        client = CodexStdioClient(redactor=redactor())
        reader = asyncio.StreamReader()
        reader.feed_data(f"connection failed {SECRET}\n".encode())
        reader.feed_eof()
        client._proc = SimpleNamespace(stderr=reader)
        await client._read_stderr_loop()
        await client._dispatch_rpc_message("malformed " + SECRET)
        await client._dispatch_rpc_message(json.dumps([SECRET]))
        await client._dispatch_rpc_message(json.dumps({"unexpected": SECRET}))
        rendered = caplog.text
        assert SECRET not in rendered
        assert json.dumps(SECRET)[1:-1] not in rendered
        assert "connection failed" not in rendered
        assert "malformed" not in rendered
        assert "unexpected" not in rendered
        assert "type=str" in rendered
        assert "type=list" in rendered
        assert "type=dict" in rendered
        for payload in (
            "malformed " + SECRET,
            json.dumps([SECRET]),
            json.dumps({"unexpected": SECRET}),
        ):
            assert f"length={len(payload)}" in rendered

    @pytest.mark.parametrize("request_id", [None, 7])
    @pytest.mark.parametrize(
        "method",
        ["item/tool/call", "FirstServerGrant2026", {"token": SECRET}, 7],
    )
    async def test_full_rpc_queue_does_not_log_method_or_parameter_contents(
        self,
        caplog: pytest.LogCaptureFixture,
        request_id: int | None,
        method: Any,
    ) -> None:
        caplog.set_level(logging.WARNING, logger="band.integrations.codex.rpc_base")
        private = freeze_mcp_servers(
            {
                "private": CodexMcpHttpServer(
                    "http://localhost/mcp", bearer_token="FirstServerGrant2026"
                )
            }
        )
        client = CodexStdioClient(redactor=private.redactor)
        client._events = asyncio.Queue(maxsize=1)
        await client._dispatch_rpc_message(json.dumps({"method": "occupied"}))
        await client._dispatch_rpc_message(
            json.dumps(
                {"id": request_id, "method": method, "params": {"token": SECRET}}
            )
        )
        assert [record.levelno for record in caplog.records] == [logging.WARNING]
        rendered = caplog.text
        assert SECRET not in rendered
        assert "FirstServerGrant2026" not in rendered
        assert "token" not in rendered
        if isinstance(method, str):
            assert method not in rendered

    async def test_rpc_error_message_and_nested_data_are_redacted(
        self,
    ) -> None:
        class Peer(BaseJsonRpcClient):
            async def _send_json(self, payload: dict[str, Any]) -> None:
                await self._dispatch_rpc_message(
                    json.dumps(
                        {
                            "id": payload["id"],
                            "error": {
                                "code": -32000,
                                "message": "server denied " + SECRET,
                                "data": {
                                    SECRET: ["Bearer " + SECRET, {"cause": SECRET}]
                                },
                            },
                        }
                    )
                )

        peer = Peer(redactor=redactor())
        with pytest.raises(CodexJsonRpcError) as error:
            await peer.request("thread/start", {})
        assert error.value.code == -32000
        assert "server denied" in str(error.value)
        assert SECRET not in str(error.value)
        assert SECRET not in repr(error.value.data)
        assert json.dumps(SECRET)[1:-1] not in json.dumps(error.value.data)

    async def test_success_results_and_tool_arguments_are_not_redacted(
        self,
    ) -> None:
        class Peer(BaseJsonRpcClient):
            async def _send_json(self, payload: dict[str, Any]) -> None:
                assert payload["params"] == {"arguments": {"token": SECRET}}
                await self._dispatch_rpc_message(
                    json.dumps({"id": payload["id"], "result": {"content": [SECRET]}})
                )

        peer = Peer(redactor=redactor())
        result = await peer.request("tools/call", {"arguments": {"token": SECRET}})
        assert result == {"content": [SECRET]}
        await peer._dispatch_rpc_message(
            json.dumps(
                {
                    "id": "server-request",
                    "method": "item/tool/call",
                    "params": {"arguments": {"token": SECRET}},
                }
            )
        )
        event = await peer.recv_event(timeout_s=1)
        assert event.params == {"arguments": {"token": SECRET}}
        assert event.raw["params"] == {"arguments": {"token": SECRET}}

    async def test_rpc_error_delivered_before_close_is_safe_when_consumed_after(
        self,
        exited_process_group: int,
    ) -> None:
        scope = redactor()
        client = CodexStdioClient(redactor=scope)
        sent = asyncio.Event()
        consume = asyncio.Event()
        outgoing: list[dict[str, Any]] = []

        def write_request(data: bytes) -> None:
            outgoing.append(json.loads(data))
            sent.set()

        async def drain() -> None:
            await consume.wait()

        async def wait() -> None:
            return None

        client._proc = SimpleNamespace(
            pid=exited_process_group,
            stdin=SimpleNamespace(write=write_request, drain=drain, close=lambda: None),
            wait=wait,
        )
        request = asyncio.create_task(client.request("thread/start", {}))
        try:
            await asyncio.wait_for(sent.wait(), timeout=1)
            await client._dispatch_rpc_message(
                json.dumps(
                    {
                        "id": outgoing[0]["id"],
                        "error": {
                            "code": -32000,
                            "message": "server denied " + SECRET,
                            "data": {SECRET: ["Bearer " + SECRET, {"cause": SECRET}]},
                        },
                    }
                )
            )
            assert not request.done()
            await client.close()
            assert scope.text(SECRET) == SECRET
            assert not request.done()
            consume.set()
            with pytest.raises(CodexJsonRpcError) as error:
                await asyncio.wait_for(request, timeout=1)
            assert error.value.code == -32000
            assert error.value.message == "server denied [REDACTED]"
            assert error.value.data == {
                "[REDACTED]": ["Bearer [REDACTED]", {"cause": "[REDACTED]"}]
            }
            assert SECRET not in str(error.value)
            assert json.dumps(SECRET)[1:-1] not in json.dumps(error.value.data)
        finally:
            consume.set()
            await asyncio.gather(request, return_exceptions=True)
            await client.close()

    async def test_secret_scope_is_retained_until_physical_teardown(
        self,
        exited_process_group: int,
    ) -> None:
        scope = redactor()
        client = CodexStdioClient(env={"GENERATED": SECRET}, redactor=scope)
        exited = asyncio.Event()

        class Process:
            pid = exited_process_group
            stdin = None

            async def wait(self) -> None:
                await exited.wait()

        client._proc = Process()
        closing = asyncio.create_task(client.close())
        try:
            while client._close_task is None:
                await asyncio.sleep(0)
            assert scope.text(SECRET) != SECRET
            assert client.env is not None and client.env["GENERATED"] == SECRET
            closing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closing
            assert scope.text(SECRET) != SECRET
            exited.set()
            await client.close()
            assert client.env is None
            assert scope.text(SECRET) == SECRET
        finally:
            exited.set()
            await asyncio.gather(closing, return_exceptions=True)
            await client.close()
