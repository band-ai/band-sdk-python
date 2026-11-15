"""MCP HTTP JSON/SSE framing and exact endpoint/session credential lifecycle."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from bandpap.channels import MCPClient, MCPError
from bandpap.http import Response

from tests.pap.channelkit import connection


class TestMCPChannel:
    async def test_initialize_tools_and_close_bind_bearer_to_advertised_endpoint(
        self, tmp_path: Path
    ) -> None:
        oauth, transport, store = connection(tmp_path / "state.db")
        client = MCPClient(
            oauth, oauth.discovery.document.apis[1], allowed_tools=frozenset({"lookup"})
        )
        initialize = {
            "jsonrpc": "2.0",
            "id": "unused",
            "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}},
        }
        transport.responses.extend(
            [
                initialize["result"],
                Response(202),
                {"tools": [{"name": "lookup", "inputSchema": {"type": "object"}}]},
                {"content": [{"type": "text", "text": "Done"}], "isError": False},
            ]
        )
        await client.initialize()
        assert (await client.list_tools())["tools"][0]["name"] == "lookup"
        assert (await client.call_tool("lookup", {"query": "x"}))["isError"] is False
        await client.close()
        assert all(
            item["url"] == "https://mcp.company.example/endpoint"
            for item in transport.requests
        )
        assert all(
            item["headers"]["Authorization"]
            == "Bearer private-token-Bearer:https://mcp.company.example/endpoint"
            for item in transport.requests
        )
        assert all("DPoP" not in item["headers"] for item in transport.requests)
        assert transport.requests[0]["document"]["method"] == "initialize"
        assert (
            transport.requests[1]["document"]["method"] == "notifications/initialized"
        )
        assert transport.requests[1]["headers"]["MCP-Protocol-Version"] == "2025-06-18"
        assert transport.requests[-1]["document"]["method"] == "tools/call"
        assert not client.initialized
        store.close()

    async def test_session_headers_and_sse_response_are_handled(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        oauth, transport, store = connection(tmp_path / "state.db")
        client = MCPClient(
            oauth, oauth.discovery.document.apis[1], allowed_tools=frozenset({"lookup"})
        )
        monkeypatch.setattr(
            "bandpap.channels.uuid4", lambda: SimpleNamespace(hex="fixed-id")
        )
        transport.responses.extend(
            [
                Response(
                    200,
                    {
                        "mcp-session-id": "mcp-session",
                        "content-type": "application/json",
                    },
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": "fixed-id",
                            "result": {
                                "protocolVersion": "2025-06-18",
                                "capabilities": {"tools": {}},
                            },
                        }
                    ).encode(),
                ),
                Response(202),
                Response(
                    200,
                    {"content-type": "text/event-stream"},
                    b'event: message\r\ndata: {"jsonrpc":"2.0","method":"notifications/progress","params":{}}\r\n\r\nevent: message\r\ndata: {"jsonrpc":"2.0","id":"fixed-id",\r\ndata: "result":{"content":[],"isError":true}}\r\n\r\n',
                ),
                Response(405),
            ]
        )
        await client.initialize()
        result = await client.call_tool("lookup", {})
        assert result["isError"] is True
        assert transport.requests[1]["headers"]["Mcp-Session-Id"] == "mcp-session"
        assert transport.requests[2]["headers"]["Mcp-Session-Id"] == "mcp-session"
        await client.close()
        assert client.session_id is None
        store.close()

    async def test_tool_session_loss_does_not_reinitialize_or_repeat_effect(
        self, tmp_path: Path
    ) -> None:
        oauth, transport, store = connection(tmp_path / "state.db")
        client = MCPClient(
            oauth, oauth.discovery.document.apis[1], allowed_tools=frozenset({"lookup"})
        )
        transport.responses.extend(
            [
                {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}},
                Response(202),
            ]
        )
        await client.initialize()
        client.session_id = "session-old"
        transport.responses.append(Response(404))
        with pytest.raises(ValueError, match="session expired"):
            await client.call_tool("lookup", {})
        assert len(transport.requests) == 3
        assert not client.initialized
        with pytest.raises(ValueError, match="Initialize"):
            await client.call_tool("lookup", {})
        assert len(transport.requests) == 3
        store.close()

    async def test_unapproved_tool_and_timeout_never_retry(
        self, tmp_path: Path
    ) -> None:
        oauth, transport, store = connection(tmp_path / "state.db")
        client = MCPClient(
            oauth, oauth.discovery.document.apis[1], allowed_tools=frozenset({"lookup"})
        )
        transport.responses.extend(
            [
                {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}},
                Response(202),
                TimeoutError("Lost tool response"),
            ]
        )
        await client.initialize()
        with pytest.raises(PermissionError):
            await client.call_tool("purchase", {})
        with pytest.raises(TimeoutError):
            await client.call_tool("lookup", {})
        assert len(transport.requests) == 3
        store.close()

    async def test_negotiation_and_jsonrpc_errors_are_explicit(
        self, tmp_path: Path
    ) -> None:
        oauth, transport, store = connection(tmp_path / "state.db")
        client = MCPClient(
            oauth, oauth.discovery.document.apis[1], allowed_tools=frozenset()
        )
        transport.responses.append(
            {"protocolVersion": "2024-11-05", "capabilities": {}}
        )
        with pytest.raises(ValueError, match="Unsupported negotiated"):
            await client.initialize()
        with pytest.raises(MCPError) as error:
            MCPClient._result(
                Response(
                    200,
                    body=b'{"jsonrpc":"2.0","id":"one","error":{"code":-32601,"message":"untrusted description"}}',
                ),
                "one",
            )
        assert error.value.code == -32601
        assert "untrusted description" not in str(error.value)
        store.close()
