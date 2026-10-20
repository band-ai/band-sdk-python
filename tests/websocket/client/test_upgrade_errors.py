"""WebSocket upgrade refusals parse into WebSocketUpgradeError."""

from __future__ import annotations

import pytest

from band.client.streaming import WebSocketClient, WebSocketUpgradeError
from band.client.streaming.errors import CONNECTION_CONFLICT_CODE
from tests.websocket.client.kit import connection_conflict_error_body, upgrade_exception


class TestUpgradeErrors:
    def test_parses_distinct_upgrade_errors_from_http_json_response(self):
        cases = [
            (
                409,
                connection_conflict_error_body(),
                CONNECTION_CONFLICT_CODE,
                None,
            ),
            (
                400,
                b'{"error":{"code":"invalid_on_conflict","message":"bad on_conflict","request_id":"req-400"}}',
                "invalid_on_conflict",
                None,
            ),
            (
                503,
                b'{"error":{"code":"tracking_failed","message":"tracking unavailable","request_id":"req-503"}}',
                "tracking_failed",
                None,
            ),
            (
                429,
                b'{"error":{"code":"too_many_requests","message":"slow down","request_id":"req-429","retry_after":12}}',
                "too_many_requests",
                12,
            ),
        ]

        for status_code, body, code, retry_after in cases:
            err = WebSocketUpgradeError.from_exception(
                upgrade_exception(status_code, body, {"Retry-After": "30"})
            )

            assert err is not None
            assert err.status_code == status_code
            assert err.code == code
            assert err.request_id == f"req-{status_code}"
            assert err.retry_after == retry_after

    def test_uses_retry_after_header_for_429_upgrade_error(self):
        err = WebSocketUpgradeError.from_exception(
            upgrade_exception(
                429,
                b'{"error":{"code":"too_many_requests","message":"slow down","request_id":"req-header"}}',
                {"Retry-After": "30"},
            )
        )

        assert err is not None
        assert err.retry_after == 30

    def test_ignores_generic_auth_upgrade_error_without_json_contract(self):
        err = WebSocketUpgradeError.from_exception(upgrade_exception(403, b""))

        assert err is None

    async def test_aenter_wraps_upgrade_error(self, scripted_connect):
        upgrade_exc = upgrade_exception(
            409,
            connection_conflict_error_body(),
        )

        scripted_connect(upgrade_exc)

        client = WebSocketClient("ws://localhost", "test-key", "agent-123")

        with pytest.raises(WebSocketUpgradeError) as exc_info:
            await client.__aenter__()

        assert exc_info.value.status_code == 409
        assert exc_info.value.code == CONNECTION_CONFLICT_CODE
        assert exc_info.value.request_id == "req-409"
        # Raw upgrade responses must not reappear in consumer tracebacks.
        assert exc_info.value.__cause__ is None
        assert exc_info.value.__suppress_context__

    async def test_aenter_reraises_unrecognized_upgrade_error(self, scripted_connect):
        original_exc = RuntimeError("socket exploded")

        scripted_connect(original_exc)

        client = WebSocketClient("ws://localhost", "test-key", "agent-123")

        with pytest.raises(RuntimeError, match="socket exploded"):
            await client.__aenter__()
