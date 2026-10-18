from __future__ import annotations

import json
import logging
import traceback
from contextlib import nullcontext
from http import HTTPStatus
from urllib.parse import quote

import pytest
from phoenix_channels_python_client.exceptions import PHXConnectionError
from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.http11 import Request, Response

from band.client.streaming.client import WebSocketClient
from band.client.streaming.errors import WebSocketUpgradeError
from band.testing import fake_phoenix_server
from band.testing.phoenix_server import Upgrade


class TestTransportDiagnosticSecurity:
    @pytest.mark.parametrize("status", [400, 409, 429, 503])
    @pytest.mark.parametrize("field", ["message", "code", "request_id"])
    async def test_refused_upgrade_and_probe_sanitize_each_error_field(
        self, caplog: pytest.LogCaptureFixture, status: int, field: str
    ) -> None:
        secret = "synthetic/private+ credential?x=1&z=#[]"
        encoded = quote(secret, safe="")
        mixed = encoded.replace("%2F", "%2f")
        uri_encoded = quote(secret, safe=";/?:@&=+$,#-_.!~*'()")
        upgrades = []
        error = {
            "code": "connection_conflict",
            "message": "refused",
            "request_id": "safe-request",
            "retry_after": 7,
        }
        error[field] = (
            secret
            + " "
            + mixed
            + " "
            + uri_encoded
            + " authorization: Bearer unconfigured-bearer-value"
        )

        def refuse(connection: ServerConnection, request: Request) -> Response:
            upgrades.append(Upgrade.from_request(request))
            return connection.respond(HTTPStatus(status), json.dumps({"error": error}))

        async def peer(connection: ServerConnection) -> None:
            await connection.wait_closed()

        caplog.set_level(logging.DEBUG, logger="phoenix_channels_python_client.client")
        async with serve(peer, "127.0.0.1", 0, process_request=refuse) as server:
            port = server.sockets[0].getsockname()[1]
            client = WebSocketClient(
                f"ws://127.0.0.1:{port}/socket", secret, "agent-synthetic"
            )
            client.client = client._build_phx_client()
            with pytest.raises(PHXConnectionError) as initial:
                await client.client.__aenter__()
            classified = await client._classify_connect_failure(initial.value)
            assert classified is not None
            assert classified.status_code == status
            assert classified.retry_after == 7
            for diagnostic in (
                classified.message,
                classified.code,
                classified.request_id,
                client._cached_connect_failure,
                client._probed_failure_message,
                "".join(traceback.format_exception(initial.value)),
            ):
                assert secret not in str(diagnostic)
                assert encoded not in str(diagnostic)
                assert mixed not in str(diagnostic)
                assert uri_encoded not in str(diagnostic)
                assert "unconfigured-bearer-value" not in str(diagnostic)
        assert len(upgrades) == 2
        for upgrade in upgrades:
            assert "api_key" not in upgrade.query
            assert upgrade.headers["x-api-key"] == secret
        records = [
            record
            for record in caplog.records
            if record.name == "phoenix_channels_python_client.client"
        ]
        assert records
        for record in records:
            assert record.args == ()
            assert record.exc_info is None
            assert not hasattr(record, "websocket")
            assert secret not in repr(record.__dict__)
            assert encoded not in repr(record.__dict__)
            assert mixed not in repr(record.__dict__)
            assert uri_encoded not in repr(record.__dict__)
            assert "unconfigured-bearer-value" not in repr(record.__dict__)

    @pytest.mark.parametrize("field", ["message", "code", "request_id"])
    async def test_terminal_error_and_consumer_traceback_are_safe(
        self, field: str
    ) -> None:
        secret = "synthetic-terminal-credential"
        error = {
            "code": "connection_conflict",
            "message": "refused",
            "request_id": "safe-request",
        }
        error[field] = secret

        def refuse(connection: ServerConnection, request: Request) -> Response:
            return connection.respond(HTTPStatus.CONFLICT, json.dumps({"error": error}))

        async def peer(connection: ServerConnection) -> None:
            await connection.wait_closed()

        async with serve(peer, "127.0.0.1", 0, process_request=refuse) as server:
            port = server.sockets[0].getsockname()[1]
            client = WebSocketClient(
                f"ws://127.0.0.1:{port}/socket", secret, "agent-synthetic"
            )
            with pytest.raises(WebSocketUpgradeError) as terminal:
                await client.__aenter__()
            assert secret not in "".join(traceback.format_exception(terminal.value))
            assert secret not in repr(client.last_disconnect_reason)

    async def test_trusted_proxy_replaces_header_without_credential_query(self) -> None:
        sentinel = "proxy-managed-public-sentinel"
        injected = "synthetic-proxy-owned-credential"
        incoming: list[Upgrade] = []
        forwarded: list[Upgrade] = []

        def authenticate(
            connection: ServerConnection, request: Request
        ) -> Response | None:
            upgrade = Upgrade.from_request(request)
            forwarded.append(upgrade)
            if upgrade.headers.get("x-api-key") != injected:
                return connection.respond(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return None

        async def upstream(connection: ServerConnection) -> None:
            await connection.wait_closed()

        async with serve(
            upstream, "127.0.0.1", 0, process_request=authenticate
        ) as trusted:
            upstream_port = trusted.sockets[0].getsockname()[1]

            async def proxy(connection: ServerConnection) -> None:
                assert connection.request is not None
                incoming.append(Upgrade.from_request(connection.request))
                assert connection.request.headers["x-api-key"] == sentinel
                async with connect(
                    f"ws://127.0.0.1:{upstream_port}" + connection.request.path,
                    additional_headers={"x-api-key": injected},
                ):
                    await connection.wait_closed()

            async with serve(proxy, "127.0.0.1", 0) as server:
                proxy_port = server.sockets[0].getsockname()[1]
                async with WebSocketClient(
                    f"ws://127.0.0.1:{proxy_port}/socket", sentinel, "agent-synthetic"
                ):
                    pass
        assert len(incoming) == len(forwarded) == 1
        assert "api_key" not in incoming[0].query
        assert "api_key" not in forwarded[0].query
        assert forwarded[0].headers["x-api-key"] == injected

    @pytest.mark.parametrize("refuse", [False, True])
    async def test_agent_identity_remains_one_opaque_query_value(
        self, refuse: bool
    ) -> None:
        identity = "agent&api_key=untrusted-value&token=untrusted-token?#"
        async with fake_phoenix_server(refuse_upgrades=refuse) as server:
            expected_error = (
                pytest.raises(WebSocketUpgradeError) if refuse else nullcontext()
            )
            with expected_error:
                async with WebSocketClient(
                    server.url, "synthetic-header-key", identity
                ):
                    pass
        assert len(server.upgrades) == (2 if refuse else 1)
        for upgrade in server.upgrades:
            assert upgrade.query == {"vsn": "2.0.0", "agent_id": identity}
            assert upgrade.headers["x-api-key"] == "synthetic-header-key"
