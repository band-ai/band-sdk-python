from __future__ import annotations

import asyncio
import json
import logging
import traceback
from contextlib import nullcontext
from http import HTTPStatus
from urllib.parse import quote

import pytest
from websockets.asyncio.server import ServerConnection, serve
from websockets.http11 import Request, Response

from band.client.streaming.client import WebSocketClient
from band.client.streaming.errors import WebSocketUpgradeError
from band.client.streaming.session_decider import logger as decider_logger
from band.testing import fake_phoenix_server
from tests.websocket.client.credentials import Credential, credentials_sent

SECRET = "synthetic/private+ credential?x=1&z=#[]"

# The hostile refusal hints a retry this far out; the boot's bound expires
# before it, so exactly one upgrade is attempted.
HOSTILE_RETRY_AFTER_S = 7
BEFORE_THE_HINTED_RETRY_S = 1.0


def credential_spellings() -> tuple[str, ...]:
    """The credential raw, percent-encoded, mixed-case encoded and URI-encoded."""
    encoded = quote(SECRET, safe="")
    return (
        SECRET,
        encoded,
        encoded.replace("%2F", "%2f"),
        quote(SECRET, safe=";/?:@&=+$,#-_.!~*'()"),
    )


def client_log(caplog: pytest.LogCaptureFixture) -> str:
    """What the SDK and its transport logged; the fake server's own logs are
    the platform's side."""
    return "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name.startswith(CLIENT_LOGGERS)
    )


CLIENT_LOGGERS = ("band.", "phoenix_channels_python_client")


class TestTransportDiagnosticSecurity:
    async def test_connection_and_reconnect_keep_headers_and_logs_private(
        self, caplog: pytest.LogCaptureFixture, credential: Credential
    ) -> None:
        secret = "synthetic-reconnect-credential"
        personal_data = "synthetic-person@example.invalid"
        reconnected = asyncio.Event()

        async def on_reconnect() -> None:
            reconnected.set()

        caplog.set_level(logging.DEBUG, logger="phoenix_channels_python_client.client")
        async with (
            fake_phoenix_server() as server,
            credential.client(server.url, secret, on_reconnect=on_reconnect),
        ):
            await server.close_connection(code=1012, reason=personal_data)
            await asyncio.wait_for(reconnected.wait(), timeout=10)

        assert len(server.upgrades) == 2
        assert [
            (credentials_sent(upgrade), upgrade.query) for upgrade in server.upgrades
        ] == [({credential.header: secret}, credential.query)] * 2
        records = [
            record
            for record in caplog.records
            if record.name == "phoenix_channels_python_client.client"
        ]
        assert records
        assert all(
            secret not in repr(record.__dict__)
            and personal_data not in repr(record.__dict__)
            for record in records
        )

    @staticmethod
    def hostile_refusal(field: str) -> str:
        """A refusal body whose ``field`` echoes the credential in every
        encoding a log or traceback could leak it in."""
        error: dict[str, object] = {
            "code": "connection_conflict",
            "message": "refused",
            "request_id": "safe-request",
            "retry_after": HOSTILE_RETRY_AFTER_S,
        }
        error[field] = " ".join(
            [*credential_spellings(), "authorization: Bearer unconfigured-bearer-value"]
        )
        return json.dumps({"error": error})

    @staticmethod
    def assert_private(text: str) -> None:
        for leaked in (*credential_spellings(), "unconfigured-bearer-value"):
            assert leaked not in text

    @pytest.mark.parametrize("status", [HTTPStatus.BAD_REQUEST, HTTPStatus.CONFLICT])
    @pytest.mark.parametrize("field", ["message", "code", "request_id"])
    async def test_a_terminal_refusal_reports_only_redacted_fields(
        self,
        caplog: pytest.LogCaptureFixture,
        credential: Credential,
        status: HTTPStatus,
        field: str,
    ) -> None:
        caplog.set_level(logging.DEBUG)
        async with fake_phoenix_server() as server:
            client = credential.client(server.url, SECRET)
            with (
                server.refusing_upgrades(status, body=self.hostile_refusal(field)),
                pytest.raises(WebSocketUpgradeError) as refused,
            ):
                await client.__aenter__()

        assert (refused.value.status_code, refused.value.retry_after) == (
            status,
            HOSTILE_RETRY_AFTER_S,
        )
        assert refused.value.__cause__ is None
        assert refused.value.__suppress_context__
        self.assert_private("".join(traceback.format_exception(refused.value)))
        self.assert_private(repr(client.last_disconnect_reason))
        self.assert_private(client_log(caplog))
        assert [
            (credentials_sent(upgrade), upgrade.query) for upgrade in server.upgrades
        ] == [({credential.header: SECRET}, credential.query)]

    @pytest.mark.looptime
    @pytest.mark.parametrize(
        "status", [HTTPStatus.TOO_MANY_REQUESTS, HTTPStatus.SERVICE_UNAVAILABLE]
    )
    @pytest.mark.parametrize("field", ["message", "code", "request_id"])
    async def test_a_retried_refusal_logs_only_redacted_fields(
        self,
        caplog: pytest.LogCaptureFixture,
        credential: Credential,
        status: HTTPStatus,
        field: str,
    ) -> None:
        caplog.set_level(logging.DEBUG)
        async with fake_phoenix_server() as server:
            client = credential.client(
                server.url, SECRET, connect_timeout=BEFORE_THE_HINTED_RETRY_S
            )
            with (
                server.refusing_upgrades(status, body=self.hostile_refusal(field)),
                pytest.raises(TimeoutError),
            ):
                await client.__aenter__()

        [retry] = [
            record for record in caplog.records if record.name == decider_logger.name
        ]
        detail, _delay_s = retry.args
        assert retry.levelno == logging.WARNING
        assert str(status.value) in detail
        self.assert_private(client_log(caplog))
        assert client.last_disconnect_reason is None
        assert len(server.upgrades) == 1

    @pytest.mark.parametrize("field", ["message", "code", "request_id"])
    async def test_terminal_error_and_consumer_traceback_are_safe(
        self, credential: Credential, field: str
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
            client = credential.client(f"ws://127.0.0.1:{port}/socket", secret)
            with pytest.raises(WebSocketUpgradeError) as terminal:
                await client.__aenter__()
            assert secret not in "".join(traceback.format_exception(terminal.value))
            assert secret not in repr(client.last_disconnect_reason)

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
        assert len(server.upgrades) == 1
        for upgrade in server.upgrades:
            assert upgrade.query == {"vsn": "2.0.0", "agent_id": identity}
            assert upgrade.headers["x-api-key"] == "synthetic-header-key"
